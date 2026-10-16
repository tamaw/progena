"""
File Attachments Module - Upload and attach documents to messages.

Based on patterns from text-generation-webui for document-grounded chat.

Features:
- Upload text files, PDFs, and documents
- Automatic content extraction
- Token-based content truncation
- Attachment metadata tracking
- Support for URL-based attachments
- Parallel file processing
"""

import asyncio
import base64
import contextlib
import hashlib
import io
import ipaddress
import logging
import mimetypes
import socket
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import aiofiles
import aiohttp
from aiohttp.abc import AbstractResolver, ResolveResult
from pydantic import BaseModel, Field

from .attachment_blob_store import (
    AttachmentBlob,
    AttachmentBlobMeta,
    AttachmentBlobStore,
    get_attachment_blob_store,
)

logger = logging.getLogger(__name__)

# Maximum file sizes
MAX_FILE_SIZE_MB = 10
MAX_CONTENT_TOKENS = 8192
ALLOWED_URL_SCHEMES = frozenset({"http", "https"})
MAX_URL_REDIRECTS = 5
REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})


class _PinnedResolver(AbstractResolver):
    """Resolve hostnames to a prevalidated set of IPs for a single request chain."""

    def __init__(self, hosts: dict[str, list[str]]) -> None:
        self._hosts = {host: list(addresses) for host, addresses in hosts.items()}

    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: socket.AddressFamily = socket.AF_UNSPEC,
    ) -> list[ResolveResult]:
        del family
        addresses = self._hosts.get(host)
        if not addresses:
            raise OSError(f"Host {host!r} was not prevalidated for attachment download")

        results: list[ResolveResult] = []
        for address in addresses:
            ip = ipaddress.ip_address(address)
            results.append(
                {
                    "hostname": host,
                    "host": address,
                    "port": port,
                    "family": socket.AF_INET6 if ip.version == 6 else socket.AF_INET,
                    "proto": 0,
                    "flags": socket.AI_NUMERICHOST,
                }
            )
        return results

    async def close(self) -> None:
        return None


class AttachmentConfig(BaseModel):
    """Configuration for attachment handling."""

    max_file_size_mb: float = MAX_FILE_SIZE_MB
    max_content_tokens: int = MAX_CONTENT_TOKENS
    allowed_extensions: list[str] = Field(
        default_factory=lambda: [
            ".txt",
            ".md",
            ".json",
            ".yaml",
            ".yml",
            ".xml",
            ".py",
            ".js",
            ".ts",
            ".html",
            ".css",
            ".pdf",
            ".docx",
            ".doc",
            ".csv",
            ".log",
        ]
    )
    cache_dir: Path | None = None
    extract_text_from_pdf: bool = True
    extract_text_from_docx: bool = True


class AttachmentResult(BaseModel):
    """Result of processing an attachment."""

    id: str = Field(default_factory=lambda: f"att_{uuid.uuid4().hex[:8]}")
    name: str
    original_filename: str
    mime_type: str
    size_bytes: int
    content: str  # Extracted text content
    content_truncated: bool = False
    original_tokens: int | None = None
    truncated_tokens: int | None = None
    checksum: str  # SHA256 of original file
    source_type: str = "upload"  # upload, url, paste
    source_url: str | None = None
    processed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class AttachmentManager:
    """
    Manages file attachments for conversations.

    Handles:
    - File uploads with validation
    - Text extraction from various formats
    - Content truncation for token limits
    - Caching of processed attachments
    """

    def __init__(
        self,
        config: AttachmentConfig | None = None,
        blob_store: AttachmentBlobStore | None = None,
    ):
        self.config = config or AttachmentConfig()
        self.blob_store = blob_store
        self._cache: dict[str, AttachmentResult] = {}
        self._executor = ThreadPoolExecutor(max_workers=4)

        # Initialize cache directory
        if self.config.cache_dir:
            self.config.cache_dir.mkdir(parents=True, exist_ok=True)

    def _is_blocked_address(self, address: str) -> bool:
        ip = ipaddress.ip_address(address)
        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        )

    async def _resolve_url_addresses(self, parsed_url: Any) -> list[str]:
        hostname = parsed_url.hostname
        if not hostname:
            raise ValueError("URL must include a hostname")

        with contextlib.suppress(ValueError):
            return [ipaddress.ip_address(hostname).compressed]

        port = parsed_url.port or (443 if parsed_url.scheme == "https" else 80)
        try:
            addrinfo = await asyncio.to_thread(
                socket.getaddrinfo,
                hostname,
                port,
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror as exc:
            raise ValueError(f"Could not resolve URL hostname {hostname!r}") from exc

        addresses: list[str] = []
        for *_, sockaddr in addrinfo:
            host = str(sockaddr[0])
            if host not in addresses:
                addresses.append(host)
        if not addresses:
            raise ValueError(f"Could not resolve URL hostname {hostname!r}")
        return addresses

    async def _resolve_public_url(self, url: str) -> tuple[Any, list[str]]:
        parsed = urlparse(url)
        scheme = str(parsed.scheme or "").lower()
        if scheme not in ALLOWED_URL_SCHEMES:
            raise ValueError("Only http:// and https:// attachment URLs are allowed")
        if not parsed.hostname:
            raise ValueError("URL must include a hostname")

        addresses = await self._resolve_url_addresses(parsed)
        blocked = [address for address in addresses if self._is_blocked_address(address)]
        if blocked:
            raise ValueError(f"URL resolves to a blocked address: {blocked[0]}")
        return parsed, addresses

    def _validate_file(
        self,
        filename: str,
        size_bytes: int,
    ) -> tuple[bool, str | None]:
        """Validate file before processing."""
        # Check extension
        ext = Path(filename).suffix.lower()
        if ext not in self.config.allowed_extensions:
            return (
                False,
                f"File type '{ext}' not allowed. Allowed: {', '.join(self.config.allowed_extensions)}",
            )

        # Check size
        max_bytes = self.config.max_file_size_mb * 1024 * 1024
        if size_bytes > max_bytes:
            return (
                False,
                f"File too large ({size_bytes / 1024 / 1024:.1f}MB). Max: {self.config.max_file_size_mb}MB",
            )

        return True, None

    def _calculate_checksum(self, content: bytes) -> str:
        """Calculate SHA256 checksum of content."""
        return hashlib.sha256(content).hexdigest()

    def _extract_text_from_pdf(self, content: bytes) -> str:
        """Extract text from PDF bytes."""
        try:
            import pypdf  # pyright: ignore[reportMissingImports]

            reader = pypdf.PdfReader(io.BytesIO(content))
            text_parts = []
            for page in reader.pages:
                text = page.extract_text()
                if text:
                    text_parts.append(text)
            return "\n\n".join(text_parts)
        except ImportError:
            logger.warning("pypdf not installed, cannot extract PDF text")
            return "[PDF content - pypdf not installed for extraction]"
        except Exception as e:
            logger.error("Error extracting PDF text: %s", e)
            return f"[Error extracting PDF text: {e}]"

    def _extract_text_from_docx(self, content: bytes) -> str:
        """Extract text from DOCX bytes."""
        try:
            import docx  # pyright: ignore[reportMissingImports]

            doc = docx.Document(io.BytesIO(content))
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            return "\n\n".join(paragraphs)
        except ImportError:
            logger.warning("python-docx not installed, cannot extract DOCX text")
            return "[DOCX content - python-docx not installed for extraction]"
        except Exception as e:
            logger.error("Error extracting DOCX text: %s", e)
            return f"[Error extracting DOCX text: {e}]"

    def _extract_text(
        self,
        content: bytes,
        filename: str,
        mime_type: str,
    ) -> str:
        """Extract text content from file bytes."""
        ext = Path(filename).suffix.lower()

        # PDF extraction
        if ext == ".pdf" or mime_type == "application/pdf":
            if self.config.extract_text_from_pdf:
                return self._extract_text_from_pdf(content)
            return "[PDF content]"

        # DOCX extraction
        if (
            ext in (".docx",)
            or mime_type
            == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ):
            if self.config.extract_text_from_docx:
                return self._extract_text_from_docx(content)
            return "[DOCX content]"

        # DOC (old format) - limited support
        if ext == ".doc":
            return "[DOC content - old format, limited extraction support]"

        # Try to decode as text
        try:
            # Try UTF-8 first
            return content.decode("utf-8")
        except UnicodeDecodeError:
            try:
                # Fall back to latin-1
                return content.decode("latin-1")
            except Exception:
                return "[Binary content - could not decode as text]"

    def _truncate_content(
        self,
        content: str,
        max_tokens: int,
    ) -> tuple[str, bool, int | None, int | None]:
        """
        Truncate content to fit token limit.

        Returns: (truncated_content, was_truncated, original_tokens, truncated_tokens)
        """
        # Simple character-based estimation (4 chars per token)
        estimated_tokens = len(content) // 4

        if estimated_tokens <= max_tokens:
            return content, False, estimated_tokens, estimated_tokens

        # Binary search for optimal truncation point
        max_chars = max_tokens * 4
        truncated = content[:max_chars]

        # Find a good break point (newline or sentence)
        for sep in ["\n\n", "\n", ". ", " "]:
            last_sep = truncated.rfind(sep)
            if last_sep > max_chars * 0.8:  # Keep at least 80%
                truncated = truncated[: last_sep + len(sep)]
                break

        truncated += "\n\n[... content truncated ...]"
        truncated_tokens = len(truncated) // 4

        return truncated, True, estimated_tokens, truncated_tokens

    async def process_file(
        self,
        filename: str,
        content: bytes,
        source_type: str = "upload",
        source_url: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AttachmentResult:
        """
        Process a file attachment.

        Args:
            filename: Original filename
            content: File content as bytes
            source_type: How the file was provided (upload, url, paste)
            source_url: URL if downloaded from web
            metadata: Additional metadata

        Returns:
            AttachmentResult with extracted content
        """
        # Validate
        valid, error = self._validate_file(filename, len(content))
        if not valid:
            return AttachmentResult(
                name=filename,
                original_filename=filename,
                mime_type="application/octet-stream",
                size_bytes=len(content),
                content="",
                checksum="",
                source_type=source_type,
                source_url=source_url,
                error=error,
            )

        # Calculate checksum
        checksum = self._calculate_checksum(content)

        # Check cache / existing blob (dedup by content checksum)
        if checksum in self._cache:
            logger.debug("Returning cached attachment: %s", checksum[:8])
            return self._cache[checksum]
        stored_meta = self._store().get_by_checksum(checksum)
        if stored_meta is not None:
            stored_result = self._result_from_meta(stored_meta)
            self._cache[checksum] = stored_result
            logger.debug("Returning stored attachment: %s", checksum[:8])
            return stored_result

        # Determine MIME type
        mime_type, _ = mimetypes.guess_type(filename)
        mime_type = mime_type or "application/octet-stream"

        # Extract text (run in executor for blocking operations)
        loop = asyncio.get_event_loop()
        text_content = await loop.run_in_executor(
            self._executor,
            self._extract_text,
            content,
            filename,
            mime_type,
        )

        # Truncate if needed (prompt-injection budget only -- the full
        # extracted text is persisted in the blob for the agent's direct
        # paged file reads via /attachments/).
        truncated_content, was_truncated, orig_tokens, trunc_tokens = self._truncate_content(
            text_content,
            self.config.max_content_tokens,
        )

        result = AttachmentResult(
            name=Path(filename).stem,
            original_filename=filename,
            mime_type=mime_type,
            size_bytes=len(content),
            content=truncated_content,
            content_truncated=was_truncated,
            original_tokens=orig_tokens,
            truncated_tokens=trunc_tokens,
            checksum=checksum,
            source_type=source_type,
            source_url=source_url,
            metadata=metadata or {},
        )

        # Persist the raw bytes + full extracted text as one blob row.
        self._store().create(
            id=result.id,
            filename=filename,
            mime_type=mime_type,
            data=content,
            checksum=checksum,
            text_content=text_content or None,
            extraction_error=None,
        )
        self._cache[checksum] = result

        logger.info(
            "Processed attachment: %s (%s, %d bytes, truncated=%s)",
            filename,
            mime_type,
            len(content),
            was_truncated,
        )

        return result

    async def process_file_path(
        self,
        file_path: Path,
        metadata: dict[str, Any] | None = None,
    ) -> AttachmentResult:
        """Process a file from disk."""
        async with aiofiles.open(file_path, "rb") as f:
            content = await f.read()
        return await self.process_file(
            filename=file_path.name,
            content=content,
            source_type="file",
            metadata=metadata,
        )

    async def process_url(
        self,
        url: str,
        metadata: dict[str, Any] | None = None,
    ) -> AttachmentResult:
        """Download and process a file from URL."""
        try:
            current_url = url
            max_bytes = int(self.config.max_file_size_mb * 1024 * 1024)
            for _ in range(MAX_URL_REDIRECTS + 1):
                parsed, addresses = await self._resolve_public_url(current_url)
                resolver = _PinnedResolver({str(parsed.hostname): addresses})
                connector = aiohttp.TCPConnector(resolver=resolver, use_dns_cache=False)
                timeout = aiohttp.ClientTimeout(total=30.0)

                async with (
                    aiohttp.ClientSession(timeout=timeout, connector=connector) as session,
                    session.get(current_url, allow_redirects=False) as response,
                ):
                    if response.status in REDIRECT_STATUS_CODES:
                        redirect_target = response.headers.get("location")
                        if not redirect_target:
                            raise ValueError("Redirect response missing Location header")
                        current_url = urljoin(current_url, redirect_target)
                        continue

                    response.raise_for_status()
                    content_length_header = response.headers.get("content-length")
                    if content_length_header:
                        content_length: int | None
                        try:
                            content_length = int(content_length_header)
                        except ValueError:
                            content_length = None
                        if content_length is not None and content_length > max_bytes:
                            raise ValueError(
                                f"Remote file too large ({content_length / 1024 / 1024:.1f}MB). "
                                f"Max: {self.config.max_file_size_mb}MB"
                            )

                    content = await response.content.read(max_bytes + 1)
                    if len(content) > max_bytes:
                        raise ValueError(
                            f"Remote file too large ({len(content) / 1024 / 1024:.1f}MB). "
                            f"Max: {self.config.max_file_size_mb}MB"
                        )
                    break
            else:
                raise ValueError("Too many redirects while fetching attachment URL")

            parsed = urlparse(current_url)
            filename = Path(parsed.path).name or "downloaded_file"

            resolved_metadata = dict(metadata or {})
            if current_url != url:
                resolved_metadata["final_url"] = current_url

            return await self.process_file(
                filename=filename,
                content=content,
                source_type="url",
                source_url=url,
                metadata=resolved_metadata,
            )

        except Exception as e:
            logger.error("Error downloading URL %s: %s", url, e)
            return AttachmentResult(
                name="download_failed",
                original_filename=url,
                mime_type="application/octet-stream",
                size_bytes=0,
                content="",
                checksum="",
                source_type="url",
                source_url=url,
                error=str(e),
            )

    async def process_base64(
        self,
        data: str,
        filename: str,
        metadata: dict[str, Any] | None = None,
    ) -> AttachmentResult:
        """Process base64-encoded file content."""
        try:
            # Handle data URL format
            if data.startswith("data:"):
                # Format: data:mime/type;base64,content
                header, b64_content = data.split(",", 1)
            else:
                b64_content = data

            content = base64.b64decode(b64_content)
            return await self.process_file(
                filename=filename,
                content=content,
                source_type="base64",
                metadata=metadata,
            )

        except Exception as e:
            logger.error("Error decoding base64: %s", e)
            return AttachmentResult(
                name=filename,
                original_filename=filename,
                mime_type="application/octet-stream",
                size_bytes=0,
                content="",
                checksum="",
                source_type="base64",
                error=str(e),
            )

    async def process_text_paste(
        self,
        text: str,
        name: str = "pasted_text",
        metadata: dict[str, Any] | None = None,
    ) -> AttachmentResult:
        """Process pasted text as an attachment."""
        content = text.encode("utf-8")
        return await self.process_file(
            filename=f"{name}.txt",
            content=content,
            source_type="paste",
            metadata=metadata,
        )

    async def process_multiple(
        self,
        files: list[tuple[str, bytes]],
    ) -> list[AttachmentResult]:
        """Process multiple files in parallel."""
        tasks = [self.process_file(filename, content) for filename, content in files]
        return await asyncio.gather(*tasks)

    def get_cached(self, checksum: str) -> AttachmentResult | None:
        """Get cached attachment by checksum."""
        cached = self._cache.get(checksum)
        if cached is not None:
            return cached
        meta = self._store().get_by_checksum(checksum)
        if meta is None:
            return None
        result = self._result_from_meta(meta)
        self._cache[checksum] = result
        return result

    def get_by_id(self, attachment_id: str) -> AttachmentResult | None:
        """Look up a processed attachment by its ``att_...`` id (not checksum)."""
        for attachment in self._cache.values():
            if attachment.id == attachment_id:
                return attachment
        blob = self._store().get(attachment_id)
        if blob is None:
            return None
        result = self._result_from_meta(blob)
        self._cache[result.checksum] = result
        return result

    def clear_cache(self) -> None:
        """Clear the in-process attachment cache (blob storage is untouched)."""
        self._cache.clear()

    def get_cache_stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        return {
            "entries": len(self._cache),
            "total_size_bytes": sum(a.size_bytes for a in self._cache.values()),
            "total_content_chars": sum(len(a.content) for a in self._cache.values()),
        }

    def _store(self) -> AttachmentBlobStore:
        return self.blob_store or get_attachment_blob_store()

    @staticmethod
    def _result_from_meta(meta: AttachmentBlobMeta | AttachmentBlob) -> AttachmentResult:
        """Rebuild an :class:`AttachmentResult` (prompt-facing view) from a blob row."""
        return AttachmentResult(
            id=meta.id,
            name=Path(meta.filename).stem,
            original_filename=meta.filename,
            mime_type=meta.mime_type,
            size_bytes=meta.size_bytes,
            content=meta.text_content or "",
            checksum=meta.checksum,
            source_type="upload",
            error=meta.extraction_error,
        )


# FastAPI integration helpers


def format_attachments_for_context(attachments: list[AttachmentResult]) -> str:
    """
    Build a compact, agent-only note listing this turn's attachments.

    Deliberately does NOT inline extracted content: the agent already has
    direct read access to it via the `/attachments/<filename>` virtual
    filesystem mount (see `agent/attachment_backend.py`) and the
    `convert_attachment` tool for binary formats that need markitdown, so
    duplicating potentially large extracted text into every turn's prompt
    would only burn tokens for content the agent can already fetch itself.
    This note just tells the agent *that* something was attached and
    *where* to find it.
    """
    if not attachments:
        return ""

    lines = [
        "Attached this turn (read via /attachments/<name>, or use the "
        "convert_attachment tool for binary formats):"
    ]
    for att in attachments:
        detail = f"error: {att.error}" if att.error else f"{att.size_bytes:,} bytes"
        lines.append(f"- {att.original_filename} ({att.mime_type}, {detail})")
    return "\n".join(lines)


async def create_attachment_from_upload(
    file_content: bytes,
    filename: str,
    content_type: str,
    manager: AttachmentManager | None = None,
) -> AttachmentResult:
    """Helper for FastAPI file upload handling."""
    mgr = manager or AttachmentManager()
    return await mgr.process_file(
        filename=filename,
        content=file_content,
        source_type="upload",
        metadata={"content_type": content_type},
    )

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
import hashlib
import io
import logging
import mimetypes
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiofiles
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Maximum file sizes
MAX_FILE_SIZE_MB = 10
MAX_CONTENT_TOKENS = 8192


class AttachmentConfig(BaseModel):
    """Configuration for attachment handling."""
    max_file_size_mb: float = MAX_FILE_SIZE_MB
    max_content_tokens: int = MAX_CONTENT_TOKENS
    allowed_extensions: list[str] = Field(default_factory=lambda: [
        ".txt", ".md", ".json", ".yaml", ".yml", ".xml",
        ".py", ".js", ".ts", ".html", ".css",
        ".pdf", ".docx", ".doc",
        ".csv", ".log",
    ])
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
    processed_at: datetime = Field(default_factory=datetime.utcnow)
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

    def __init__(self, config: AttachmentConfig | None = None):
        self.config = config or AttachmentConfig()
        self._cache: dict[str, AttachmentResult] = {}
        self._executor = ThreadPoolExecutor(max_workers=4)

        # Initialize cache directory
        if self.config.cache_dir:
            self.config.cache_dir.mkdir(parents=True, exist_ok=True)

    def _validate_file(
        self,
        filename: str,
        size_bytes: int,
    ) -> tuple[bool, str | None]:
        """Validate file before processing."""
        # Check extension
        ext = Path(filename).suffix.lower()
        if ext not in self.config.allowed_extensions:
            return False, f"File type '{ext}' not allowed. Allowed: {', '.join(self.config.allowed_extensions)}"

        # Check size
        max_bytes = self.config.max_file_size_mb * 1024 * 1024
        if size_bytes > max_bytes:
            return False, f"File too large ({size_bytes / 1024 / 1024:.1f}MB). Max: {self.config.max_file_size_mb}MB"

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
        if ext in (".docx",) or mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
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
                truncated = truncated[:last_sep + len(sep)]
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

        # Check cache
        if checksum in self._cache:
            logger.debug("Returning cached attachment: %s", checksum[:8])
            return self._cache[checksum]

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

        # Truncate if needed
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

        # Cache result
        self._cache[checksum] = result

        logger.info(
            "Processed attachment: %s (%s, %d bytes, truncated=%s)",
            filename, mime_type, len(content), was_truncated
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
            import httpx

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url)
                response.raise_for_status()
                content = response.content

            # Extract filename from URL
            parsed = urlparse(url)
            filename = Path(parsed.path).name or "downloaded_file"

            return await self.process_file(
                filename=filename,
                content=content,
                source_type="url",
                source_url=url,
                metadata=metadata,
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
        tasks = [
            self.process_file(filename, content)
            for filename, content in files
        ]
        return await asyncio.gather(*tasks)

    def get_cached(self, checksum: str) -> AttachmentResult | None:
        """Get cached attachment by checksum."""
        return self._cache.get(checksum)

    def clear_cache(self) -> None:
        """Clear the attachment cache."""
        self._cache.clear()

    def get_cache_stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        return {
            "entries": len(self._cache),
            "total_size_bytes": sum(a.size_bytes for a in self._cache.values()),
            "total_content_chars": sum(len(a.content) for a in self._cache.values()),
        }


# FastAPI integration helpers

def format_attachments_for_context(
    attachments: list[AttachmentResult],
    max_total_tokens: int = 16000,
) -> str:
    """
    Format attachments for inclusion in LLM context.

    Returns formatted string with all attachment contents.
    """
    if not attachments:
        return ""

    parts = ["## Attached Documents\n"]
    total_tokens = 0

    for att in attachments:
        if att.error:
            parts.append(f"### {att.name} (Error: {att.error})\n\n")
            continue

        header = f"### {att.name}"
        if att.source_url:
            header += f" (from: {att.source_url})"
        header += f"\n*Type: {att.mime_type}, Size: {att.size_bytes:,} bytes*\n\n"

        content_tokens = (att.truncated_tokens or 0)
        if total_tokens + content_tokens > max_total_tokens:
            parts.append(f"{header}[Content omitted - token limit reached]\n\n")
            continue

        parts.append(f"{header}{att.content}\n\n")
        total_tokens += content_tokens

    return "".join(parts)


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

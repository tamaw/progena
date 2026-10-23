"""Local OAuth and remote credential-import commands for ChatGPT plan providers."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from src.progena.settings.chatgpt_oauth import (
    ChatGPTOAuthError,
    authorize_with_browser,
    default_credential_path,
    default_host_id_path,
    load_or_create_host_id,
    load_protected_credential,
    write_protected_credential,
)
from src.progena.settings.providers import ProviderRepository, provider_database_url


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage a protected Sign in with ChatGPT credential"
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    sign_in = subcommands.add_parser("sign-in", help="complete browser OAuth on this computer")
    sign_in.add_argument("--credential-file", type=Path, default=default_credential_path())
    sign_in.add_argument("--host-id-file", type=Path, default=default_host_id_path())
    sign_in.add_argument("--agent-name", default="Progena")
    sign_in.add_argument("--timeout", type=float, default=300.0)
    sign_in.add_argument(
        "--reauthorize",
        action="store_true",
        help="reuse the registration in --credential-file after validating its identity",
    )
    sign_in.add_argument(
        "--enable-plan-usage",
        action="store_true",
        help="request consent again to enable ChatGPT plan usage after a prior decline",
    )

    host_id = subcommands.add_parser("host-id", help="create or show this host's stable opaque ID")
    host_id.add_argument("--host-id-file", type=Path, default=default_host_id_path())

    credential_import = subcommands.add_parser(
        "import", help="import an SSH-transferred credential into this server's encrypted database"
    )
    credential_import.add_argument("--credential-file", type=Path, required=True)
    credential_import.add_argument("--provider-id")
    credential_import.add_argument("--name")
    credential_import.add_argument(
        "--database-url",
        help="backend provider database URL; defaults to the database derived from PROGENA_DATABASE_URL",
    )

    disconnect = subcommands.add_parser("disconnect", help="revoke and clear a server registration")
    disconnect.add_argument("--provider-id", required=True)
    disconnect.add_argument("--database-url")
    return parser


def _database_url(value: str | None) -> str:
    database_url = (value or "").strip() or provider_database_url()
    if not database_url:
        raise ValueError("a backend database URL or PROGENA_DATABASE_URL is required")
    return database_url


def _open_repository(database_url: str) -> ProviderRepository:
    repository = ProviderRepository(database_url)
    repository.open()
    return repository


def _run_sign_in(args: argparse.Namespace) -> int:
    credential_file = args.credential_file.expanduser()
    if credential_file.exists() and not args.reauthorize:
        raise ValueError(
            "credential file already exists; use --reauthorize or a new --credential-file"
        )
    if args.enable_plan_usage and not args.reauthorize:
        raise ValueError("--enable-plan-usage requires --reauthorize")
    existing = load_protected_credential(credential_file) if args.reauthorize else None
    host_id = load_or_create_host_id(args.host_id_file)
    credential = authorize_with_browser(
        host_id=host_id,
        agent_name=args.agent_name,
        existing=existing,
        timeout_seconds=args.timeout,
        enable_plan_usage=args.enable_plan_usage,
    )
    write_protected_credential(credential_file, credential)
    print(f"Protected credential saved to {credential_file}")
    return 0


def _run_import(args: argparse.Namespace) -> int:
    credential = load_protected_credential(args.credential_file)
    repository = _open_repository(_database_url(args.database_url))
    try:
        provider = repository.import_chatgpt_credential(
            credential.as_mapping(), provider_id=args.provider_id, name=args.name
        )
    finally:
        repository.close()
    print(f"ChatGPT registration imported as provider {provider['id']}")
    return 0


def _run_disconnect(args: argparse.Namespace) -> int:
    repository = _open_repository(_database_url(args.database_url))
    try:
        repository.disconnect_chatgpt(args.provider_id)
    finally:
        repository.close()
    print("ChatGPT registration disconnected")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "sign-in":
            return _run_sign_in(args)
        if args.command == "host-id":
            print(load_or_create_host_id(args.host_id_file))
            return 0
        if args.command == "import":
            return _run_import(args)
        if args.command == "disconnect":
            return _run_disconnect(args)
        raise AssertionError(f"unknown command {args.command}")
    except (ChatGPTOAuthError, ConnectionError, LookupError, ValueError) as exc:
        print(f"ChatGPT OAuth command failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

"""Root pytest configuration.

A developer's local ``.env`` may point the ``PROGENITOR_*_DIR`` variables at
another checkout (a long-running ``main`` worktree). ``load_dotenv`` never
overrides variables that are already set, so pinning them here, before any
``app`` module is imported, makes the shipped schemas and path defaults under
test the ones in this checkout -- the same ones CI sees.
"""

import json
import os
import shutil
import socket
import subprocess
import time
from contextlib import suppress
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

_ROOT = Path(__file__).resolve().parents[1]

os.environ.update(
    PROGENITOR_DATA_DIR=str(_ROOT / "data"),
    PROGENITOR_STATE_DIR=str(_ROOT / "data" / "state"),
    PROGENITOR_LOG_DIR=str(_ROOT / "logs"),
    PROGENITOR_WORKTREE_DIR=str(_ROOT / "worktrees"),
    PROGENITOR_PROMPTS_DIR=str(_ROOT / "data" / "prompts"),
)


def _docker(*args: str, timeout: float = 10) -> str:
    result = subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=True, timeout=timeout
    )
    return result.stdout.strip()


@pytest.fixture(scope="session")
def postgres_admin_url():
    """A test-only server, never the application's configured database."""
    configured = os.getenv("PROGENITOR_TEST_DATABASE_URL", "").strip()
    if configured:
        if not configured.startswith(("postgresql://", "postgres://")):
            pytest.fail("PROGENITOR_TEST_DATABASE_URL must be a PostgreSQL URI")
        yield configured
        return

    if shutil.which("docker") is None:
        pytest.skip("Postgres tests need Docker or PROGENITOR_TEST_DATABASE_URL")
    try:
        _docker("info", "--format", "{{.ServerVersion}}", timeout=5)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pytest.skip("Postgres tests need a reachable Docker daemon or PROGENITOR_TEST_DATABASE_URL")

    image = "pgvector/pgvector:pg16"
    try:
        _docker("image", "inspect", image)
    except subprocess.CalledProcessError:
        _docker("pull", image, timeout=30)

    runner_network = None
    with suppress(subprocess.CalledProcessError):
        networks = json.loads(
            _docker(
                "inspect",
                "--type",
                "container",
                socket.gethostname(),
                "--format",
                "{{json .NetworkSettings.Networks}}",
            )
        )
        runner_network = next(
            (name for name, settings in networks.items() if settings.get("IPAddress")), None
        )
    # A containerized runner cannot reach ports bound to the Docker host's loopback.
    network_args = (
        ["--network", runner_network] if runner_network else ["--publish", "127.0.0.1::5432"]
    )

    container = f"progenitor-pytest-{uuid4().hex}"
    try:
        # The server expires even if pytest's hard watchdog bypasses teardown.
        _docker(
            "run",
            "--detach",
            "--rm",
            "--pull=never",
            "--name",
            container,
            *network_args,
            "--tmpfs",
            "/var/lib/postgresql/data:rw",
            "--env",
            "POSTGRES_PASSWORD=pytest",
            "--entrypoint",
            "/usr/bin/timeout",
            image,
            "--signal=TERM",
            "--kill-after=5s",
            "300s",
            "docker-entrypoint.sh",
            "postgres",
            "-c",
            "statement_timeout=5000",
            "-c",
            "lock_timeout=2000",
            "-c",
            "idle_in_transaction_session_timeout=10000",
        )
        if runner_network:
            networks = json.loads(
                _docker("inspect", container, "--format", "{{json .NetworkSettings.Networks}}")
            )
            host = networks[runner_network]["IPAddress"]
            port = "5432"
        else:
            host = "127.0.0.1"
            port = _docker("port", container, "5432/tcp").rsplit(":", 1)[1]
        url = f"postgresql://postgres:pytest@{host}:{port}/postgres?connect_timeout=3"
        deadline = time.monotonic() + 10
        while True:
            try:
                with psycopg.connect(url):
                    break
            except psycopg.OperationalError:
                if time.monotonic() >= deadline:
                    pytest.fail("Disposable Postgres did not become ready within 10 seconds")
                time.sleep(0.1)
        yield url
    finally:
        # It may already have exited at the lifetime limit.
        with suppress(subprocess.CalledProcessError):
            _docker("rm", "--force", container)


@pytest.fixture
def postgres_url(postgres_admin_url):
    """Give each test its own database with bounded connection and SQL waits."""
    database = f"progenitor_test_{uuid4().hex}"
    with psycopg.connect(
        postgres_admin_url,
        autocommit=True,
        connect_timeout=3,
        options="-c statement_timeout=5000 -c lock_timeout=2000",
    ) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        try:
            for setting, value in (
                ("statement_timeout", "5s"),
                ("lock_timeout", "2s"),
                ("idle_in_transaction_session_timeout", "10s"),
            ):
                admin.execute(
                    sql.SQL("ALTER DATABASE {} SET {} TO {}").format(
                        sql.Identifier(database), sql.Identifier(setting), sql.Literal(value)
                    )
                )
            parts = urlsplit(postgres_admin_url)
            params = [
                (key, value)
                for key, value in parse_qsl(parts.query)
                if key not in {"connect_timeout", "options"}
            ]
            params.append(("connect_timeout", "3"))
            url = urlunsplit(parts._replace(path=f"/{database}", query=urlencode(params)))
            with psycopg.connect(url, autocommit=True) as conn:
                conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            yield url
        finally:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))

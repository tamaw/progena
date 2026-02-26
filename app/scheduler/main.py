"""Cron-based scheduler that forwards due jobs to Progenitor over IPC."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from croniter import croniter

from app.progenitor.ipc import IPCClient

logger = logging.getLogger(__name__)


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _to_iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


@dataclass
class SchedulerConfig:
    enabled: bool = True
    poll_seconds: int = 5
    schedule_path: Path = Path("data/schedule.json")
    ipc_host: str = "127.0.0.1"
    ipc_port: int = 9500
    request_timeout_seconds: int = 300

    @classmethod
    def from_env(cls) -> SchedulerConfig:
        return cls(
            enabled=os.getenv("SCHEDULER_ENABLED", "true").lower() in ("1", "true", "yes", "on"),
            poll_seconds=max(1, int(os.getenv("SCHEDULER_POLL_SECONDS", "5"))),
            schedule_path=Path(os.getenv("SCHEDULER_SCHEDULE_PATH", "data/schedule.json")),
            ipc_host=os.getenv("SCHEDULER_IPC_HOST", "127.0.0.1"),
            ipc_port=int(os.getenv("SCHEDULER_IPC_PORT", "9500")),
            request_timeout_seconds=max(1, int(os.getenv("SCHEDULER_REQUEST_TIMEOUT_SECONDS", "300"))),
        )


class SchedulerService:
    def __init__(self, config: SchedulerConfig) -> None:
        self.config = config
        self._running = False
        self._ipc = IPCClient(
            host=config.ipc_host,
            port=config.ipc_port,
            timeout=config.request_timeout_seconds,
            auto_reconnect=True,
            max_reconnect_attempts=10,
        )

    def _read_schedule(self) -> list[dict[str, Any]]:
        path = self.config.schedule_path
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("[]", encoding="utf-8")
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("Invalid JSON in %s; resetting to empty list", path)
            data = []
        return data if isinstance(data, list) else []

    def _write_schedule(self, jobs: list[dict[str, Any]]) -> None:
        path = self.config.schedule_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(jobs, indent=2), encoding="utf-8")
        tmp.replace(path)

    def _compute_next_run(self, cron_expr: str, base: datetime) -> datetime:
        return croniter(cron_expr, base).get_next(datetime)

    async def _dispatch_job(self, job: dict[str, Any]) -> str:
        message = str(job.get("message") or "").strip()
        if not message:
            raise ValueError(f"Job {job.get('id')} has no message")
        request_id = f"scheduler_{job.get('id', 'job')}_{int(_now_utc().timestamp())}"
        chunks: list[str] = []
        async for chunk in self._ipc.send_message_stream(message, request_id=request_id):
            if chunk:
                chunks.append(chunk)
        return "".join(chunks)

    async def _run_cycle(self) -> None:
        now = _now_utc()
        jobs = self._read_schedule()
        changed = False

        for job in jobs:
            if not isinstance(job, dict):
                continue
            if not job.get("enabled", True):
                continue

            cron_expr = str(job.get("cron") or "").strip()
            if not cron_expr:
                continue

            next_run = _parse_dt(job.get("next_run"))
            if next_run is None:
                next_run = self._compute_next_run(cron_expr, now)
                job["next_run"] = _to_iso(next_run)
                changed = True

            if next_run > now:
                continue

            try:
                response = await self._dispatch_job(job)
                logger.info(
                    "Executed scheduler job %s (%d chars response)",
                    job.get("id"),
                    len(response),
                )
                job["last_status"] = "ok"
                job["last_error"] = None
            except Exception as exc:  # noqa: BLE001
                logger.exception("Scheduler job %s failed", job.get("id"))
                job["last_status"] = "error"
                job["last_error"] = str(exc)

            run_at = _now_utc()
            job["last_run"] = _to_iso(run_at)
            job["next_run"] = _to_iso(self._compute_next_run(cron_expr, run_at))
            changed = True

        if changed:
            self._write_schedule(jobs)

    async def run_forever(self) -> None:
        if not self.config.enabled:
            logger.info("Scheduler disabled (SCHEDULER_ENABLED=false)")
            return

        self._running = True
        logger.info(
            "Scheduler started (poll=%ss, schedule=%s, ipc=%s:%s)",
            self.config.poll_seconds,
            self.config.schedule_path,
            self.config.ipc_host,
            self.config.ipc_port,
        )

        await self._ipc.connect()
        try:
            while self._running:
                await self._run_cycle()
                await asyncio.sleep(self.config.poll_seconds)
        finally:
            await self._ipc.disconnect()

    def stop(self) -> None:
        self._running = False


async def _amain() -> None:
    logging.basicConfig(
        level=os.getenv("SCHEDULER_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    service = SchedulerService(SchedulerConfig.from_env())
    await service.run_forever()


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()


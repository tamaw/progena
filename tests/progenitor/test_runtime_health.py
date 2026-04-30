import json

import pytest

from app.progenitor.runtime.health import HEALTH_FILE, update_health_file


@pytest.mark.asyncio
async def test_health_file_contains_only_operational_fields(tmp_path):
    await update_health_file(state_dir=tmp_path, status="running")

    health = json.loads((tmp_path / HEALTH_FILE).read_text())

    assert set(health) == {"status", "last_active", "pid"}
    assert health["status"] == "running"
    assert isinstance(health["last_active"], str)
    assert isinstance(health["pid"], int)

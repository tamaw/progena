from app.progenitor.config import ProgenitorConfig, StorageConfig
from app.progenitor.main import Progenitor


def test_progenitor_uses_namespaced_logs_dir(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AGENT_NAME", "progenitor")
    config = ProgenitorConfig(
        storage=StorageConfig(
            state_dir=tmp_path / "state",
            log_dir=tmp_path / "logs",
            worktree_dir=tmp_path / "worktrees",
            prompts_dir=tmp_path / "prompts",
        )
    )

    progenitor = Progenitor(config)

    assert progenitor.logs_dir == tmp_path / "logs" / "progenitor"
    assert progenitor.state_manager.logs_dir == tmp_path / "logs" / "progenitor"

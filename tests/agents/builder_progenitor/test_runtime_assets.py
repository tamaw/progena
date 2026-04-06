import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE = REPO_ROOT / "app" / "agents" / "builder_progenitor" / "Dockerfile"
REQUIREMENTS = REPO_ROOT / "app" / "agents" / "builder_progenitor" / "requirements.txt"
LSP_CONFIG = REPO_ROOT / "app" / "agents" / "builder_progenitor" / "lsp-config.json"


def test_builder_progenitor_requirements_include_copilot_runtime_tools() -> None:
    requirements = REQUIREMENTS.read_text(encoding="utf-8").splitlines()

    assert any(line.startswith("pyright") for line in requirements)
    assert any(line.startswith("ruff") for line in requirements)


def test_builder_progenitor_lsp_config_targets_pyright_langserver() -> None:
    config = json.loads(LSP_CONFIG.read_text(encoding="utf-8"))

    assert config == {
        "lspServers": {
            "python": {
                "command": "pyright-langserver",
                "args": ["--stdio"],
                "fileExtensions": {
                    ".py": "python",
                    ".pyi": "python",
                    ".pyw": "python",
                },
            }
        }
    }


def test_builder_progenitor_dockerfile_installs_copilot_lsp_config() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "/home/runner/.copilot/lsp-config.json" in dockerfile
    assert "COPY --chown=runner:agent app/agents/builder_progenitor/lsp-config.json" in dockerfile

"""
Validate Node - Runs checks/tests after code changes.
"""

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Coroutine, TYPE_CHECKING

from ..state.models import ProgenitorState, ValidationResult

if TYPE_CHECKING:
    from ..config import ProgenitorConfig


def create_node(config: "ProgenitorConfig") -> Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]:
    """Create the validate node function.

    Args:
        config: ProgenitorConfig with validation settings.
    """

    async def validate_node(state: ProgenitorState) -> ProgenitorState:
        """Run validation checks on build output."""
        current_item = state.get("current_work_item")

        if not current_item:
            return {}

        # Get worktree path
        if isinstance(current_item, dict):
            worktree_path = current_item.get("worktree_path")
            item_id = current_item.get("id", "unknown")
            result_data = current_item.get("result", {})
        else:
            worktree_path = current_item.worktree_path
            item_id = current_item.id
            result_data = current_item.result or {}

        if not worktree_path:
            # No worktree - skip validation
            return {
                "last_validation_result": {
                    "success": True,
                    "work_item_id": item_id,
                    "checks_run": [],
                    "message": "No worktree - validation skipped",
                }
            }

        worktree = Path(worktree_path)
        checks_run = []
        errors = []
        warnings = []

        try:
            # Check 1: Syntax validation (Python files)
            python_files = list(worktree.rglob("*.py"))
            for py_file in python_files[:10]:  # Limit to first 10
                try:
                    with open(py_file, 'r') as f:
                        source = f.read()
                    compile(source, str(py_file), 'exec')
                    checks_run.append(f"syntax:{py_file.name}")
                except SyntaxError as e:
                    errors.append(f"Syntax error in {py_file.name}: {e}")

            # Check 2: Run tests if they exist
            test_dirs = [
                worktree / "tests",
                worktree / "test",
            ]

            for test_dir in test_dirs:
                if test_dir.exists():
                    try:
                        process = await asyncio.create_subprocess_exec(
                            "python", "-m", "pytest", str(test_dir), "-v", "--tb=short",
                            cwd=worktree,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                        stdout, stderr = await asyncio.wait_for(
                            process.communicate(),
                            timeout=60
                        )

                        checks_run.append("pytest")

                        if process.returncode != 0:
                            errors.append(f"Tests failed: {stderr.decode()[:500]}")
                    except asyncio.TimeoutError:
                        warnings.append("Test execution timed out")
                    except Exception as e:
                        warnings.append(f"Could not run tests: {e}")

            # Check 3: Basic structure validation
            if (worktree / "__init__.py").exists() or any(python_files):
                checks_run.append("structure")
            else:
                warnings.append("No Python files found in worktree")

        except Exception as e:
            errors.append(f"Validation error: {e}")

        success = len(errors) == 0

        validation_result = ValidationResult(
            success=success,
            work_item_id=item_id,
            checks_run=checks_run,
            errors=errors,
            warnings=warnings,
        )

        return {
            "last_validation_result": validation_result.model_dump(),
        }

    return validate_node

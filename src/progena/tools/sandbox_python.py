"""Python execution in disposable Daytona sandboxes, not on the runtime host."""

from __future__ import annotations

from pydantic import BaseModel, ValidationError

from src.progena.sandboxes.execution import SandboxPythonRequest
from src.progena.tools.protocol import BaseSystemTool, ToolContext, ToolResult


class SandboxPythonTool(BaseSystemTool):
    @property
    def name(self) -> str:
        return "sandbox_python"

    @property
    def description(self) -> str:
        return (
            "Write and run Python in a fresh Daytona sandbox; never execute it on the Progena host. "
            "Supply code or code_file, not both. Python receives the JSON object inputs and must "
            "assign a JSON-serializable result. Relative file paths use the sandbox working directory. "
            "input_files maps sandbox-relative destinations to explicit virtual source paths. "
            "output_files maps sandbox-relative sources to /artifacts/ or configured /shared/ paths. "
            "Only selected files are transferred; virtual directories and host credentials are not "
            "mounted remotely. Each call starts fresh, returns result/stdout/stderr and export paths, "
            "and deletes the sandbox. Use files/results, not shell or Python variables, between calls."
        )

    @property
    def args_model(self) -> type[BaseModel]:
        return SandboxPythonRequest

    async def execute(self, context: ToolContext) -> ToolResult:
        if context.sandbox_python is None:
            return ToolResult.fail(
                "Daytona Python execution is not configured. Set DAYTONA_API_KEY on the "
                "Progena runtime; there is no host-execution fallback."
            )
        try:
            request = SandboxPythonRequest.model_validate(context.tool_args)
            return ToolResult.ok(await context.sandbox_python(request))
        except ValidationError as exc:
            return ToolResult.fail(f"Invalid sandbox Python request: {exc}")
        except Exception as exc:  # boundary: provider/transfer errors must reach the caller
            return ToolResult.fail(f"Sandbox Python execution failed: {exc}")

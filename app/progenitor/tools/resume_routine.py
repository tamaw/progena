"""Resume a routine run paused at a human-in-the-loop node."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .protocol import BaseSystemTool, ToolContext, ToolResult


class ResumeRoutineArgs(BaseModel):
    thread_id: str = Field(
        description=(
            "The routine run's thread_id, exactly as given in the "
            "'waiting for a human answer' message (routine:<id>:<revision>:<uuid>)."
        )
    )
    answer: str = Field(description="The answer to give the routine's human node.")


class ResumeRoutineTool(BaseSystemTool):
    """Answer a routine's human node and continue its run from where it paused."""

    @property
    def name(self) -> str:
        return "resume_routine"

    @property
    def description(self) -> str:
        return (
            "Resume a routine run that is waiting on a human node's question, "
            "given the thread_id it reported and an answer."
        )

    @property
    def args_model(self) -> type[BaseModel]:
        return ResumeRoutineArgs

    async def execute(self, context: ToolContext) -> ToolResult:
        if context.resume_routine is None:
            return ToolResult.fail("Routine resume is not available in this context.")

        args = context.tool_args if isinstance(context.tool_args, dict) else {}
        thread_id = str(args.get("thread_id") or "").strip()
        answer = args.get("answer")
        if not thread_id:
            return ToolResult.fail("Missing 'thread_id'")
        if answer is None or (isinstance(answer, str) and not answer.strip()):
            return ToolResult.fail("Missing 'answer'")

        try:
            result = await context.resume_routine(thread_id, answer)
        except ValueError as exc:
            return ToolResult.fail(str(exc))
        except Exception as exc:  # noqa: BLE001 - report, don't crash the calling turn
            return ToolResult.fail(f"Failed to resume routine: {exc}")

        return ToolResult.ok(result)

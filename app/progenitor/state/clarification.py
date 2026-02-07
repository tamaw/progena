"""
Clarification Detector - Detects when Copilot CLI needs more information.

Analyzes Copilot CLI output to determine if clarification is needed from the user.
Uses LLM to:
1. Detect if output indicates need for more information
2. Format a clear question for the user
3. Structure context for incorporating the response

This module integrates with the VERIFY phase as part of stop condition checking.
"""

import logging
from typing import Any, Optional
from pydantic import BaseModel, Field

from ..runtime.time_utils import utcnow_isoformat
from ..llm.prompts import PromptTemplates

logger = logging.getLogger(__name__)

_prompts = PromptTemplates()


class ClarificationRequest(BaseModel):
    """A request for clarification from the user."""

    needs_clarification: bool = Field(
        default=False,
        description="Whether clarification is needed"
    )
    question: str = Field(
        default="",
        description="Formatted question to ask the user"
    )
    context: str = Field(
        default="",
        description="Raw context from output indicating the need"
    )
    category: str = Field(
        default="general",
        description="Category: requirements, technical, design, scope, other"
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence that clarification is needed (0-1)"
    )
    original_output: str = Field(
        default="",
        description="Original output that triggered clarification"
    )


class ClarificationDetector:
    """
    Detects when Copilot CLI output indicates need for more information.

    Integrates with the inner loop VERIFY phase. After BUILD completes,
    the detector analyzes the output to see if Copilot was blocked or
    uncertain due to missing information.

    Detection signals include:
    - Explicit questions in output
    - "I need more information about..."
    - Incomplete implementations with TODO markers
    - Uncertainty language ("unclear", "ambiguous", "not specified")
    - Request for clarification patterns
    """

    # Patterns that strongly indicate need for clarification
    STRONG_SIGNALS = [
        "need more information",
        "please clarify",
        "could you specify",
        "what should",
        "which approach",
        "unclear requirement",
        "ambiguous",
        "not specified",
        "please provide",
        "need to know",
        "waiting for input",
        "require additional context",
        "missing information",
    ]

    # Patterns that weakly suggest potential need for clarification
    WEAK_SIGNALS = [
        "TODO:",
        "FIXME:",
        "placeholder",
        "not implemented",
        "assumed",
        "default value",
        "may need to adjust",
    ]

    def __init__(self, llm_client=None):
        """
        Initialize the detector.

        Args:
            llm_client: Optional LLM client for nuanced detection.
                       Falls back to heuristic detection if not provided.
        """
        self.llm_client = llm_client

    async def detect(
        self,
        copilot_output: dict[str, Any],
        task_description: str,
        ideal_state: Optional[Any] = None,
    ) -> ClarificationRequest:
        """
        Analyze Copilot output to detect if clarification is needed.

        Args:
            copilot_output: Output from CopilotCLI.build() containing
                           stdout, stderr, summary, etc.
            task_description: Original task/build description
            ideal_state: Optional ideal state for context

        Returns:
            ClarificationRequest with detection results
        """
        stdout = copilot_output.get("stdout", "")
        stderr = copilot_output.get("stderr", "")
        summary = copilot_output.get("summary", "")
        success = copilot_output.get("success", False)

        combined_output = f"{stdout}\n{stderr}\n{summary}".strip()

        # Quick heuristic check first
        heuristic_result = self._heuristic_detect(combined_output)

        # If heuristics found nothing and build succeeded, no clarification needed
        if not heuristic_result.needs_clarification and success:
            return ClarificationRequest(
                needs_clarification=False,
                original_output=combined_output[:1000],
            )

        # Use LLM for deeper analysis if available
        if self.llm_client:
            return await self._llm_detect(
                combined_output,
                task_description,
                ideal_state,
                heuristic_result,
            )

        return heuristic_result

    def _heuristic_detect(self, output: str) -> ClarificationRequest:
        """
        Heuristic-based detection of clarification needs.

        Scans output for known patterns that indicate Copilot
        is uncertain or needs more information.
        """
        output_lower = output.lower()

        # Check for strong signals
        strong_matches = []
        for signal in self.STRONG_SIGNALS:
            if signal.lower() in output_lower:
                # Find the context around the match
                idx = output_lower.find(signal.lower())
                start = max(0, idx - 50)
                end = min(len(output), idx + len(signal) + 100)
                context = output[start:end].strip()
                strong_matches.append({
                    "signal": signal,
                    "context": context,
                })

        # Check for weak signals
        weak_matches = []
        for signal in self.WEAK_SIGNALS:
            if signal.lower() in output_lower:
                weak_matches.append(signal)

        # Calculate confidence
        if strong_matches:
            confidence = min(0.9, 0.5 + 0.1 * len(strong_matches))
            needs_clarification = True
            # Extract the most relevant context
            context = strong_matches[0]["context"]
            # Simple question generation from context
            question = f"The build process indicated: '{context}'. Could you provide more details?"
            category = self._categorize_need(strong_matches[0]["signal"])
        elif len(weak_matches) >= 3:
            confidence = 0.4
            needs_clarification = True
            context = f"Found multiple incomplete markers: {', '.join(weak_matches[:3])}"
            question = "The build has several incomplete sections. Would you like to provide more specific requirements?"
            category = "requirements"
        else:
            confidence = 0.0
            needs_clarification = False
            context = ""
            question = ""
            category = "general"

        return ClarificationRequest(
            needs_clarification=needs_clarification,
            question=question,
            context=context,
            category=category,
            confidence=confidence,
            original_output=output[:1000],
        )

    def _categorize_need(self, signal: str) -> str:
        """Categorize the type of clarification needed."""
        signal_lower = signal.lower()

        if any(w in signal_lower for w in ["what", "which", "how"]):
            return "design"
        elif any(w in signal_lower for w in ["requirement", "specify", "unclear"]):
            return "requirements"
        elif any(w in signal_lower for w in ["technical", "implementation"]):
            return "technical"
        elif any(w in signal_lower for w in ["scope", "boundary"]):
            return "scope"
        else:
            return "general"

    async def _llm_detect(
        self,
        output: str,
        task_description: str,
        ideal_state: Optional[Any],
        heuristic_result: ClarificationRequest,
    ) -> ClarificationRequest:
        """
        Use LLM for nuanced clarification detection.

        Provides more accurate detection and better question formatting
        than pure heuristics.
        """
        # Build context for LLM
        ideal_state_summary = ""
        if ideal_state:
            if hasattr(ideal_state, "task_description"):
                ideal_state_summary = f"Task: {ideal_state.task_description}"
            elif isinstance(ideal_state, dict):
                ideal_state_summary = f"Task: {ideal_state.get('task_description', '')}"

        prompt = _prompts.get(
            "clarification.detection.md",
            task_description=task_description,
            ideal_state_summary=ideal_state_summary,
            output_tail=output[-2000:],
            heuristic_needs=str(heuristic_result.needs_clarification),
            heuristic_confidence=str(heuristic_result.confidence),
            heuristic_context=str(heuristic_result.context),
        )

        try:
            # llm_client is guaranteed to be not None here due to the check above
            assert self.llm_client is not None
            response = await self.llm_client.generate(
                prompt=prompt,
                system_prompt=_prompts.get("clarification.detection_system.md"),
            )

            return self._parse_llm_response(response, output)

        except Exception as e:
            logger.warning(f"LLM detection failed, using heuristics: {e}")
            return heuristic_result

    def _parse_llm_response(
        self,
        response: str,
        original_output: str,
    ) -> ClarificationRequest:
        """Parse LLM response into ClarificationRequest."""
        lines = response.strip().split("\n")

        result = ClarificationRequest(original_output=original_output[:1000])

        for line in lines:
            line = line.strip()
            if line.startswith("NEEDS_CLARIFICATION:"):
                value = line.split(":", 1)[1].strip().lower()
                result.needs_clarification = value in ("yes", "true", "1")
            elif line.startswith("CONFIDENCE:"):
                try:
                    result.confidence = float(line.split(":", 1)[1].strip())
                except ValueError:
                    result.confidence = 0.5
            elif line.startswith("CATEGORY:"):
                result.category = line.split(":", 1)[1].strip().lower()
            elif line.startswith("CONTEXT:"):
                result.context = line.split(":", 1)[1].strip()
            elif line.startswith("QUESTION:"):
                result.question = line.split(":", 1)[1].strip()

        return result

    async def format_user_question(
        self,
        clarification: ClarificationRequest,
        task_description: str,
    ) -> str:
        """
        Format the clarification request into a user-friendly message.

        This is called when we need to present the clarification
        request to the user in the response.
        """
        if not clarification.needs_clarification:
            return ""

        # If we have a pre-formatted question, use it
        if clarification.question:
            base_question = clarification.question
        else:
            base_question = "Could you provide more details about your requirements?"

        # Add context about what we're building
        message_parts = [
            "**Clarification Needed**",
            "",
            f"While building your request, I need some additional information:",
            "",
            f"❓ {base_question}",
            "",
        ]

        # Add category-specific guidance
        if clarification.category == "requirements":
            message_parts.append("_Please describe what specific behavior or functionality you need._")
        elif clarification.category == "technical":
            message_parts.append("_Please specify any technical constraints or preferences._")
        elif clarification.category == "design":
            message_parts.append("_Please describe your preferred approach or design._")
        elif clarification.category == "scope":
            message_parts.append("_Please clarify what should or shouldn't be included._")

        message_parts.extend([
            "",
            "Once you respond, I'll incorporate your feedback and continue building.",
        ])

        return "\n".join(message_parts)


def create_clarification_history_entry(
    clarification: ClarificationRequest,
    user_response: Optional[str] = None,
) -> dict[str, Any]:
    """Create a history entry for a clarification exchange."""
    return {
        "timestamp": utcnow_isoformat(),
        "question": clarification.question,
        "category": clarification.category,
        "confidence": clarification.confidence,
        "context": clarification.context,
        "user_response": user_response,
        "resolved": user_response is not None,
    }

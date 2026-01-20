"""
THINK Node - Second phase of inner loop (loop.spec.md).

Generates approaches, trade-offs, and risks for reaching ideal state.
"""

import logging
from typing import Callable, Coroutine, Any

from ..state.models import ProgenitorState
from ..runtime.time_utils import utcnow_isoformat

logger = logging.getLogger(__name__)


def create_node(
    llm_client,
    state_manager=None,
) -> Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]:
    """Create the think node function."""
    
    async def think_node(state: ProgenitorState) -> ProgenitorState:
        """
        THINK phase: Generate approaches, trade-offs, and risks.
        
        Considers:
        - Current ideal state and criteria
        - Previous iteration results (if any)
        - Available tools and capabilities
        - Constraints and anti-criteria
        """
        ideal_state = state.get("ideal_state")
        if not ideal_state:
            logger.warning("No ideal state in THINK phase")
            return {}
        
        iteration = state.get("current_iteration", 0)
        last_verification = state.get("last_verification_report")
        
        # Build thinking prompt
        prompt = _build_thinking_prompt(ideal_state, iteration, last_verification, state)
        
        try:
            response = await llm_client.generate(
                prompt=prompt,
                system_prompt="You are a strategic thinker analyzing approaches to reach ideal state.",
            )
            
            # Parse approaches
            approaches = _parse_approaches(response)
            
            if state_manager:
                await state_manager.log_decision({
                    "event": "thinking_phase",
                    "iteration": iteration,
                    "approaches_generated": len(approaches),
                    "timestamp": utcnow_isoformat(),
                })
            
            return {
                "current_approaches": approaches,
                "thinking_output": response,
            }
            
        except Exception as e:
            logger.error(f"THINK phase error: {e}")
            # Fallback to simple approach
            return {
                "current_approaches": [{
                    "name": "default",
                    "description": "Proceed with standard execution",
                    "risks": ["Unknown risks due to thinking error"],
                }],
            }
    
    return think_node


def _build_thinking_prompt(ideal_state, iteration: int, last_verification, state) -> str:
    """Build prompt for thinking phase."""
    ideal_state_dict = ideal_state.model_dump() if hasattr(ideal_state, "model_dump") else ideal_state
    
    prompt_parts = [
        "Analyze approaches to reach this ideal state:",
        "",
        f"IDEAL STATE:",
        f"Task: {ideal_state_dict.get('task_description', 'N/A')}",
        f"Success Criteria: {len(ideal_state_dict.get('success_criteria', []))} criteria",
        f"Anti-Criteria: {len(ideal_state_dict.get('anti_criteria', []))} to avoid",
    ]
    
    if iteration > 0 and last_verification:
        prompt_parts.extend([
            "",
            f"PREVIOUS ITERATION (#{iteration}):",
            f"Score: {last_verification.get('overall_score', 0):.2f}",
            f"Passed: {last_verification.get('passed', False)}",
            f"Gaps: {', '.join(last_verification.get('gaps', [])[:3])}",
        ])
    
    prompt_parts.extend([
        "",
        "Generate 2-3 approaches with:",
        "1. Strategy description",
        "2. Key trade-offs",
        "3. Known risks",
        "4. Expected outcome",
        "",
        "Focus on addressing gaps and meeting criteria.",
    ])
    
    return "\n".join(prompt_parts)


def _parse_approaches(response: str) -> list[dict[str, Any]]:
    """Parse approaches from LLM response."""
    # Simplified parser - would be more sophisticated in production
    return [
        {
            "name": "primary",
            "description": response[:500] if response else "Standard approach",
            "trade_offs": ["Speed vs quality"],
            "risks": ["May need iteration"],
            "expected_outcome": "Progress toward ideal state",
        }
    ]

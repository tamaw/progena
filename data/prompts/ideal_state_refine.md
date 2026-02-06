# Ideal State Refinement

You refine success criteria based on observed iteration outcomes. Be specific about what to tighten, add, or adjust.

## Current Ideal State

{current_state}

## Iteration Results

{results}

## Instructions

Based on the iteration results, suggest refinements:

### New Success Criteria
- Only add criteria that address gaps revealed by the iteration
- Use unique IDs that don't conflict with existing ones (prefix with "criterion_refined_")

### Tightened Criteria
- Identify existing criteria that were too loose (passed too easily)
- Provide more specific measurements and higher target scores
- Reference the existing criterion_id exactly

### New Anti-Criteria
- Add anti-criteria for any issues or violations observed in the iteration
- Use unique IDs prefixed with "anti_refined_"
- Be specific about detection methods

### Quality Bar Updates
- If the iteration revealed the quality bar was too low or too vague, update it
- Leave updated_quality_description empty if no change is needed

### Reasoning
- Explain WHY each refinement is needed based on the iteration evidence

# THINK Node Improvements Plan (Updated Analysis)

## Summary
The THINK node (app/progenitor/nodes/think.py) is functional but has several areas for improvement related to robustness, maintainability, consistency, and intelligence. This analysis combines the original review with deeper inspection of context handling and learning capabilities.

---

## Critical Issues (High Priority)

### 1. **Hardcoded System Prompt**
**Location:** Lines 96-103
**Issue:** The system prompt for THINK is hardcoded directly in the node function, not loaded from the prompts system like other nodes.
**Impact:**
- Inconsistent with other nodes (LEARN, VALIDATE, etc.)
- Makes prompt iteration harder (requires code changes vs markdown edits)
- Violates the pattern established in prompts.py

**Improvement:**
- Create `data/prompts/think.md` with the system prompt
- Add `get_think_prompt()` method to `PromptTemplates` class
- Update `think_node` to load prompt from file

---

### 2. **Brittle LLM Response Parsing**
**Location:** Lines 332-506 (`_parse_structured_approaches`)
**Issue:** Uses fragile text parsing with lots of string matching assumptions. Fails if LLM formats response slightly differently.
**Impact:**
- Fallback to generic placeholder approaches (lines 432-456, 458-485)
- Low quality when parsing fails
- Hard to debug when parsing goes wrong

**Improvement Options:**

**Option A: Use Structured Output (RECOMMENDED)**
- Use LLM's structured output/function calling with `StructuredApproach` Pydantic model
- Eliminates parsing brittleness entirely
- Validates output against schema
- Example:
```python
response = await llm_client.generate_structured(
    prompt=prompt,
    system_prompt=system_prompt,
    response_model=ThinkOutput,  # Pydantic model
)
# response is already a ThinkOutput object, no parsing needed
```

**Option B: Enhanced Parsing**
- Add validation/logging for parse failures
- Better error recovery
- More flexible pattern matching

---

### 3. **Limited Context in Prompt**
**Location:** Lines 216-329 (`_build_thinking_prompt`)
**Issue:** The prompt doesn't include critical context that would help generate better approaches:
- Available tools/capabilities (from state or config)
- Resource constraints (time, compute, API limits)
- Dependencies information
- Complexity estimates from previous iterations
- Anti-patterns that failed before

**Impact:**
- LLM generates approaches without knowing what tools are available
- Can't optimize for resource constraints
- May suggest approaches that aren't feasible

**Improvement:**
```python
def _build_thinking_prompt(ideal_state, iteration: int, last_verification, state) -> str:
    # ... existing code ...

    # Add available tools context
    available_tools = state.get("config", {}).get("available_tools", [])
    if available_tools:
        prompt_parts.extend([
            "",
            "AVAILABLE TOOLS:",
            *[f"  - {tool}" for tool in available_tools[:10]],
        ])

    # Add resource constraints
    constraints = state.get("config", {}).get("constraints", {})
    if constraints:
        prompt_parts.extend([
            "",
            "CONSTRAINTS:",
            f"  - Max iterations: {constraints.get('max_iterations', 'N/A')}",
            f"  - Time budget: {constraints.get('time_budget', 'N/A')}",
        ])

    # Add dependency info
    deps = state.get("dependencies", {})
    if deps:
        prompt_parts.extend([
            "",
            "DEPENDENCIES:",
            *[f"  - {dep}" for dep in deps.get("critical", [])[:5]],
        ])
```

---

### 4. **No Approach Quality Validation**
**Location:** Throughout - missing validation layer
**Issue:** Generated approaches are not validated for quality:
- Do they actually address the stated gaps?
- Are implementation steps concrete and actionable?
- Are trade-offs and risks meaningful?
- Is the approach feasible given constraints?

**Impact:**
- Low-quality approaches make it to PLAN node
- Wastes cycles on infeasible approaches
- No feedback to improve prompt or selection

**Improvement:**
```python
def _validate_approach(
    approach: StructuredApproach,
    gaps: list[str],
    available_tools: list[str]
) -> tuple[bool, float, list[str]]:
    """Validate approach quality and return (is_valid, quality_score, issues)"""
    issues = []
    quality_score = 1.0

    # Check implementation steps are concrete
    if len(approach.implementation_steps) < 2:
        issues.append("Too few implementation steps")
        quality_score -= 0.3

    # Check gap coverage
    if gaps and not approach.addresses_gaps:
        issues.append("Doesn't address any identified gaps")
        quality_score -= 0.4

    # Validate addresses_gaps actually mentions the gaps
    if approach.addresses_gaps:
        mentioned_gaps = sum(1 for ag in approach.addresses_gaps
                           if any(g.lower() in ag.lower() for g in gaps))
        if mentioned_gaps == 0:
            issues.append("Claims to address gaps but doesn't reference them")
            quality_score -= 0.3

    # Check trade-offs exist and are meaningful
    if not approach.trade_offs:
        issues.append("No trade-offs identified")
        quality_score -= 0.2
    elif all(t.severity == "low" for t in approach.trade_offs):
        issues.append("All trade-offs marked as low severity (needs diversity)")
        quality_score -= 0.1

    # Check risks exist and are meaningful
    if not approach.risks:
        issues.append("No risks identified")
        quality_score -= 0.2

    # Check for overly generic descriptions
    generic_terms = ["execute", "implement", "test", "verify"]
    if all(any(term in step.lower() for term in generic_terms)
           for step in approach.implementation_steps[:3]):
        issues.append("Implementation steps are too generic")
        quality_score -= 0.2

    return quality_score > 0.5, max(0, quality_score), issues
```

---

## Important Issues (Medium Priority)

### 5. **Low-Quality Placeholder Approaches**
**Location:** Lines 458-485
**Issue:** When fewer than 2 approaches are parsed, the code creates generic placeholder approaches that don't add real value.

**Current behavior:**
```python
while len(approaches) < 2:
    alt_approach = StructuredApproach(
        id=f"alternative_{uuid.uuid4().hex[:6]}",
        name=f"alternative_{len(approaches) + 1}",
        description="Alternative approach with different trade-offs",
        ...
    )
```

**Improvement:**
- Instead of creating generic placeholders, retry the LLM call with more specific instructions
- Add a note in the THINK output indicating only N approaches were generated
- Don't artificially inflate the count with low-quality data
- OR: Use previous iteration's approaches as fallback if available

---

### 6. **Naive Approach Selection Algorithm**
**Location:** Lines 494-497
**Issue:** The recommended approach is selected using an overly simple weighted formula:
```python
recommended = max(approaches, key=lambda a: (
    len(set(a.addresses_gaps) & set(gaps_analyzed)) * 0.3 + a.expected_success_probability
))
```

**Problems:**
- Doesn't consider risk severity (selecting high-risk approach with slightly higher probability)
- Ignores trade-off severity
- No consideration of previous iteration patterns
- No diversity bonus (avoids repeating same approach type)
- Weights are hardcoded and not tunable

**Improvement:**
```python
def _select_recommended_approach(
    approaches: list[StructuredApproach],
    gaps: list[str],
    previous_approaches: list[str] = None
) -> tuple[str, str]:
    """Select best approach considering multiple factors."""
    scores = []

    for approach in approaches:
        score = approach.expected_success_probability

        # Bonus for addressing gaps (weighted by coverage)
        gap_coverage = len(set(approach.addresses_gaps) & set(gaps)) / max(1, len(gaps))
        score += gap_coverage * 0.3

        # Penalty for high-severity risks
        for risk in approach.risks:
            if risk.likelihood == "high" and risk.impact == "high":
                score -= 0.2
            elif risk.likelihood == "high" or risk.impact == "high":
                score -= 0.1

        # Penalty for high-severity trade-offs
        high_severity_tradeoffs = sum(1 for t in approach.trade_offs if t.severity == "high")
        score -= high_severity_tradeoffs * 0.1

        # Diversity bonus (avoid repeating previous approach types)
        if previous_approaches and approach.name not in previous_approaches:
            score += 0.05

        # Complexity penalty (simpler is better, all else equal)
        complexity = len(approach.implementation_steps)
        if complexity > 10:
            score -= 0.05

        scores.append((score, approach))

    best_score, best_approach = max(scores, key=lambda x: x[0])

    # Build reasoning
    reasoning = (
        f"Selected '{best_approach.name}' with {best_approach.expected_success_probability:.0%} "
        f"success probability (adjusted score: {best_score:.2f}). "
    )
    if gap_coverage > 0.5:
        reasoning += f"Addresses {len(set(best_approach.addresses_gaps) & set(gaps))}/{len(gaps)} gaps. "

    return best_approach.id, reasoning
```

---

### 7. **Fragile Trade-off/Risk Parsing**
**Location:** Lines 509-586 (`_parse_tradeoff`, `_parse_risk`)
**Issue:** Uses regex-like string matching which is fragile.

**Example:**
```python
if "gain" in rest_lower and "lose" in rest_lower:
    parts = rest.split("but")
```

**Improvement:**
- Use structured output (see #2) - eliminates this entirely
- If keeping text parsing, add more robust patterns
- Better error handling and logging
- Test with diverse LLM output formats

---

### 8. **Missing Approach Comparison Output**
**Location:** Return value (lines 159-169)
**Issue:** The output includes individual approaches but no explicit comparison to help PLAN node decide between them.

**Impact:**
- PLAN node must re-analyze differences
- No guidance on trade-offs between approaches
- Harder to explain to users why one was chosen

**Improvement:**
```python
# In ThinkOutput or return value, add:
{
    "phases": {
        "think": {
            "approaches": approaches_dicts,
            "recommended_id": think_output.recommended_approach_id,
            "gaps_analyzed": gaps_analyzed,
            "comparison_matrix": {
                "dimensions": ["success_prob", "risk_level", "complexity", "gap_coverage"],
                "values": [
                    {
                        "approach_id": a.id,
                        "success_prob": a.expected_success_probability,
                        "risk_level": _calculate_risk_score(a),
                        "complexity": len(a.implementation_steps),
                        "gap_coverage": len(set(a.addresses_gaps) & set(gaps)),
                    }
                    for a in approaches
                ],
            },
            "key_differences": _highlight_key_differences(approaches),
        },
    },
}
```

---

### 9. **No Meta-Learning from Approach Success**
**Location:** Throughout
**Issue:** While the LEARN node tracks lessons learned, THINK doesn't track which *approach types* succeeded or failed over time.

**Impact:**
- Can't learn that "incremental approaches work better than big-bang"
- Can't adjust success probability estimates based on historical data
- Missing opportunity for continuous improvement

**Improvement:**
- Track approach type success rates in state or persistent storage
- Use historical success rates to adjust expected_success_probability
- Prefer approach patterns that have worked before
- Example:
```python
# In state_manager or database:
approach_history = {
    "incremental": {"attempts": 15, "successes": 12, "avg_score": 0.82},
    "big_bang": {"attempts": 8, "successes": 3, "avg_score": 0.45},
    "hybrid": {"attempts": 10, "successes": 7, "avg_score": 0.68},
}

# Use in selection:
if approach.name in approach_history:
    historical_success_rate = approach_history[approach.name]["successes"] / max(1, approach_history[approach.name]["attempts"])
    # Blend LLM estimate with historical data
    adjusted_probability = 0.7 * approach.expected_success_probability + 0.3 * historical_success_rate
```

---

## Lower Priority Issues

### 10. **Prompt Building Code is Long and Awkward**
**Location:** Lines 216-329 (`_build_thinking_prompt`)
**Issue:** The function is quite long with awkward string building patterns.

**Improvement:**
- Create a dedicated prompt template in `data/prompts/think.md`
- Use template substitution (jinja2 or similar) instead of building strings in code
- Move prompt construction logic to prompts.py

---

### 11. **Inconsistent Error Handling**
**Location:** Lines 170-211 (error handling)
**Issue:** On error, returns a single default approach. Doesn't distinguish between different error types.

**Improvement:**
- Log more details about what failed (parsing error vs LLM error vs validation error)
- Consider retrying with simpler prompt for transient failures
- Return more informative error state
- Track error patterns for debugging

---

### 12. **No Unit Tests**
**Issue:** No evidence of unit tests for the parsing logic, which is complex and error-prone.

**Improvement:**
- Add unit tests for `_parse_structured_approaches` with various LLM outputs
- Test edge cases (malformed responses, missing sections, partial data)
- Test the recommendation selection logic with different scoring scenarios
- Test validation logic
- Property-based tests (all outputs meet minimum quality bar)

---

### 13. **Unused Import**
**Location:** Line 22
**Issue:** `PromptSpec` is imported but never used.

**Improvement:**
- Remove unused import, OR
- Implement its intended use (if it was meant for prompt templating)

---

## Recommended Implementation Order

### Phase 1: Foundation & Robustness (HIGH priority)
**Goal:** Make THINK reliable and production-ready

1. **Implement structured output** (Issue #2)
   - Use `response_model` parameter in llm_client
   - Eliminates parsing brittleness entirely
   - ~2-3 hours

2. **Externalize system prompt** (Issue #1)
   - Create `data/prompts/think.md`
   - Add `get_think_prompt()` to prompts.py
   - ~1 hour

3. **Add approach validation** (Issue #4)
   - Implement `_validate_approach()` function
   - Reject/retry low-quality approaches
   - ~2-3 hours

4. **Enhance prompt context** (Issue #3)
   - Add available tools, constraints, dependencies
   - ~2 hours

**Total Phase 1:** 7-9 hours

---

### Phase 2: Intelligence & Quality (MEDIUM priority)
**Goal:** Make THINK smarter and more effective

5. **Improve selection algorithm** (Issue #6)
   - Multi-factor scoring with risk/trade-off penalties
   - Configurable weights
   - Better reasoning output
   - ~3 hours

6. **Remove placeholder generation** (Issue #5)
   - Use retry logic instead
   - Fall back to previous iteration approaches
   - ~1-2 hours

7. **Add approach comparison output** (Issue #8)
   - Comparison matrix
   - Key differences highlighting
   - ~2 hours

8. **Better error handling** (Issue #11)
   - Distinguish error types
   - Retry logic for transient failures
   - Better logging
   - ~2 hours

**Total Phase 2:** 8-9 hours

---

### Phase 3: Polish & Learning (LOW priority)
**Goal:** Long-term improvements and maintainability

9. **Add meta-learning** (Issue #9)
   - Track approach type success rates
   - Use historical data in selection
   - ~4-5 hours

10. **Refactor prompt building** (Issue #10)
    - Move to template system (jinja2)
    - Cleaner code structure
    - ~2-3 hours

11. **Comprehensive unit tests** (Issue #12)
    - Test parsing, validation, selection
    - Edge cases and property tests
    - ~4-5 hours

12. **Code cleanup** (Issue #13)
    - Remove unused imports
    - ~15 minutes

**Total Phase 3:** 10-13 hours

---

## Technical Implementation Examples

### Structured Output (Issue #2)
```python
# In llm_client.py or wrapper:
async def generate_structured(
    self,
    prompt: str,
    system_prompt: str,
    response_model: Type[BaseModel],
) -> BaseModel:
    """Generate structured output using JSON schema."""
    schema = response_model.model_json_schema()

    # Use Anthropic's tools/JSON mode or similar
    response = await self.client.messages.create(
        model=self.model_name,
        messages=[{"role": "user", "content": prompt}],
        system=system_prompt,
        tools=[{
            "name": "provide_structured_output",
            "description": "Provide structured output matching the schema",
            "input_schema": schema,
        }],
        tool_choice={"type": "tool", "name": "provide_structured_output"},
    )

    # Parse tool call result as Pydantic model
    tool_result = response.content[0].input
    return response_model(**tool_result)

# In think.py:
response = await llm_client.generate_structured(
    prompt=prompt,
    system_prompt=system_prompt,
    response_model=ThinkOutput,
)
# response is already a validated ThinkOutput object
```

### External Prompt (Issue #1)
```python
# In data/prompts/think.md:
"""
You are a strategic thinker analyzing approaches to reach ideal state.

## Your Task
Generate exactly 2-3 distinct approaches, each with:
- Clear strategy and implementation steps
- Explicit trade-offs (what we gain vs what we give up)
- Known risks with likelihood and impact
- Expected success probability

## Output Format
APPROACH 1: [Name]
STRATEGY: [Core strategy]
STEPS:
  1. [Step 1]
  2. [Step 2]
TRADE-OFFS:
  - [Aspect]: Gain [X] but lose [Y] (severity: low/medium/high)
RISKS:
  - [Risk]: likelihood=low/medium/high, impact=low/medium/high
SUCCESS_PROBABILITY: [0.0-1.0]
...

Focus on feasibility, measurable outcomes, and learning from previous iterations.
"""

# In prompts.py:
def get_think_prompt(self) -> str:
    """Get the system prompt for THINK phase."""
    return self._load_prompt("think.md")

# In think.py:
system_prompt = prompt_templates.get_think_prompt()
```

---

## Success Criteria

**Phase 1 Complete When:**
- [ ] System prompt externalized to data/prompts/think.md
- [ ] LLM uses structured output (no text parsing needed)
- [ ] Approach validation rejects/improves low-quality outputs
- [ ] Prompt includes tools, constraints, dependencies context
- [ ] Parse success rate > 95%

**Phase 2 Complete When:**
- [ ] Selection algorithm considers risks, trade-offs, diversity
- [ ] No generic placeholder approaches generated
- [ ] Comparison matrix helps PLAN node make informed choices
- [ ] Error handling distinguishes error types and retries appropriately
- [ ] Recommended approach succeeds > 70% of the time

**Phase 3 Complete When:**
- [ ] Meta-learning tracks and uses approach type success rates
- [ ] Prompt building uses template system
- [ ] Comprehensive unit test coverage (>80%)
- [ ] Code is clean with no unused imports
- [ ] Historical success data improves probability estimates

---

## Files to Modify

1. **app/progenitor/nodes/think.py** - Main implementation
2. **app/progenitor/llm/prompts.py** - Add get_think_prompt()
3. **app/progenitor/llm/client.py** - Add generate_structured() (if not exists)
4. **data/prompts/think.md** - New file for system prompt
5. **app/progenitor/state/models.py** - May need to add approach_history tracking
6. **tests/progenitor/test_think.py** - New/updated unit tests

---

## Risk Assessment

**High Risk:**
- Structured output requires LLM client changes (may need llm_client.generate_structured())
- Meta-learning requires persistent storage design decisions

**Medium Risk:**
- Validation might be too strict initially (needs tuning)
- Selection algorithm changes might affect existing behavior

**Low Risk:**
- External prompts (straightforward refactor)
- Code cleanup and testing

---

## Open Questions for Review

1. **Should we implement structured output or stick with improved parsing?**
   - Structured output is better long-term but requires client changes
   - Enhanced parsing is quicker but still fragile

2. **How should we store approach success history?**
   - In state.json (ephemeral, per-task)
   - In database (persistent, cross-task)
   - In separate analytics file

3. **What's the right balance in selection algorithm between exploration and exploitation?**
   - Always use highest probability (exploitation)
   - Sometimes try lower-probability novel approaches (exploration)
   - Make it configurable?

4. **Should approach validation be strict (fail fast) or lenient (warn but continue)?**
   - Strict: Better quality but might block valid approaches
   - Lenient: More permissive but allows low-quality approaches through

5. **Do we want THINK to suggest entirely new approaches or build on previous ones?**
   - Fresh approaches each iteration
   - Incremental refinement of previous approaches
   - Hybrid (some new, some refined)

---

## Next Steps

1. **Review this plan** - Confirm priorities and approach
2. **Answer open questions** - Make design decisions
3. **Start Phase 1** - Focus on robustness and foundation
4. **Test incrementally** - Validate each change improves quality
5. **Measure impact** - Track success rates before and after changes

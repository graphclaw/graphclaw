# Tool Use Correctness Rubric

Score the agent's tool use from 0.0 to 1.0.

## Criteria

### 1.0 — Correct and complete
- Called the correct tool(s) for the request
- Tool arguments are accurate and complete (no placeholder values)
- Did not call unnecessary tools
- Handled tool results correctly in the response

### 0.7–0.9 — Mostly correct
- Called the right tool(s) but with minor argument issues
- May have called one extra unnecessary tool
- Tool result was used in the response

### 0.4–0.6 — Partially correct
- Called a plausible but wrong tool (e.g., graph_read when graph_write needed)
- Correct tool called but with significantly wrong arguments
- Tool result was not integrated into response

### 0.0–0.3 — Incorrect
- No tools called when tools were required
- Called a tool that doesn't exist or is out of scope
- Arguments are completely wrong
- Performed the action in plain text instead of via tool

## Notes
- For delegation scenarios: the orchestrator must call `skill.invoke` not answer inline. Score ≤ 0.3 if it answers inline.
- For read operations: `graph_read` or `score_read` must be called; data from memory alone = ≤ 0.5.
- For write operations: `graph_write` must be called with correct entity type and payload.

# Helpfulness Rubric

Score the agent's response from 0.0 to 1.0 on how helpful it was to the user.

## Criteria

### 1.0 — Excellent
- Directly addresses the user's request
- Uses available tools to provide real data (not hallucinated)
- Response is concise and actionable
- Proactively mentions relevant follow-up actions

### 0.7–0.9 — Good
- Addresses the core request
- Uses tools correctly but may miss a follow-up opportunity
- Response is clear but slightly verbose

### 0.4–0.6 — Mediocre
- Partially addresses the request
- Describes what it would do instead of doing it
- Uses incorrect tools or incorrect parameters

### 0.0–0.3 — Poor
- Ignores or misunderstands the request
- Hallucinates data instead of reading from graph
- Provides generic unhelpful response
- Asks unnecessary clarifying questions for clear requests

## Notes
- A response that correctly delegates to a skill and returns the skill output should score 0.9+.
- A response that answers a tool-requiring question purely from memory should score ≤ 0.5.

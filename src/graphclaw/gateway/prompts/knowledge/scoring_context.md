# Scoring Context

## 7-Factor Scoring System
Each task is scored 0.0–1.0 on seven weighted factors.
The final score determines rank in the action queue.

| Factor | Default Weight | Description |
|--------|---------------|-------------|
| timeline_urgency (W1) | 0.30 | How close is the deadline? Exponential decay as deadline approaches. |
| dependency_weight (W2) | 0.20 | How many tasks depend on this one? More dependents = higher score. |
| critical_path (W3) | 0.20 | Is this on the critical path? Blocking chain analysis. |
| blocker (W4) | 0.15 | Is this blocking other tasks? Direct blockers score highest. |
| human_override (W5) | 0.05 | Has the user manually flagged this as urgent? |
| resource_risk (W6) | 0.05 | Is the assigned resource overloaded or unavailable? |
| constraint_pressure (W7) | 0.05 | Are there active constraints applying pressure? |

## What Affects Priority
- Tasks with an imminent deadline and many dependents rank highest.
- BLOCKED tasks rank lower (can't act on them) unless the blocker is resolvable.
- FOLLOW_UP tasks escalate in score as their follow-up deadline passes.
- Human overrides (W5) temporarily boost a task's score for one cycle.

## Action Queue
The action queue shows tasks sorted by final_score descending.
Rank 1 = most urgent action the agent should take next.
The top-5 tasks appear in the system prompt graph summary each session.

## User Customisation
Users can adjust W1-W7 weights in their scoring_weights.json to reflect their priorities.
A user who values deadlines over dependencies would raise W1 and lower W2.

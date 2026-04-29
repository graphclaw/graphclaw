# Node Creation Rules

Use these rules to decide which task type to create.

## Task Types

| Type | When to create |
|------|---------------|
| ATOMIC | A single discrete step that one person or agent can complete directly. No sub-tasks. |
| COMPOSITE | A complex task with multiple sub-tasks. Create child ATOMICs under it. |
| FOLLOW_UP | Waiting for an external party to respond or act. Has a contact and a follow-up deadline. |
| RECURRING | A repeating pattern (daily standups, weekly reviews). Has a recurrence_pattern. |
| DELEGATED | Explicitly handed off to another person or team. Has a delegated_to field. |
| APPROVAL | Waiting for a formal sign-off or decision from a stakeholder. |
| MILESTONE | A key checkpoint or target date in a project. No work items — just a marker. |
| REVIEW | A task to review output or progress (code review, document review). |
| DECISION | A task where a choice must be made before work can continue. |
| CHECKIN | A scheduled check-in or progress review with another person. |
| RESEARCH | Gathering information or investigating something before deciding. |

## Goal vs Task
- Create a GOAL when the user is expressing a desired outcome ("I want to launch the API").
- Create TASKs to represent the concrete steps needed to reach that goal.
- Wire tasks to goals with PART_OF edges.

## Bottom-Up Goal Inference
If the user describes work without stating an explicit goal, infer the implied goal:
1. What outcome does this work lead to?
2. Create a GOAL with that outcome as the title.
3. Create the user's described work as tasks under that goal.

# Follow-Up Timing Rules

## FOLLOW_UP Task Behaviour
A FOLLOW_UP task tracks "waiting for X from Y" situations.
It has a contact, a follow-up deadline, and an urgency escalation schedule.

## Default Escalation by Domain

| Domain | First follow-up | Second follow-up | Escalate |
|--------|----------------|-----------------|---------|
| Business/Contract | 3 days | 7 days | 14 days |
| Legal/Compliance | 5 days | 10 days | 21 days |
| Internal team | 1 day | 3 days | 7 days |
| Vendor/Supplier | 5 days | 10 days | 21 days |
| Personal | 7 days | 14 days | 30 days |

## Urgency Escalation
- Day 0: FOLLOW_UP created, initial message sent.
- First follow-up: Gentle nudge ("Just checking in...").
- Second follow-up: More direct ("Haven't heard back — is there a blocker?").
- Escalate: Involve manager or escalation contact; bump task priority to P1.

## When to Create a FOLLOW_UP Task
- User sends an email, Slack message, or request and is waiting for a response.
- A task was delegated to an external party and no confirmation received.
- A decision was requested from a stakeholder.

## Closing FOLLOW_UP Tasks
- When the contact responds: transition to COMPLETE, spawn an ATOMIC task if action is needed.
- When the contact confirms completion: transition to COMPLETE.
- When the request is no longer needed: transition to CANCELLED.

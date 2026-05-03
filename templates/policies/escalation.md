---
fail_mode: closed
escalate_on_deadline_miss: true
escalate_on_blocked_task: true
interrupt_threshold: 0.8
quiet_hours_escalate: false
---
# Escalation Policy

Controls when the agent interrupts you with a notification.

## Escalate immediately

- Task deadline missed (escalate_on_deadline_miss = true)
- Task blocked by an external dependency (escalate_on_blocked_task = true)
- Scoring priority score exceeds **0.8** (interrupt_threshold)

## Do not interrupt during quiet hours

quiet_hours_escalate = false means alerts wait until your working hours begin.

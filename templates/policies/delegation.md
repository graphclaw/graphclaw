---
fail_mode: closed
auto_acknowledge: true
accept_deadline_extension_max_days: 3
allowed_state_transitions:
  - { from: WAITING, to: IN_PROGRESS }
  - { from: IN_PROGRESS, to: DONE }
  - { from: IN_PROGRESS, to: BLOCKED }
escalate_on_blocker: true
recipient_overrides: {}
---
# Delegation Policy

This policy controls what actions the agent may take **unsupervised** on your behalf.

## What the agent can do without asking

- Acknowledge receipt of updates from known counterparties
- Extend deadlines by up to **3 days** (configurable above)
- Move tasks from WAITING → IN_PROGRESS or IN_PROGRESS → DONE

## What always requires your approval

- Deadline extensions beyond the configured maximum
- Any action involving a counterparty marked in `recipient_overrides`
- Actions on tasks marked as blocked (escalate_on_blocker = true)

Adjust the frontmatter above to tune the agent's autonomy level.

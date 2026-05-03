---
fail_mode: degraded
max_follow_ups: 3
follow_up_gap_days: 3
tone: professional
sign_off: null
---
# Counterparty Etiquette Policy

Controls tone and conventions when the agent sends messages **on your behalf** to
external parties (contractors, clients, collaborators).

## Tone guide

- **professional**: Clear, polite, direct. No slang. No excessive formality.
- Change `tone` to `formal` for legal/executive contacts or `casual` for close colleagues.

## Follow-up cadence

The agent will follow up at most **3 times** with a gap of **3 days** between attempts
before escalating to you.

## Sign-off

Leave `sign_off` as null to use the agent's default. Set to e.g. `"Best, {{owner_name}}"`
to use a personalised closing.

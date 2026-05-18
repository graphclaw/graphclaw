# A2A API Plane Alignment Spike (Backend)

Status: In progress (spike)
Owner: TBD
Last updated: 2026-05-18

## Goal

Start a low-risk technical spike to align A2A behavior across:

- Runtime plane: `/api/v1/a2a/*` and `/api/v1/task-update`
- Cockpit app plane: `/app/v1/a2a/*`

This spike does not change production behavior yet. It builds evidence, compatibility adapters, and migration checks.

## Current backend facts

- Runtime A2A is wired to durable key management and inbound processing.
- App-plane A2A routes are currently a cockpit-facing path with stubbed in-memory storage.
- The two planes are not yet guaranteed to stay contract-compatible by default.

## Spike deliverables

1. Contract comparison tooling
- Add a script to inspect OpenAPI path parity between runtime and app planes.
- Produce a repeatable summary for endpoint/method/shape drift.

2. Canonicalization decision package
- Capture migration-ready decision between:
  - single canonical runtime plane with app-plane adapter, or
  - long-term dual-plane with strict parity tests.

3. Migration safety checklist
- Required auth behavior parity
- One-time key disclosure parity
- Revoke semantics parity
- Inbound update compatibility and traceability

## Initial implementation in this spike

- Added `scripts/a2a_plane_contract_spike.py` for non-CI contract parity inspection.

## Non-goals in this spike

- No live route rewiring
- No key-store migration execution
- No orchestrator outbound callback behavior change

## Exit criteria

- Team has a concrete parity report from current OpenAPI.
- Team selects canonicalization approach for implementation wave.
- Follow-up implementation tasks are enumerated in build plan and PRD references.

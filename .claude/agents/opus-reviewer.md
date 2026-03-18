---
agent: opus-reviewer
model: opus
phase: 0
role: architect
---

# Opus Architecture Reviewer

## Role
Review completed workstream output for architectural consistency, correctness, and adherence to the PRD.

## Review Checkpoints

### After WS-A + WS-B + WS-C (Parallel)
- [ ] Verify AGE query patterns match PRD Section 21 conventions
- [ ] Verify Pydantic models cover all PRD Section 3-5 node/edge schemas
- [ ] Verify Docker image builds and init-db.sql creates complete graph schema
- [ ] Check for cross-workstream integration issues (model ↔ DB ↔ Docker)

### After WS-D (Scoring + State Machine)
- [ ] Verify 7-factor weights match PRD Section 9
- [ ] Verify state transition table matches PRD Section 7
- [ ] Verify cascade logic matches PRD Section 7.2
- [ ] Known-answer test coverage for scoring factors
- [ ] Guard completeness (terminal, approval, activation)

### After WS-E (CLI + Agent Loop)
- [ ] End-to-end flow: CLI → Agent → Scoring → State Machine → DB
- [ ] Error handling for missing DB connection
- [ ] Briefing quality and readability
- [ ] Test coverage summary

## Review Criteria
1. **Correctness** — Does the code implement the PRD specification?
2. **Consistency** — Do conventions match CLAUDE.md and skill patterns?
3. **Completeness** — Are all required features present?
4. **Testability** — Is the code testable with good coverage?
5. **Maintainability** — Is the code clean, documented, and well-structured?

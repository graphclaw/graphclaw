---
name: code-security-review
description: >
  Review Python code for security vulnerabilities, injection risks, authentication gaps,
  and data exposure. Specialized for FastAPI + PostgreSQL + Apache AGE stack.
  Use when auditing code for security, reviewing database queries for injection,
  checking secrets handling, or assessing attack surface. Triggers on:
  "security review", "vulnerability", "injection", "secrets", "attack surface", "OWASP".
---

# Code Security Review

## Review Procedure

### 1. Injection Vulnerabilities

**SQL / Cypher Injection** (CRITICAL for Apache AGE):
- AGE Cypher queries inside `$$` blocks cannot use parameterized queries
- All string interpolation MUST use the `_escape()` helper
- Verify every f-string in Cypher queries escapes user-controlled values
- Check for raw `.format()` or `%` string formatting with user input

Audit pattern:
```python
# SAFE — escaped
eid = _escape(node_id)
f"MATCH (n {{id: '{eid}'}})"

# UNSAFE — raw interpolation
f"MATCH (n {{id: '{node_id}'}})"
```

Flag:
- Any user-controlled value in Cypher without `_escape()`
- SQL queries outside AGE that don't use parameterized queries
- Dynamic label/type names from user input without allowlist validation

### 2. Authentication & Authorization

- OAuth token validation on every API endpoint
- JWT expiry enforced (RS256, 15-minute expiry per CLAUDE.md)
- No hardcoded credentials in source code
- Environment variables used for all secrets
- RBAC checks before data access operations

Flag:
- Endpoints without auth decorators
- JWT tokens with excessive expiry
- Hardcoded API keys, passwords, or tokens
- Missing user-scoping on data queries (user A accessing user B's data)

### 3. Secrets & Sensitive Data

- No secrets in source code, config files, or logs
- `.env` files in `.gitignore`
- Secrets loaded via `SecretsClient` abstraction
- Sensitive fields excluded from `model_dump()` / serialization
- No PII in log messages

Scan for:
- Patterns: `password`, `secret`, `token`, `api_key`, `private_key`
- Hardcoded strings that look like credentials
- Base64-encoded strings in source
- Logging of request bodies that may contain auth tokens

### 4. Input Validation

- All external input validated via Pydantic models
- String lengths bounded (prevent DoS via oversized payloads)
- Enum values validated against allowlists
- File paths sanitized (no path traversal)
- Numeric ranges validated

Flag:
- Raw `dict` used for API input instead of Pydantic model
- Unbounded string fields
- Missing validation on path parameters
- Integer overflow potential in scoring calculations

### 5. Dependency & Configuration Security

- No known vulnerable dependencies (check version constraints)
- Docker images use specific tags, not `latest` in production
- Non-root user in Dockerfile
- Health check endpoints don't expose sensitive info
- CORS configured restrictively

Flag:
- `*` in CORS allowed origins
- Docker running as root
- Dependencies without version pinning
- Debug mode enabled in configuration

### 6. Data Exposure

- API responses don't leak internal IDs, stack traces, or system info
- Error messages are generic to clients, detailed to logs
- Database connection strings not in error responses
- Graph traversal queries bounded (prevent full-graph dump)

Flag:
- Stack traces returned in API responses
- Internal node IDs exposed to clients
- Unbounded graph traversal depth
- Verbose error messages with system paths

### 7. Output Format

```markdown
## Security Review: `<module>`

### Risk Level: LOW / MEDIUM / HIGH / CRITICAL

### Findings

| # | Severity | Category | File:Line | Description | Remediation |
|---|----------|----------|-----------|-------------|-------------|
| 1 | CRITICAL | Injection | file.py:42 | ... | ... |

### Positive Security Practices
- <practices already in place>

### Recommendations
1. <prioritized security improvement>
```

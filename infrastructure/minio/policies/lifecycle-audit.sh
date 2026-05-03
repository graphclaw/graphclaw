#!/usr/bin/env bash
# lifecycle-audit.sh — Wave 0 MinIO lifecycle rule audit (FR-DEL-008)
#
# Usage:
#   ./lifecycle-audit.sh [MINIO_ALIAS] [BUCKET]
#
# Arguments:
#   MINIO_ALIAS   mc alias name (default: local)
#   BUCKET        bucket to audit (default: graphclaw)
#
# Exit codes:
#   0 — no forbidden lifecycle rules found
#   1 — lifecycle rules on forbidden prefixes detected (FATAL — do not start gateway)
#   2 — mc (MinIO client) not found or alias not configured
#
# Description:
#   Queries the MinIO bucket lifecycle configuration using the mc CLI and
#   checks whether any rules target user-data prefixes (users/, tasks/, goals/,
#   attachments/, agent/).  Prints a human-readable report and exits non-zero
#   if any violations are found.
#
# Required:
#   mc (MinIO Client) configured with the target alias.
#   mc alias set local http://localhost:9000 <access_key> <secret_key>
#
# Run in CI:
#   MINIO_ALIAS=local BUCKET=graphclaw ./infrastructure/minio/policies/lifecycle-audit.sh

set -euo pipefail

MINIO_ALIAS="${1:-local}"
BUCKET="${2:-graphclaw}"

# Prefixes that must NEVER have lifecycle (auto-expiry / delete) rules.
FORBIDDEN_PREFIXES=(
    "users/"
    "tasks/"
    "goals/"
    "attachments/"
    "agent/"
)

# Ensure mc is available.
if ! command -v mc &>/dev/null; then
    echo "ERROR: mc (MinIO Client) not found in PATH." >&2
    echo "Install: https://min.io/docs/minio/linux/reference/minio-mc.html" >&2
    exit 2
fi

# Check the alias is configured.
if ! mc alias ls "${MINIO_ALIAS}" &>/dev/null; then
    echo "ERROR: mc alias '${MINIO_ALIAS}' not configured." >&2
    echo "Run: mc alias set ${MINIO_ALIAS} http://localhost:9000 <access_key> <secret_key>" >&2
    exit 2
fi

echo "Auditing lifecycle rules on ${MINIO_ALIAS}/${BUCKET} ..."

# Get lifecycle rules as JSON.
RULES_JSON="$(mc ilm ls --json "${MINIO_ALIAS}/${BUCKET}" 2>/dev/null || echo '{"rules":[]}')"

if [ -z "${RULES_JSON}" ] || [ "${RULES_JSON}" = '{"rules":[]}' ]; then
    echo "OK: No lifecycle rules configured on ${MINIO_ALIAS}/${BUCKET}."
    exit 0
fi

VIOLATIONS=0

for PREFIX in "${FORBIDDEN_PREFIXES[@]}"; do
    # Use jq to find any rule whose prefix starts with or contains the forbidden prefix.
    MATCHES="$(echo "${RULES_JSON}" | jq -r --arg p "${PREFIX}" \
        '[.rules[]? | select(.filter.prefix? // "" | startswith($p) or ($p | startswith(. // "")))] | length' 2>/dev/null || echo "0")"

    if [ "${MATCHES}" != "0" ] && [ "${MATCHES}" != "" ]; then
        echo "VIOLATION: Lifecycle rule(s) found on forbidden prefix '${PREFIX}'" >&2
        echo "${RULES_JSON}" | jq -r --arg p "${PREFIX}" \
            '.rules[]? | select(.filter.prefix? // "" | startswith($p) or ($p | startswith(. // ""))) | "  Rule ID: \(.id // "?") | Status: \(.status // "?") | Prefix: \(.filter.prefix // "?")"' 2>/dev/null || true
        VIOLATIONS=$((VIOLATIONS + 1))
    fi
done

if [ "${VIOLATIONS}" -gt 0 ]; then
    echo "" >&2
    echo "FATAL: ${VIOLATIONS} lifecycle violation(s) detected." >&2
    echo "Remove these lifecycle rules before starting the GraphClaw gateway." >&2
    echo "This check enforces FR-DEL-008 (Wave 0 No-Delete principle)." >&2
    exit 1
fi

echo "OK: No lifecycle rules on forbidden prefixes. Gateway may start safely."
exit 0

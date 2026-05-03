"""Wave 1 E2E validation script.

Tests live gateway at localhost:8000.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

BASE = "http://localhost:8000"
RESULTS: list[str] = []


def call(
    method: str,
    path: str,
    body: dict | None = None,
    token: str | None = None,
) -> tuple[int, dict]:
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body else None
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def check(name: str, condition: bool, detail: str = "") -> None:
    mark = "PASS" if condition else "FAIL"
    msg = f"[{mark}] {name}" + (f": {detail}" if detail else "")
    RESULTS.append(msg)
    print(msg)


def main() -> None:
    # ── Auth ──────────────────────────────────────────────────────────────────
    status, resp = call("POST", "/auth/dev-token", {"user_id": "USER-dev-001", "role": "ADMIN"})
    check("dev-token issues 200", status == 200, f"status={status}")
    token = resp.get("access_token", "")
    check("dev-token returns token", bool(token))

    # ── Policy PUT ────────────────────────────────────────────────────────────
    status, resp = call(
        "PUT",
        "/app/v1/agents/main/policies/delegation",
        {
            "frontmatter": {
                "fail_mode": "closed",
                "auto_acknowledge": True,
                "accept_deadline_extension_max_days": 7,
                "escalate_on_blocker": True,
            },
            "body": "# Delegation Policy\n\nE2E test policy.",
        },
        token,
    )
    check("PUT delegation returns 200", status == 200, f"status={status}")
    version = resp.get("version", "")
    check("PUT delegation returns etag", len(version) == 32, f"version={version}")

    # ── Policy GET ────────────────────────────────────────────────────────────
    status, resp = call("GET", "/app/v1/agents/main/policies/delegation", token=token)
    check("GET delegation returns 200", status == 200, f"status={status}")
    fm = resp.get("frontmatter", {})
    check("GET delegation fail_mode=closed", fm.get("fail_mode") == "closed")
    check("GET delegation deadline=7", fm.get("accept_deadline_extension_max_days") == 7)
    check("GET delegation body present", "E2E test policy" in resp.get("body", ""))

    # ── GET missing closed policy → 404 ──────────────────────────────────────
    status, resp = call("GET", "/app/v1/agents/main/policies/escalation", token=token)
    check("GET missing escalation returns 404", status == 404, f"status={status}")

    # ── GET degraded default → 200 ────────────────────────────────────────────
    status, resp = call("GET", "/app/v1/agents/main/policies/reply_tone", token=token)
    check("GET missing reply_tone (degraded) returns 200", status == 200, f"status={status}")

    # ── PUT bad frontmatter → 422 ─────────────────────────────────────────────
    status, _ = call(
        "PUT",
        "/app/v1/agents/main/policies/delegation",
        {"frontmatter": {"fail_mode": "not_valid"}, "body": ""},
        token,
    )
    check("PUT bad frontmatter returns 422", status == 422, f"status={status}")

    # ── PUT unknown policy → 422 ──────────────────────────────────────────────
    status, _ = call(
        "PUT",
        "/app/v1/agents/main/policies/nonexistent",
        {"frontmatter": {}, "body": ""},
        token,
    )
    check("PUT unknown policy returns 422", status == 422, f"status={status}")

    # ── Version mismatch → 409 ────────────────────────────────────────────────
    status, _ = call(
        "PUT",
        "/app/v1/agents/main/policies/delegation",
        {
            "frontmatter": {"fail_mode": "closed"},
            "body": "Updated",
            "expected_version": "wrong_version_12345678901234567",
        },
        token,
    )
    check("PUT wrong expected_version returns 409", status == 409, f"status={status}")

    # ── Summary ──────────────────────────────────────────────────────────────
    passed = sum(1 for r in RESULTS if r.startswith("[PASS]"))
    failed = sum(1 for r in RESULTS if r.startswith("[FAIL]"))
    print(f"\nWave 1 E2E: {passed} passed, {failed} failed")
    if failed > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

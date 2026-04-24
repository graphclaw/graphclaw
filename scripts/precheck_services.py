"""Run DB/Redis/MinIO readiness checks for GraphClaw integration tests.

Usage:
    python scripts/precheck_services.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.integration_precheck import run_services_precheck  # noqa: E402


def main() -> int:
    ok, details = run_services_precheck()
    if ok:
        print("PASS: DB, Redis, and storage services are ready.")
        return 0

    print("FAIL: integration services precheck failed:")
    for entry in details:
        print(f"- {entry}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

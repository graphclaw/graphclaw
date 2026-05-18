#!/usr/bin/env python3
# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Manual A2A API-plane parity probe for local development.

This script is a spike artifact, not a CI test.
It prints runtime-plane vs app-plane endpoint coverage from OpenAPI.

Usage:
    python scripts/a2a_plane_contract_spike.py
    python scripts/a2a_plane_contract_spike.py --openapi-url http://localhost:8000/openapi.json
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from typing import Any

DEFAULT_OPENAPI_URL = "http://localhost:8000/openapi.json"

RUNTIME_PATHS = {
    "/api/v1/a2a/agents",
    "/api/v1/a2a/agents/{key_id}",
    "/api/v1/a2a/agents/{key_id}/rotate",
    "/api/v1/task-update",
}

APP_PATHS = {
    "/app/v1/a2a/agents",
    "/app/v1/a2a/agents/{key_id}",
    "/app/v1/a2a/agents/{key_id}/rotate",
}


def fetch_openapi(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=20) as response:  # noqa: S310
        payload = response.read().decode("utf-8")
    return json.loads(payload)


def extract_methods(paths: dict[str, Any], target_path: str) -> list[str]:
    entry = paths.get(target_path, {})
    if not isinstance(entry, dict):
        return []
    methods = [method.upper() for method in entry if method.lower() in {"get", "post", "put", "patch", "delete"}]
    return sorted(methods)


def print_plane_summary(paths: dict[str, Any], title: str, target_paths: set[str]) -> None:
    print(f"\n== {title} ==")
    for path in sorted(target_paths):
        methods = extract_methods(paths, path)
        if methods:
            print(f"{path:<45} methods: {', '.join(methods)}")
        else:
            print(f"{path:<45} methods: (missing)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe A2A OpenAPI parity across runtime/app planes")
    parser.add_argument("--openapi-url", default=DEFAULT_OPENAPI_URL, help="OpenAPI JSON URL")
    args = parser.parse_args()

    try:
        spec = fetch_openapi(args.openapi_url)
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to fetch OpenAPI from {args.openapi_url}: {exc}")
        return

    paths = spec.get("paths", {})
    if not isinstance(paths, dict):
        print("OpenAPI payload does not contain a valid 'paths' map")
        return

    print(f"OpenAPI source: {args.openapi_url}")
    print_plane_summary(paths, "Runtime plane", RUNTIME_PATHS)
    print_plane_summary(paths, "App plane", APP_PATHS)

    runtime_missing = [path for path in sorted(RUNTIME_PATHS) if not extract_methods(paths, path)]
    app_missing = [path for path in sorted(APP_PATHS) if not extract_methods(paths, path)]

    print("\n== Quick parity notes ==")
    if runtime_missing:
        print("Runtime plane missing paths:")
        for path in runtime_missing:
            print(f"  - {path}")
    else:
        print("Runtime plane: all expected spike paths present")

    if app_missing:
        print("App plane missing paths:")
        for path in app_missing:
            print(f"  - {path}")
    else:
        print("App plane: all expected spike paths present")

    print("\nThis script is informational only and intentionally does not assert/fail CI.")


if __name__ == "__main__":
    main()

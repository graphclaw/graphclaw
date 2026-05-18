# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
from pathlib import Path

from graphclaw.agent.activity_formatter import format_event


def test_activity_formatter_matches_fixture_cases() -> None:
    fixture_path = Path(__file__).resolve().parents[1] / "fixtures" / "event_formatter_cases.json"
    cases = json.loads(fixture_path.read_text(encoding="utf-8"))

    assert isinstance(cases, list)
    for case in cases:
        record = case["record"]
        expected = case["expected"]
        assert format_event(record) == expected

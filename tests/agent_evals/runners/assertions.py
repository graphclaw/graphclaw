# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""
Behavioral assertion vocabulary for agent eval turn results.

Each assertion function maps one TurnAssert spec entry to a pytest-compatible
assertion. Failures include human-readable messages so test output is self-explanatory.
"""
from __future__ import annotations

import re

from .chat_session import TurnAssert, TurnResult


def _tool_names(result: TurnResult) -> list[str]:
    return [tc.get("name", tc.get("type", "")) for tc in result.tool_calls]


def _args_subset_match(actual_args: dict, expected: dict) -> bool:
    """True if every key in expected appears in actual with equal value (nested)."""
    for key, expected_val in expected.items():
        if key not in actual_args:
            return False
        actual_val = actual_args[key]
        if isinstance(expected_val, dict) and isinstance(actual_val, dict):
            if not _args_subset_match(actual_val, expected_val):
                return False
        elif actual_val != expected_val:
            return False
    return True


def run_turn_assertions(assert_specs: list[TurnAssert], result: TurnResult) -> None:
    """
    Execute every assertion in assert_specs against the turn result.
    Raises AssertionError with a descriptive message on the first failure.
    """
    tool_names = _tool_names(result)

    for spec in assert_specs:
        # ── tool_called ──────────────────────────────────────────────────────
        if spec.tool_called is not None:
            assert spec.tool_called in tool_names, (
                f"Expected tool_called={spec.tool_called!r} but got tools: {tool_names}\n"
                f"Agent response: {result.agent[:200]}"
            )

        # ── tool_not_called ──────────────────────────────────────────────────
        if spec.tool_not_called is not None:
            assert spec.tool_not_called not in tool_names, (
                f"Expected tool_not_called={spec.tool_not_called!r} but it was called.\n"
                f"All tools: {tool_names}"
            )

        # ── tool_args_match ──────────────────────────────────────────────────
        if spec.tool_args_match:
            # Find the tool call matching the args
            matched = False
            for tc in result.tool_calls:
                args = tc.get("args", tc.get("input", {}))
                if _args_subset_match(args, spec.tool_args_match):
                    matched = True
                    break
            assert matched, (
                f"No tool call matched tool_args_match={spec.tool_args_match}\n"
                f"Actual tool calls: {result.tool_calls}"
            )

        # ── response_contains ────────────────────────────────────────────────
        for fragment in spec.response_contains:
            assert fragment.lower() in result.agent.lower(), (
                f"Expected response to contain {fragment!r}\n"
                f"Response: {result.agent[:300]}"
            )

        # ── response_does_not_contain ─────────────────────────────────────────
        for fragment in spec.response_does_not_contain:
            assert fragment.lower() not in result.agent.lower(), (
                f"Expected response NOT to contain {fragment!r}\n"
                f"Response: {result.agent[:300]}"
            )

        # ── response_matches_regex ────────────────────────────────────────────
        if spec.response_matches_regex:
            assert re.search(spec.response_matches_regex, result.agent), (
                f"Response did not match regex {spec.response_matches_regex!r}\n"
                f"Response: {result.agent[:300]}"
            )

        # ── latency_ms_under ─────────────────────────────────────────────────
        if spec.latency_ms_under is not None:
            assert result.latency_ms < spec.latency_ms_under, (
                f"Latency {result.latency_ms:.0f}ms exceeded {spec.latency_ms_under}ms"
            )

        # ── cost_usd_under ────────────────────────────────────────────────────
        if spec.cost_usd_under is not None:
            assert result.cost_usd < spec.cost_usd_under, (
                f"Turn cost ${result.cost_usd:.4f} exceeded ${spec.cost_usd_under}"
            )

# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Tests for integration services precheck helper."""

from __future__ import annotations

from tests import integration_precheck


def test_run_services_precheck_success(monkeypatch):
    monkeypatch.setattr(integration_precheck, "_check_database", lambda _dsn: None)
    monkeypatch.setattr(integration_precheck, "_check_redis", lambda _url: None)
    monkeypatch.setattr(
        integration_precheck,
        "_check_storage",
        lambda _endpoint, _bucket, _region, _key, _secret: None,
    )

    ok, details = integration_precheck.run_services_precheck()

    assert ok is True
    assert details == []


def test_run_services_precheck_collects_failures(monkeypatch):
    monkeypatch.setattr(integration_precheck, "_check_database", lambda _dsn: "db down")
    monkeypatch.setattr(integration_precheck, "_check_redis", lambda _url: None)
    monkeypatch.setattr(
        integration_precheck,
        "_check_storage",
        lambda _endpoint, _bucket, _region, _key, _secret: "minio down",
    )

    ok, details = integration_precheck.run_services_precheck()

    assert ok is False
    assert "db down" in details
    assert "minio down" in details

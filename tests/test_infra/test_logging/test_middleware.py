# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Tests for LoggingMiddleware."""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from graphclaw.infra.logging.middleware import LoggingMiddleware


@pytest.fixture
def app_with_middleware():
    app = FastAPI()
    app.add_middleware(LoggingMiddleware)

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


class TestLoggingMiddleware:
    def test_request_is_logged(self, app_with_middleware, caplog):
        with caplog.at_level(logging.INFO, logger="graphclaw.http"):
            with TestClient(app_with_middleware) as client:
                resp = client.get("/ping")
        assert resp.status_code == 200
        http_records = [r for r in caplog.records if r.name == "graphclaw.http"]
        assert len(http_records) == 1
        assert getattr(http_records[0], "event_type", None) == "http.request"
        assert getattr(http_records[0], "path", None) == "/ping"
        assert getattr(http_records[0], "status_code", None) == 200

    def test_health_endpoint_not_logged(self, app_with_middleware, caplog):
        with caplog.at_level(logging.INFO, logger="graphclaw.http"):
            with TestClient(app_with_middleware) as client:
                client.get("/health")
        http_records = [r for r in caplog.records if r.name == "graphclaw.http"]
        assert len(http_records) == 0

    def test_session_id_generated_when_absent(self, app_with_middleware, caplog):
        with caplog.at_level(logging.INFO, logger="graphclaw.http"):
            with TestClient(app_with_middleware) as client:
                client.get("/ping")
        http_records = [r for r in caplog.records if r.name == "graphclaw.http"]
        sid = getattr(http_records[0], "session_id", "")
        assert sid.startswith("SES-")

    def test_x_session_id_header_used(self, app_with_middleware, caplog):
        with caplog.at_level(logging.INFO, logger="graphclaw.http"):
            with TestClient(app_with_middleware) as client:
                client.get("/ping", headers={"X-Session-ID": "SES-custom-abc"})
        http_records = [r for r in caplog.records if r.name == "graphclaw.http"]
        assert getattr(http_records[0], "session_id", "") == "SES-custom-abc"

    def test_latency_ms_present(self, app_with_middleware, caplog):
        with caplog.at_level(logging.INFO, logger="graphclaw.http"):
            with TestClient(app_with_middleware) as client:
                client.get("/ping")
        http_records = [r for r in caplog.records if r.name == "graphclaw.http"]
        assert isinstance(getattr(http_records[0], "latency_ms", None), int)

# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Tests for graphclaw.gateway.models — TaskMatch, HealthStatus, EmailConfig."""

from __future__ import annotations

from graphclaw.gateway.models import EmailConfig, HealthStatus, TaskMatch
from graphclaw.models.enums import ConfidenceLevel, MatchedBy

# ---------------------------------------------------------------------------
# TaskMatch
# ---------------------------------------------------------------------------


class TestTaskMatchCreation:
    def test_task_match_creation(self):
        match = TaskMatch(
            task_id="TSK-AB-0001-ATM",
            matched_by=MatchedBy.TASK_ID,
            confidence=ConfidenceLevel.HIGH,
        )
        assert match.task_id == "TSK-AB-0001-ATM"
        assert match.matched_by == MatchedBy.TASK_ID
        assert match.confidence == ConfidenceLevel.HIGH

    def test_task_match_matched_text_defaults_to_empty(self):
        match = TaskMatch(
            task_id="TSK-CD-0002-ATM",
            matched_by=MatchedBy.VECTOR_SEARCH,
            confidence=ConfidenceLevel.MEDIUM,
        )
        assert match.matched_text == ""

    def test_task_match_with_matched_text(self):
        match = TaskMatch(
            task_id="TSK-EF-0003-ATM",
            matched_by=MatchedBy.VECTOR_SEARCH,
            confidence=ConfidenceLevel.LOW,
            matched_text="Please complete the report",
        )
        assert match.matched_text == "Please complete the report"

    def test_task_match_confidence_levels(self):
        for level in ConfidenceLevel:
            match = TaskMatch(
                task_id="TSK-GH-0004-ATM",
                matched_by=MatchedBy.TASK_ID,
                confidence=level,
            )
            assert match.confidence == level

    def test_task_match_serialization_roundtrip(self):
        original = TaskMatch(
            task_id="TSK-IJ-0005-ATM",
            matched_by=MatchedBy.VECTOR_SEARCH,
            confidence=ConfidenceLevel.HIGH,
            matched_text="some text",
        )
        restored = TaskMatch.model_validate_json(original.model_dump_json())
        assert restored.task_id == original.task_id
        assert restored.matched_by == original.matched_by
        assert restored.confidence == original.confidence
        assert restored.matched_text == original.matched_text


# ---------------------------------------------------------------------------
# HealthStatus
# ---------------------------------------------------------------------------


class TestHealthStatusDefaults:
    def test_health_status_defaults(self):
        status = HealthStatus()
        assert status.status == "ok"
        assert status.version == "0.1.0"
        assert status.services == {}

    def test_health_status_custom_values(self):
        status = HealthStatus(
            status="degraded",
            version="0.2.0",
            services={"db": "ok", "redis": "unavailable"},
        )
        assert status.status == "degraded"
        assert status.version == "0.2.0"
        assert status.services["redis"] == "unavailable"

    def test_health_status_serialization(self):
        status = HealthStatus(
            status="ready",
            services={"broker": "ok"},
        )
        data = status.model_dump()
        assert data["status"] == "ready"
        assert data["services"]["broker"] == "ok"

    def test_health_status_version_field(self):
        status = HealthStatus(version="1.0.0-rc1")
        assert status.version == "1.0.0-rc1"


# ---------------------------------------------------------------------------
# EmailConfig
# ---------------------------------------------------------------------------


class TestEmailConfigDefaults:
    def test_email_config_defaults(self):
        config = EmailConfig()
        assert config.imap_host == ""
        assert config.imap_port == 993
        assert config.smtp_host == ""
        assert config.smtp_port == 587
        assert config.username == ""
        assert config.password == ""
        assert config.poll_interval == 30.0
        assert config.enabled is False

    def test_email_config_custom_values(self):
        config = EmailConfig(
            imap_host="imap.example.com",
            imap_port=993,
            smtp_host="smtp.example.com",
            smtp_port=465,
            username="user@example.com",
            password="secret",
            poll_interval=60.0,
            enabled=True,
        )
        assert config.imap_host == "imap.example.com"
        assert config.smtp_port == 465
        assert config.enabled is True

    def test_email_config_enabled_false_by_default(self):
        config = EmailConfig(
            imap_host="imap.gmail.com",
            username="test@gmail.com",
            password="app-password",
        )
        assert config.enabled is False

    def test_email_config_serialization_roundtrip(self):
        original = EmailConfig(
            imap_host="imap.test.com",
            smtp_host="smtp.test.com",
            username="u@test.com",
            password="pw",
            enabled=True,
            poll_interval=15.0,
        )
        restored = EmailConfig.model_validate(original.model_dump())
        assert restored.imap_host == original.imap_host
        assert restored.username == original.username
        assert restored.poll_interval == original.poll_interval
        assert restored.enabled == original.enabled

# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Tests for graphclaw.gateway.channels.email.ses_receiver — SESEmailReceiver.

Description
-----------
Unit tests for the SES inbound email webhook receiver and the accompanying
infra helpers (SESConfig, build_ses_receipt_rule, LAMBDA_HANDLER_CODE).

Tests cover:
- HMAC-SHA256 signature verification (valid / invalid / no secret).
- handle_ses_notification with presigned URL (httpx mocked).
- handle_ses_notification fallback to constructed S3 URL.
- from_env construction from environment variables.
- SESConfig immutability (frozen dataclass).
- build_ses_receipt_rule output structure.
- LAMBDA_HANDLER_CODE content validation.
"""

from __future__ import annotations

import hashlib
import hmac
from email.message import EmailMessage
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from graphclaw.gateway.channels.email.ses_receiver import SESEmailReceiver
from graphclaw.gateway.schemas import InboundMessage
from infra.ses.config import SESConfig, build_ses_receipt_rule
from infra.ses.lambda_handler import LAMBDA_HANDLER_CODE

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_raw_email_bytes(
    from_addr: str = "sender@example.com",
    subject: str = "Hello from SES",
    body: str = "This is the email body.",
    message_id: str = "<ses-test-123@example.com>",
) -> bytes:
    """Build minimal RFC 2822 email bytes."""
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["Subject"] = subject
    msg["Message-ID"] = message_id
    msg["Date"] = "Thu, 01 Jun 2024 12:00:00 +0000"
    msg.set_content(body)
    return msg.as_bytes()


def _make_signature(secret: str, body: bytes) -> str:
    """Compute the expected HMAC-SHA256 hex digest."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Signature verification tests
# ---------------------------------------------------------------------------


class TestVerifyLambdaSignature:
    """Tests for SESEmailReceiver.verify_lambda_signature."""

    def test_verify_lambda_signature_valid(self) -> None:
        """Correct HMAC-SHA256 signature must return True."""
        secret = "supersecret"
        body = b'{"s3_key": "email/msg-abc"}'
        receiver = SESEmailReceiver(
            s3_bucket="test-bucket",
            lambda_shared_secret=secret,
        )
        sig = _make_signature(secret, body)
        assert receiver.verify_lambda_signature(body, sig) is True

    def test_verify_lambda_signature_invalid(self) -> None:
        """Wrong secret must return False."""
        body = b'{"s3_key": "email/msg-abc"}'
        receiver = SESEmailReceiver(
            s3_bucket="test-bucket",
            lambda_shared_secret="correct-secret",
        )
        wrong_sig = _make_signature("wrong-secret", body)
        assert receiver.verify_lambda_signature(body, wrong_sig) is False

    def test_verify_lambda_signature_no_secret(self) -> None:
        """Empty lambda_shared_secret must skip verification and return True (dev mode)."""
        receiver = SESEmailReceiver(
            s3_bucket="test-bucket",
            lambda_shared_secret="",
        )
        # Any signature (or empty string) should pass when no secret is configured
        assert receiver.verify_lambda_signature(b"body", "any-signature") is True
        assert receiver.verify_lambda_signature(b"body", "") is True


# ---------------------------------------------------------------------------
# handle_ses_notification tests
# ---------------------------------------------------------------------------


class TestHandleSesNotification:
    """Tests for SESEmailReceiver.handle_ses_notification."""

    @pytest.mark.asyncio
    async def test_handle_ses_notification_with_presigned_url(self) -> None:
        """Presigned URL in payload → download via httpx → InboundMessage returned."""
        raw_bytes = _make_raw_email_bytes()
        payload = {
            "s3_bucket": "graphclaw-inbound-email",
            "s3_key": "email/msg-123",
            "presigned_url": "https://s3.amazonaws.com/signed-url",
            "sns_message_id": "sns-abc",
            "recipient": "user@graphclaw.ai",
        }

        mock_response = MagicMock()
        mock_response.content = raw_bytes
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_async_client = MagicMock()
        mock_async_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_async_client.__aexit__ = AsyncMock(return_value=None)

        receiver = SESEmailReceiver(s3_bucket="graphclaw-inbound-email")

        with patch(
            "graphclaw.gateway.channels.email.ses_receiver.httpx.AsyncClient",
            return_value=mock_async_client,
        ):
            result = await receiver.handle_ses_notification(payload)

        assert result is not None
        assert isinstance(result, InboundMessage)
        assert result.channel == "email"
        assert result.sender == "sender@example.com"
        assert result.subject == "Hello from SES"
        # Verify the presigned URL was used (not the fallback path)
        mock_client.get.assert_called_once_with("https://s3.amazonaws.com/signed-url")

    @pytest.mark.asyncio
    async def test_handle_ses_notification_fallback_s3_url(self) -> None:
        """No presigned_url in payload → fallback S3 URL constructed correctly."""
        raw_bytes = _make_raw_email_bytes()
        payload = {
            "s3_bucket": "graphclaw-inbound-email",
            "s3_key": "email/msg-456",
            "sns_message_id": "sns-xyz",
            "recipient": "user@graphclaw.ai",
        }

        mock_response = MagicMock()
        mock_response.content = raw_bytes
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_async_client = MagicMock()
        mock_async_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_async_client.__aexit__ = AsyncMock(return_value=None)

        receiver = SESEmailReceiver(
            s3_bucket="graphclaw-inbound-email",
            aws_region="us-west-2",
        )

        with patch(
            "graphclaw.gateway.channels.email.ses_receiver.httpx.AsyncClient",
            return_value=mock_async_client,
        ):
            result = await receiver.handle_ses_notification(payload)

        assert result is not None
        assert isinstance(result, InboundMessage)
        # Verify the fallback URL was constructed with the correct region
        expected_url = "https://graphclaw-inbound-email.s3.us-west-2.amazonaws.com/email/msg-456"
        mock_client.get.assert_called_once_with(expected_url)


# ---------------------------------------------------------------------------
# from_env tests
# ---------------------------------------------------------------------------


class TestFromEnv:
    """Tests for SESEmailReceiver.from_env."""

    def test_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Environment variables must populate SESEmailReceiver fields correctly."""
        monkeypatch.setenv("SES_S3_BUCKET", "my-custom-bucket")
        monkeypatch.setenv("AWS_REGION", "eu-west-1")
        monkeypatch.setenv("SES_LAMBDA_SECRET", "my-secret")

        receiver = SESEmailReceiver.from_env()

        assert receiver.s3_bucket == "my-custom-bucket"
        assert receiver.aws_region == "eu-west-1"
        assert receiver.lambda_shared_secret == "my-secret"

    def test_from_env_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing env vars must fall back to documented defaults."""
        monkeypatch.delenv("SES_S3_BUCKET", raising=False)
        monkeypatch.delenv("AWS_REGION", raising=False)
        monkeypatch.delenv("SES_LAMBDA_SECRET", raising=False)

        receiver = SESEmailReceiver.from_env()

        assert receiver.s3_bucket == "graphclaw-inbound-email"
        assert receiver.aws_region == "us-east-1"
        assert receiver.lambda_shared_secret == ""


# ---------------------------------------------------------------------------
# SESConfig tests
# ---------------------------------------------------------------------------


class TestSESConfig:
    """Tests for infra.ses.config.SESConfig."""

    def test_ses_config_frozen(self) -> None:
        """SESConfig must be immutable (frozen dataclass)."""
        config = SESConfig()
        with pytest.raises((AttributeError, TypeError)):
            config.s3_bucket = "other-bucket"  # type: ignore[misc]

    def test_ses_config_defaults(self) -> None:
        """Default field values must match documented values."""
        config = SESConfig()
        assert config.receipt_rule_set_name == "graphclaw-inbound"
        assert config.s3_bucket == "graphclaw-inbound-email"
        assert config.s3_key_prefix == "email/"
        assert config.aws_region == "us-east-1"
        assert "graphclaw.ai" in config.recipients

    def test_ses_config_custom_values(self) -> None:
        """Custom values must be stored correctly."""
        config = SESConfig(
            s3_bucket="my-bucket",
            lambda_function_arn="arn:aws:lambda:us-east-1:123:function:myFn",
            recipients=("mail.example.com",),
        )
        assert config.s3_bucket == "my-bucket"
        assert config.lambda_function_arn == "arn:aws:lambda:us-east-1:123:function:myFn"
        assert config.recipients == ("mail.example.com",)


# ---------------------------------------------------------------------------
# build_ses_receipt_rule tests
# ---------------------------------------------------------------------------


class TestBuildSesReceiptRule:
    """Tests for infra.ses.config.build_ses_receipt_rule."""

    def test_build_ses_receipt_rule_structure(self) -> None:
        """Output dict must have correct RuleSetName and Actions with S3Action."""
        config = SESConfig(
            lambda_function_arn="arn:aws:lambda:us-east-1:123:function:forwarder",
        )
        rule = build_ses_receipt_rule(config)

        assert rule["RuleSetName"] == "graphclaw-inbound"
        assert "Rule" in rule

        rule_body = rule["Rule"]
        assert rule_body["Name"] == "graphclaw-inbound-rule"
        assert rule_body["Enabled"] is True
        assert rule_body["ScanEnabled"] is True
        assert "graphclaw.ai" in rule_body["Recipients"]

        actions = rule_body["Actions"]
        assert isinstance(actions, list)
        assert len(actions) >= 1

        # S3Action must be present
        s3_actions = [a for a in actions if "S3Action" in a]
        assert len(s3_actions) == 1
        assert s3_actions[0]["S3Action"]["BucketName"] == "graphclaw-inbound-email"

        # LambdaAction must be present when lambda_function_arn is set
        lambda_actions = [a for a in actions if "LambdaAction" in a]
        assert len(lambda_actions) == 1
        assert lambda_actions[0]["LambdaAction"]["InvocationType"] == "Event"

    def test_build_ses_receipt_rule_no_lambda(self) -> None:
        """When lambda_function_arn is empty, LambdaAction must be omitted."""
        config = SESConfig(lambda_function_arn="")
        rule = build_ses_receipt_rule(config)
        actions = rule["Rule"]["Actions"]
        lambda_actions = [a for a in actions if "LambdaAction" in a]
        assert len(lambda_actions) == 0


# ---------------------------------------------------------------------------
# LAMBDA_HANDLER_CODE tests
# ---------------------------------------------------------------------------


class TestLambdaHandlerCode:
    """Tests for infra.ses.lambda_handler.LAMBDA_HANDLER_CODE."""

    def test_lambda_handler_code_is_string(self) -> None:
        """LAMBDA_HANDLER_CODE must be a non-empty string containing 'presigned'."""
        assert isinstance(LAMBDA_HANDLER_CODE, str)
        assert len(LAMBDA_HANDLER_CODE) > 0
        assert "presigned" in LAMBDA_HANDLER_CODE

    def test_lambda_handler_code_contains_key_symbols(self) -> None:
        """LAMBDA_HANDLER_CODE must reference the required gateway endpoint and HMAC."""
        assert "/webhooks/email/ses" in LAMBDA_HANDLER_CODE
        assert "generate_presigned_url" in LAMBDA_HANDLER_CODE
        assert "hmac" in LAMBDA_HANDLER_CODE
        assert "X-GraphClaw-Signature" in LAMBDA_HANDLER_CODE

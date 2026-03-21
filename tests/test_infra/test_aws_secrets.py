"""tests.test_infra.test_aws_secrets — Unit tests for AWSSecretsClient.

Tests the AWS Secrets Manager client using a mock boto3 client so no real
AWS calls are made.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from graphclaw.infra.secrets import AWSSecretsClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_boto3_client(
    secret_value: str = "mysecret",
    not_found: bool = False,
    create_fails: bool = False,
):
    """Return a mock boto3 secretsmanager client."""
    client = MagicMock()

    if not_found:
        exc = type("ResourceNotFoundException", (Exception,), {})()
        client.get_secret_value.side_effect = exc
    else:
        client.get_secret_value.return_value = {"SecretString": secret_value}

    if create_fails:
        client.create_secret.side_effect = RuntimeError("create failed")
    else:
        client.create_secret.return_value = {}

    client.put_secret_value.return_value = {}
    client.delete_secret.return_value = {}
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAWSSecretsClient:
    def _patch_boto3(self, boto3_client_mock):
        """Context manager that patches boto3.client to return our mock."""
        return patch("boto3.client", return_value=boto3_client_mock)

    async def test_get_secret_returns_value(self):
        mock_client = _make_boto3_client(secret_value="s3cr3t")
        aws = AWSSecretsClient(region="us-east-1")
        with self._patch_boto3(mock_client):
            result = await aws.get_secret("MY_KEY")
        assert result == "s3cr3t"
        mock_client.get_secret_value.assert_called_once_with(SecretId="MY_KEY")

    async def test_get_secret_with_prefix(self):
        mock_client = _make_boto3_client(secret_value="prefixed")
        aws = AWSSecretsClient(region="us-east-1", secret_prefix="graphclaw/prod/")
        with self._patch_boto3(mock_client):
            result = await aws.get_secret("API_KEY")
        assert result == "prefixed"
        mock_client.get_secret_value.assert_called_once_with(SecretId="graphclaw/prod/API_KEY")

    async def test_get_secret_not_found_raises_key_error(self):
        mock_client = _make_boto3_client(not_found=True)
        aws = AWSSecretsClient(region="us-east-1")
        with self._patch_boto3(mock_client):
            with pytest.raises(KeyError, match="MY_KEY"):
                await aws.get_secret("MY_KEY")

    async def test_set_secret_calls_put(self):
        mock_client = _make_boto3_client()
        aws = AWSSecretsClient(region="us-east-1")
        with self._patch_boto3(mock_client):
            await aws.set_secret("API_KEY", "newvalue")
        mock_client.put_secret_value.assert_called_once_with(
            SecretId="API_KEY", SecretString="newvalue"
        )

    async def test_set_secret_creates_if_not_exists(self):
        """If put_secret_value raises, fall back to create_secret."""
        mock_client = _make_boto3_client()
        mock_client.put_secret_value.side_effect = Exception("ResourceNotFoundException")
        aws = AWSSecretsClient(region="us-east-1")
        with self._patch_boto3(mock_client):
            await aws.set_secret("NEW_KEY", "newvalue")
        mock_client.create_secret.assert_called_once_with(Name="NEW_KEY", SecretString="newvalue")

    async def test_delete_secret_calls_delete(self):
        mock_client = _make_boto3_client()
        aws = AWSSecretsClient(region="us-east-1")
        with self._patch_boto3(mock_client):
            await aws.delete_secret("OLD_KEY")
        mock_client.delete_secret.assert_called_once_with(
            SecretId="OLD_KEY", RecoveryWindowInDays=30
        )

    async def test_delete_secret_not_found_raises_key_error(self):
        mock_client = _make_boto3_client()
        exc = type("ResourceNotFoundException", (Exception,), {})()
        mock_client.delete_secret.side_effect = exc
        aws = AWSSecretsClient(region="us-east-1")
        with self._patch_boto3(mock_client):
            with pytest.raises(KeyError):
                await aws.delete_secret("NONEXISTENT")

    def test_boto3_not_installed_raises_runtime_error(self):
        aws = AWSSecretsClient(region="us-east-1")
        with patch.dict(sys.modules, {"boto3": None}):
            with pytest.raises(RuntimeError, match="boto3 is required"):
                aws._get_client()

    def test_region_from_env(self, monkeypatch):
        monkeypatch.setenv("AWS_REGION", "eu-west-1")
        aws = AWSSecretsClient()
        assert aws._region == "eu-west-1"

    def test_region_from_aws_default_region_env(self, monkeypatch):
        monkeypatch.delenv("AWS_REGION", raising=False)
        monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-southeast-1")
        aws = AWSSecretsClient()
        assert aws._region == "ap-southeast-1"

    def test_default_region_fallback(self, monkeypatch):
        monkeypatch.delenv("AWS_REGION", raising=False)
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
        aws = AWSSecretsClient()
        assert aws._region == "us-east-1"

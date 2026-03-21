from __future__ import annotations
"""Tests for infra.security — WAF, encryption, and security stack config (WS-5-J).

Covers:
- WAFConfig rule count and key rule presence.
- build_waf_web_acl output structure.
- EncryptionConfig production settings.
- build_s3_bucket_encryption output structure.
- build_security_config top-level keys.
- WAFConfig immutability (frozen dataclass).
"""

import pytest
import dataclasses

from infra.security.waf import (
    DEFAULT_WAF_CONFIG,
    GRAPHCLAW_WAF_RULES,
    WAFConfig,
    build_waf_web_acl,
)
from infra.security.encryption import PRODUCTION_ENCRYPTION, build_s3_bucket_encryption
from infra.security.stack import build_security_config


# ---------------------------------------------------------------------------
# WAF rules tests
# ---------------------------------------------------------------------------


def test_waf_rules_count() -> None:
    """DEFAULT_WAF_CONFIG must contain exactly 4 rules."""
    assert len(DEFAULT_WAF_CONFIG.rules) == 4


def test_waf_has_owasp_rule() -> None:
    """AWSManagedRulesCommonRuleSet (OWASP Top 10) must be present."""
    rule_names = [rule.rule_name for rule in DEFAULT_WAF_CONFIG.rules]
    assert "AWSManagedRulesCommonRuleSet" in rule_names


def test_waf_acl_structure() -> None:
    """build_waf_web_acl() must return a dict with 'Rules' and 'DefaultAction' keys."""
    acl = build_waf_web_acl()
    assert "Rules" in acl
    assert "DefaultAction" in acl
    assert isinstance(acl["Rules"], list)
    assert len(acl["Rules"]) == 4


# ---------------------------------------------------------------------------
# Encryption config tests
# ---------------------------------------------------------------------------


def test_encryption_s3_kms() -> None:
    """PRODUCTION_ENCRYPTION must use aws:kms for S3 server-side encryption."""
    assert PRODUCTION_ENCRYPTION.s3_sse_algorithm == "aws:kms"


def test_encryption_rds_enabled() -> None:
    """PRODUCTION_ENCRYPTION must have RDS storage encryption enabled."""
    assert PRODUCTION_ENCRYPTION.rds_storage_encrypted is True


def test_encryption_elasticache_tls() -> None:
    """PRODUCTION_ENCRYPTION must have ElastiCache in-transit TLS enabled."""
    assert PRODUCTION_ENCRYPTION.elasticache_in_transit_encryption is True


def test_s3_bucket_encryption_structure() -> None:
    """build_s3_bucket_encryption() must return a dict with ServerSideEncryptionConfiguration."""
    result = build_s3_bucket_encryption()
    assert "ServerSideEncryptionConfiguration" in result
    config = result["ServerSideEncryptionConfiguration"]
    assert "Rules" in config
    assert len(config["Rules"]) == 1
    rule = config["Rules"][0]
    assert "ApplyServerSideEncryptionByDefault" in rule
    assert rule["ApplyServerSideEncryptionByDefault"]["SSEAlgorithm"] == "aws:kms"


# ---------------------------------------------------------------------------
# Security stack tests
# ---------------------------------------------------------------------------


def test_build_security_config_keys() -> None:
    """build_security_config() must return a dict with 'waf', 'encryption', 'rate_limits'."""
    config = build_security_config()
    assert "waf" in config
    assert "encryption" in config
    assert "rate_limits" in config


# ---------------------------------------------------------------------------
# Immutability tests
# ---------------------------------------------------------------------------


def test_waf_config_frozen() -> None:
    """WAFConfig is a frozen dataclass — mutation must raise FrozenInstanceError."""
    waf_cfg = WAFConfig()
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        waf_cfg.scope = "CLOUDFRONT"  # type: ignore[misc]

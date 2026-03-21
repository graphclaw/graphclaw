from __future__ import annotations
"""graphclaw.infra.security.stack — Full security stack descriptor.

Description
-----------
Combines WAF, encryption, and rate-limit documentation into a single
``build_security_config()`` call.  The returned dict is suitable for
infrastructure-as-code validation, audit logging, and CI policy checks.

Public API
----------
- build_security_config: Return a full security stack descriptor dict.
- RATE_LIMITS_DOC: Human-readable rate limit documentation dict.

License: Apache 2.0
"""

from infra.security.encryption import PRODUCTION_ENCRYPTION, build_s3_bucket_encryption
from infra.security.waf import DEFAULT_WAF_CONFIG, build_waf_web_acl


# ── Rate limits documentation (mirrors RATE_LIMITS in rate_limiter.py) ───────

RATE_LIMITS_DOC: dict[str, str] = {
    "unauthenticated_ip": "30 req/min",
    "authenticated_user": "300 req/min",
    "a2a_agent": "60 req/min",
    "webhook_source": "120 req/min",
    "waf_layer": "2000 req/5min",
}


def build_security_config() -> dict:
    """Return a full security stack descriptor.

    Combines WAF web ACL config, encryption-at-rest settings for all
    storage layers, and application-level rate limit documentation into
    a single dict for infrastructure validation and audit purposes.

    Returns
    -------
    dict:
        Keys ``"waf"``, ``"encryption"``, and ``"rate_limits"``.
    """
    return {
        "waf": build_waf_web_acl(DEFAULT_WAF_CONFIG),
        "encryption": {
            "s3": build_s3_bucket_encryption(PRODUCTION_ENCRYPTION),
            "rds_encrypted": PRODUCTION_ENCRYPTION.rds_storage_encrypted,
            "elasticache_encrypted": PRODUCTION_ENCRYPTION.elasticache_at_rest_encryption,
        },
        "rate_limits": RATE_LIMITS_DOC,
    }

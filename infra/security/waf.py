from __future__ import annotations
"""graphclaw.infra.security.waf — AWS WAFv2 configuration descriptors.

Description
-----------
Defines immutable dataclasses representing WAF rules and configuration for
the GraphClaw application, and a builder function that produces an AWS
WAFv2 ``CreateWebACL`` request dict.

Public API
----------
- WAFRule: Immutable descriptor for a single WAF rule.
- WAFConfig: Immutable descriptor for the full WAF Web ACL configuration.
- GRAPHCLAW_WAF_RULES: Default tuple of WAF rules for GraphClaw.
- DEFAULT_WAF_CONFIG: Default ``WAFConfig`` using ``GRAPHCLAW_WAF_RULES``.
- build_waf_web_acl: Return an AWS WAFv2 CreateWebACL request dict.

License: Apache 2.0
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class WAFRule:
    """Immutable descriptor for a single AWS WAFv2 rule.

    Parameters
    ----------
    rule_name:
        Unique name for the rule within the Web ACL.
    priority:
        Evaluation priority — lower numbers are evaluated first.
    action:
        ``"BLOCK"`` to drop matching requests, ``"COUNT"`` to log only.
    statement_type:
        WAFv2 statement type string (e.g. ``"ManagedRuleGroup"``,
        ``"RateBasedStatement"``, ``"IPSetReferenceStatement"``).
    description:
        Human-readable description of what the rule protects against.
    """

    rule_name: str
    priority: int
    action: str           # "BLOCK" or "COUNT"
    statement_type: str   # "IPSetReferenceStatement", "RateBasedStatement", "ManagedRuleGroup"
    description: str = ""


@dataclass(frozen=True)
class WAFConfig:
    """Immutable descriptor for a WAFv2 Web ACL configuration.

    Parameters
    ----------
    scope:
        ``"REGIONAL"`` (for ALB / API GW) or ``"CLOUDFRONT"``.
    default_action:
        ``"ALLOW"`` or ``"BLOCK"`` for traffic that does not match any rule.
    rules:
        Tuple of ``WAFRule`` instances to include in the Web ACL.
    """

    scope: str = "REGIONAL"          # REGIONAL (ALB) or CLOUDFRONT
    default_action: str = "ALLOW"
    rules: tuple[WAFRule, ...] = field(default_factory=tuple)


# ── AWS Managed Rule Groups for GraphClaw ────────────────────────────────────

GRAPHCLAW_WAF_RULES: tuple[WAFRule, ...] = (
    WAFRule(
        "AWSManagedRulesCommonRuleSet",
        priority=1,
        action="BLOCK",
        statement_type="ManagedRuleGroup",
        description="OWASP Top 10 protection — SQL injection, XSS, path traversal",
    ),
    WAFRule(
        "AWSManagedRulesKnownBadInputsRuleSet",
        priority=2,
        action="BLOCK",
        statement_type="ManagedRuleGroup",
        description="Known bad inputs — Log4Shell, Spring4Shell, etc.",
    ),
    WAFRule(
        "RateLimitRule",
        priority=10,
        action="BLOCK",
        statement_type="RateBasedStatement",
        description="Block IPs exceeding 2000 req/5min at WAF layer",
    ),
    WAFRule(
        "GeoBlockRule",
        priority=20,
        action="COUNT",  # COUNT for now; switch to BLOCK for data residency enforcement
        statement_type="IPSetReferenceStatement",
        description="Geo-based monitoring (not blocking) — enable blocking per data residency",
    ),
)

DEFAULT_WAF_CONFIG = WAFConfig(rules=GRAPHCLAW_WAF_RULES)


def build_waf_web_acl(config: WAFConfig = DEFAULT_WAF_CONFIG) -> dict:
    """Return an AWS WAFv2 CreateWebACL request dict.

    Parameters
    ----------
    config:
        ``WAFConfig`` to serialise. Defaults to ``DEFAULT_WAF_CONFIG``.

    Returns
    -------
    dict:
        A dict suitable for passing to ``boto3``'s ``wafv2.create_web_acl``
        (or ``update_web_acl``) as keyword arguments.
    """
    return {
        "Name": "graphclaw-waf",
        "Scope": config.scope,
        "DefaultAction": {config.default_action.capitalize(): {}},
        "Rules": [
            {
                "Name": rule.rule_name,
                "Priority": rule.priority,
                "Action": {rule.action.capitalize(): {}},
                "Statement": {"Type": rule.statement_type},
                "VisibilityConfig": {
                    "SampledRequestsEnabled": True,
                    "CloudWatchMetricsEnabled": True,
                    "MetricName": rule.rule_name,
                },
            }
            for rule in config.rules
        ],
        "VisibilityConfig": {
            "SampledRequestsEnabled": True,
            "CloudWatchMetricsEnabled": True,
            "MetricName": "graphclaw-waf",
        },
    }

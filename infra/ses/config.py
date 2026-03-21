# Copyright 2024 GraphClaw Contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""graphclaw.infra.ses.config — SES inbound email infrastructure configuration.

Description
-----------
Provides ``SESConfig``, a frozen dataclass that captures the AWS SES receipt
rule configuration required to route inbound email to S3 and Lambda, and
``build_ses_receipt_rule``, a pure function that converts the config into the
dict shape expected by the AWS SES ``CreateReceiptRule`` API.

Design Patterns
---------------
- Value Object: ``SESConfig`` is immutable (frozen dataclass) with sensible
  defaults; all deploy-time values are injected at construction.
- Builder: ``build_ses_receipt_rule`` converts the config to the exact wire
  format required by the AWS SDK without leaking AWS SDK types into callers.

Public API
----------
- SESConfig: Frozen dataclass holding SES receipt rule parameters.
- build_ses_receipt_rule: Return the AWS SES CreateReceiptRule request dict.

Notes
-----
``lambda_function_arn`` must be provided at deploy time; it is empty by default
so that the config can be imported without triggering errors during local dev.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SESConfig:
    """Configuration for the SES inbound email receipt rule.

    Attributes
    ----------
    receipt_rule_set_name:
        Name of the SES receipt rule set that owns this rule.
    s3_bucket:
        S3 bucket where SES stores raw inbound email messages.
    s3_key_prefix:
        Key prefix applied to all objects written by the S3Action.
    lambda_function_arn:
        ARN of the Lambda function invoked by the LambdaAction.
        Set at deploy time; empty string disables the Lambda action.
    aws_region:
        AWS region where the SES receipt rule set is managed.
    recipients:
        Tuple of email addresses or domain names that SES will receive
        for. SES matches on exact addresses or domain suffixes.
    """

    receipt_rule_set_name: str = "graphclaw-inbound"
    s3_bucket: str = "graphclaw-inbound-email"
    s3_key_prefix: str = "email/"
    lambda_function_arn: str = ""  # set at deploy time
    aws_region: str = "us-east-1"
    # Domains/addresses that SES will receive for
    recipients: tuple[str, ...] = field(default_factory=lambda: ("graphclaw.ai",))


def build_ses_receipt_rule(config: SESConfig) -> dict:
    """Return the AWS SES CreateReceiptRule request dict.

    Constructs the exact payload expected by ``boto3.client("ses").create_receipt_rule``
    from an ``SESConfig`` instance.

    Parameters
    ----------
    config:
        Populated ``SESConfig`` instance.

    Returns
    -------
    dict
        Dict suitable for unpacking into ``create_receipt_rule(**result)``.

    Notes
    -----
    ``ScanEnabled=True`` enables SpamAssassin-based spam scanning on all
    inbound messages before the S3Action and LambdaAction fire.
    The LambdaAction uses ``InvocationType="Event"`` (async) so that SES
    does not wait for the Lambda response before acknowledging receipt.
    """
    actions = [
        {
            "S3Action": {
                "BucketName": config.s3_bucket,
                "ObjectKeyPrefix": config.s3_key_prefix,
            }
        },
    ]

    if config.lambda_function_arn:
        actions.append(
            {
                "LambdaAction": {
                    "FunctionArn": config.lambda_function_arn,
                    "InvocationType": "Event",  # async
                }
            }
        )

    return {
        "RuleSetName": config.receipt_rule_set_name,
        "Rule": {
            "Name": "graphclaw-inbound-rule",
            "Enabled": True,
            "Recipients": list(config.recipients),
            "Actions": actions,
            "ScanEnabled": True,  # SpamAssassin scanning
        },
    }

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
"""graphclaw.infra.ses — AWS SES inbound email infrastructure helpers.

Description
-----------
Infrastructure-as-code helpers for the SES → S3 → Lambda → Gateway email
ingest pipeline used in production.

Public re-exports
-----------------
- SESConfig: Frozen dataclass for SES receipt rule configuration.
- build_ses_receipt_rule: Build the AWS SES CreateReceiptRule request dict.
- LAMBDA_HANDLER_CODE: Python source string for the Lambda bridge function.
"""
from __future__ import annotations

from infra.ses.config import SESConfig, build_ses_receipt_rule
from infra.ses.lambda_handler import LAMBDA_HANDLER_CODE

__all__ = [
    "SESConfig",
    "build_ses_receipt_rule",
    "LAMBDA_HANDLER_CODE",
]

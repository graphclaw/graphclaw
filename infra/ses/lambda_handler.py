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
"""graphclaw.infra.ses.lambda_handler — Lambda source code for SES email forwarding.

Description
-----------
Stores the Python source code for the AWS Lambda function that bridges SES
receipt notifications to the GraphClaw gateway webhook endpoint.

The code is kept here as the ``LAMBDA_HANDLER_CODE`` string constant rather
than a deployable Python file so that:
1. It travels with the infra package as IaC documentation.
2. It can be embedded in CloudFormation / CDK inline function resources.
3. It can be zipped and deployed via the CI/CD pipeline without a separate
   Lambda repository.

Design Patterns
---------------
- Inline IaC: Lambda source embedded as a string constant; deploy tooling
  reads this module and creates/updates the Lambda function.

Public API
----------
- LAMBDA_HANDLER_CODE: Python source string for the Lambda handler.

Notes
-----
The Lambda requires these environment variables at runtime:
- ``GATEWAY_URL``: Base URL of the GraphClaw gateway (e.g. https://api.graphclaw.ai).
- ``GATEWAY_SECRET``: Shared HMAC secret for X-GraphClaw-Signature. Optional.
- ``S3_BUCKET``: S3 bucket name where SES stores raw email.
"""
from __future__ import annotations

LAMBDA_HANDLER_CODE = '''
import json, os, boto3, urllib.request, hmac, hashlib

GATEWAY_URL = os.environ["GATEWAY_URL"]          # e.g. https://api.graphclaw.ai
GATEWAY_SECRET = os.environ.get("GATEWAY_SECRET", "")
S3 = boto3.client("s3")

def handler(event, context):
    for record in event.get("Records", []):
        ses = record.get("ses", {})
        mail = ses.get("mail", {})
        receipt = ses.get("receipt", {})

        bucket = os.environ["S3_BUCKET"]
        key = f"email/{mail.get(\'messageId\', \'unknown\')}"

        # Generate pre-signed URL (1-hour expiry)
        presigned = S3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=3600,
        )

        payload = json.dumps({
            "s3_bucket": bucket,
            "s3_key": key,
            "presigned_url": presigned,
            "sns_message_id": record.get("EventSource", ""),
            "recipient": receipt.get("recipients", [""])[0],
        }).encode()

        sig = hmac.new(GATEWAY_SECRET.encode(), payload, hashlib.sha256).hexdigest() if GATEWAY_SECRET else ""

        req = urllib.request.Request(
            f"{GATEWAY_URL}/webhooks/email/ses",
            data=payload,
            headers={"Content-Type": "application/json", "X-GraphClaw-Signature": sig},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
    return {"statusCode": 200}
'''

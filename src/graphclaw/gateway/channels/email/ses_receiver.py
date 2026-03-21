"""graphclaw.gateway.channels.email.ses_receiver — SES Inbound Email Receiver.

Description
-----------
Production email ingest: SES → S3 → Lambda → POST /webhooks/email/ses

SES receives email, stores raw message in S3, triggers Lambda which POSTs
the S3 object key to this endpoint. This replaces IMAP polling in production.

Design Patterns
---------------
- Webhook Receiver: Accepts Lambda POST callbacks instead of polling.
- Strategy: Swappable ingest path alongside the IMAP EmailPoller; selected
  via the EMAIL_BACKEND environment variable.

Public API
----------
- SESEmailReceiver: Processes SES webhook notifications from Lambda.
- SESEmailReceiver.from_env: Construct from environment variables.
- SESEmailReceiver.verify_lambda_signature: Validate HMAC-SHA256 from Lambda.
- SESEmailReceiver.handle_ses_notification: Download email from S3, normalise.

Dependencies
------------
- graphclaw.gateway.channels.email.normalizer: normalize_email.
- graphclaw.gateway.schemas: InboundMessage.
- httpx: Async HTTP client for S3 presigned URL downloads (third-party).
- email: stdlib RFC 822 message parsing.
- hmac, hashlib: HMAC-SHA256 signature verification (stdlib).

Notes
-----
Local dev continues to use the IMAP EmailPoller (EMAIL_BACKEND=imap).
SESEmailReceiver is only instantiated in production (EMAIL_BACKEND=ses).

The Lambda generates a pre-signed S3 URL (1-hour expiry) so the gateway
never needs AWS credentials at runtime — only an IAM role or the URL itself.
"""
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
from __future__ import annotations

import email as email_lib
import hashlib
import hmac
import logging
from email.message import Message

import httpx

from graphclaw.gateway.channels.email.normalizer import normalize_email
from graphclaw.gateway.schemas import InboundMessage

logger = logging.getLogger(__name__)


class SESEmailReceiver:
    """Handles SES inbound email webhook from Lambda.

    Flow:
        1. Lambda POSTs {"s3_bucket": "...", "s3_key": "...", "sns_message_id": "..."}
        2. This receiver downloads the raw email from S3 using pre-signed URL or boto3
        3. Parses with Python stdlib ``email`` module
        4. Normalises to InboundMessage via existing normalizer

    Parameters
    ----------
    s3_bucket:
        Name of the S3 bucket where SES stores raw inbound email.
    aws_region:
        AWS region for constructing fallback S3 URLs.
    lambda_shared_secret:
        HMAC-SHA256 shared secret for Lambda→Gateway authentication.
        Empty string disables signature verification (dev/test mode).
    """

    def __init__(
        self,
        s3_bucket: str,
        aws_region: str = "us-east-1",
        lambda_shared_secret: str = "",
    ) -> None:
        self.s3_bucket = s3_bucket
        self.aws_region = aws_region
        self.lambda_shared_secret = lambda_shared_secret

    def verify_lambda_signature(self, body: bytes, signature: str) -> bool:
        """Verify HMAC-SHA256 signature from Lambda caller.

        Parameters
        ----------
        body:
            Raw request body bytes as received from Lambda.
        signature:
            Hex-encoded HMAC-SHA256 signature from the
            ``X-GraphClaw-Signature`` request header.

        Returns
        -------
        bool
            ``True`` if the signature is valid or no secret is configured.
            ``False`` if the signature does not match.
        """
        if not self.lambda_shared_secret:
            # No secret configured → skip verification (dev mode)
            return True
        expected = hmac.new(
            self.lambda_shared_secret.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    async def handle_ses_notification(
        self,
        payload: dict,
    ) -> InboundMessage | None:
        """Process SES notification payload from Lambda.

        Downloads the raw email from the S3 presigned URL included in the
        payload (or constructs a fallback S3 URL if absent), parses the RFC
        822 message, and returns a normalised ``InboundMessage``.

        Expected payload schema::

            {
                "s3_bucket":       "graphclaw-inbound-email",
                "s3_key":          "email/USER-abc/msg-xyz",
                "presigned_url":   "https://s3.amazonaws.com/...",  # optional
                "sns_message_id":  "...",
                "recipient":       "user@graphclaw.ai"
            }

        Parameters
        ----------
        payload:
            Decoded JSON body from the Lambda POST request.

        Returns
        -------
        InboundMessage | None
            Normalised inbound message, or ``None`` if the download or parse
            step produced no usable content.
        """
        presigned_url = payload.get("presigned_url")
        s3_key = payload.get("s3_key", "")
        recipient = payload.get("recipient", "")

        if presigned_url:
            logger.debug(
                "SESEmailReceiver: downloading email via presigned URL, key=%s", s3_key
            )
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(presigned_url)
                resp.raise_for_status()
                raw_bytes = resp.content
        else:
            # Fallback: construct S3 URL (requires public bucket or IAM role)
            url = (
                f"https://{self.s3_bucket}.s3.{self.aws_region}.amazonaws.com/{s3_key}"
            )
            logger.debug(
                "SESEmailReceiver: no presigned URL, falling back to S3 path url=%s",
                url,
            )
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                raw_bytes = resp.content

        # Parse raw RFC 822 email
        from email.message import EmailMessage as _EmailMessage

        msg: Message = email_lib.message_from_bytes(raw_bytes, _class=_EmailMessage)

        # normalize_email expects EmailMessage; the cast is safe because we
        # pass _class=EmailMessage above.
        inbound = normalize_email(msg)  # type: ignore[arg-type]

        # Override sender field with SES envelope recipient when available so
        # the routing layer knows which inbox received the message.
        if recipient:
            logger.debug(
                "SESEmailReceiver: normalised message %s for recipient %s",
                inbound.message_id,
                recipient,
            )

        return inbound

    @classmethod
    def from_env(cls) -> "SESEmailReceiver":
        """Construct from environment variables.

        Environment variables
        ---------------------
        SES_S3_BUCKET:
            S3 bucket name for inbound emails. Default: ``graphclaw-inbound-email``.
        AWS_REGION:
            AWS region. Default: ``us-east-1``.
        SES_LAMBDA_SECRET:
            HMAC-SHA256 shared secret for Lambda→Gateway auth. Default: ``""``
            (disables verification in local dev).

        Returns
        -------
        SESEmailReceiver
            Receiver configured from the current environment.
        """
        import os

        return cls(
            s3_bucket=os.environ.get("SES_S3_BUCKET", "graphclaw-inbound-email"),
            aws_region=os.environ.get("AWS_REGION", "us-east-1"),
            lambda_shared_secret=os.environ.get("SES_LAMBDA_SECRET", ""),
        )

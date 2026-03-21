# SES Inbound Email — Production Pipeline

## Why SES Replaces IMAP at Production Scale

IMAP polling requires a long-lived TCP connection to the mail server and must
poll on a fixed interval (e.g., every 60 s). At production scale this creates:

- **Connection pool pressure**: One open IMAP connection per gateway pod; does
  not scale horizontally without deduplication logic.
- **Latency floor**: Messages sit in the inbox until the next poll cycle.
- **Credential exposure**: Gateway pods must hold IMAP passwords in memory.
- **Reliability gap**: Transient IMAP disconnects drop messages unless the
  poller implements reconnect + seek-by-UID logic.

AWS SES inbound email is event-driven and push-based:
- SES receives SMTP, stores the raw message in S3, and fires Lambda
  asynchronously — no polling, no open connections, no credential exposure.
- Lambda forwards a pre-signed S3 URL to the gateway; the gateway downloads
  and normalises the message on demand.
- Horizontal scaling is automatic: the gateway endpoint is stateless.

Local dev continues to use IMAP (`EMAIL_BACKEND=imap`); SES is
`EMAIL_BACKEND=ses` (production only).

---

## Flow Diagram

```
Internet / Sender
      |
      | SMTP (port 25)
      v
+------------+
|  AWS SES   |  Receipt Rule: store to S3, invoke Lambda
+-----+------+
      |
      | PutObject
      v
+------------+
|  AWS S3    |  s3://graphclaw-inbound-email/email/<messageId>
+-----+------+
      |
      | Lambda trigger (async)
      v
+-------------------+
|  Lambda Function  |  Generates S3 pre-signed URL, POSTs to gateway
+--------+----------+
         |
         | POST /webhooks/email/ses
         | X-GraphClaw-Signature: <HMAC-SHA256>
         v
+--------------------+
|  GraphClaw Gateway |  Verifies signature, downloads email, normalises,
|  (FastAPI)         |  publishes to broker INBOUND_MESSAGES queue
+--------------------+
         |
         | publish
         v
+--------------------+
|  Redis / Broker    |  INBOUND_MESSAGES queue
+--------------------+
         |
         v
   Agent / Orchestrator
```

---

## Required Environment Variables

### Gateway (production)

| Variable           | Description                                          | Default                        |
|--------------------|------------------------------------------------------|--------------------------------|
| `SES_S3_BUCKET`    | S3 bucket where SES stores raw inbound email         | `graphclaw-inbound-email`      |
| `AWS_REGION`       | AWS region for fallback S3 URL construction          | `us-east-1`                    |
| `SES_LAMBDA_SECRET`| HMAC-SHA256 shared secret for Lambda→Gateway auth    | `""` (skip verification in dev)|
| `EMAIL_BACKEND`    | Set to `ses` in production, `imap` for local dev     | `imap`                         |

### Lambda function (set in Lambda environment)

| Variable          | Description                                              |
|-------------------|----------------------------------------------------------|
| `GATEWAY_URL`     | Base URL of the GraphClaw gateway (e.g. `https://api.graphclaw.ai`) |
| `GATEWAY_SECRET`  | Same HMAC-SHA256 secret as `SES_LAMBDA_SECRET` above     |
| `S3_BUCKET`       | Same bucket as `SES_S3_BUCKET` above                     |

---

## Setting Up SES Receipt Rules (AWS CLI)

### 1. Create a receipt rule set (once per account/region)

```bash
aws ses create-receipt-rule-set \
  --rule-set-name graphclaw-inbound \
  --region us-east-1
```

### 2. Verify the domain in SES

```bash
aws ses verify-domain-identity \
  --domain graphclaw.ai \
  --region us-east-1
```

Add the returned `VerificationToken` as a TXT record at `_amazonses.graphclaw.ai`.

### 3. Set the MX record

Point MX records for `graphclaw.ai` to the SES SMTP endpoint for your region:

```
10 inbound-smtp.us-east-1.amazonaws.com
```

### 4. Create the S3 bucket with the SES write policy

```bash
aws s3 mb s3://graphclaw-inbound-email --region us-east-1

# Allow SES to write to the bucket
aws s3api put-bucket-policy \
  --bucket graphclaw-inbound-email \
  --policy '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Principal": {"Service": "ses.amazonaws.com"},
      "Action": "s3:PutObject",
      "Resource": "arn:aws:s3:::graphclaw-inbound-email/*",
      "Condition": {"StringEquals": {"aws:Referer": "<YOUR_ACCOUNT_ID>"}}
    }]
  }'
```

### 5. Deploy the Lambda function

Package `LAMBDA_HANDLER_CODE` from `infra/ses/lambda_handler.py` and deploy:

```bash
# Write handler to a temp file and zip it
python -c "
from infra.ses.lambda_handler import LAMBDA_HANDLER_CODE
with open('/tmp/handler.py', 'w') as f: f.write(LAMBDA_HANDLER_CODE)
"
cd /tmp && zip handler.zip handler.py

aws lambda create-function \
  --function-name graphclaw-ses-forwarder \
  --runtime python3.12 \
  --handler handler.handler \
  --role arn:aws:iam::<ACCOUNT_ID>:role/graphclaw-lambda-ses \
  --zip-file fileb://handler.zip \
  --environment "Variables={
    GATEWAY_URL=https://api.graphclaw.ai,
    GATEWAY_SECRET=<your-secret>,
    S3_BUCKET=graphclaw-inbound-email
  }" \
  --region us-east-1
```

### 6. Create the receipt rule using the Python helper

```python
import boto3
from infra.ses.config import SESConfig, build_ses_receipt_rule

config = SESConfig(
    lambda_function_arn="arn:aws:lambda:us-east-1:<ACCOUNT_ID>:function:graphclaw-ses-forwarder",
)
rule = build_ses_receipt_rule(config)

ses = boto3.client("ses", region_name="us-east-1")
ses.create_receipt_rule(**rule)
```

### 7. Activate the receipt rule set

```bash
aws ses set-active-receipt-rule-set \
  --rule-set-name graphclaw-inbound \
  --region us-east-1
```

---

## Local Development

Local dev uses the IMAP EmailPoller. No SES setup is required.

Set `EMAIL_BACKEND=imap` (the default) and configure IMAP credentials:

```env
EMAIL_BACKEND=imap
GATEWAY_IMAP_HOST=imap.gmail.com
GATEWAY_IMAP_PORT=993
GATEWAY_IMAP_USER=you@gmail.com
GATEWAY_IMAP_PASS=your-app-password
```

The IMAP poller is started automatically by `EmailChannelAdapter.start()` when
`GATEWAY_IMAP_HOST`, `GATEWAY_IMAP_USER`, and `GATEWAY_IMAP_PASS` are set.

from __future__ import annotations
"""graphclaw.infra.security.encryption — Encryption-at-rest configuration.

Description
-----------
Defines immutable dataclasses representing encryption configuration for
all GraphClaw storage layers (S3, RDS, ElastiCache, ECS secrets) and
builder functions that produce AWS SDK request dicts.

Public API
----------
- EncryptionConfig: Immutable descriptor for all encryption-at-rest settings.
- PRODUCTION_ENCRYPTION: Default production-grade ``EncryptionConfig``.
- build_s3_bucket_encryption: Return an S3 PutBucketEncryption request dict.

License: Apache 2.0
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class EncryptionConfig:
    """Encryption-at-rest configuration for all storage layers.

    Parameters
    ----------
    s3_sse_algorithm:
        S3 server-side encryption algorithm. ``"aws:kms"`` uses KMS-managed
        keys; ``"AES256"`` uses S3-managed keys.
    s3_bucket_key_enabled:
        Enable S3 Bucket Key to reduce KMS API call costs.
    rds_storage_encrypted:
        Enable storage encryption for the RDS Postgres instance.
    rds_kms_key_alias:
        KMS key alias used to encrypt the RDS storage volume.
    elasticache_at_rest_encryption:
        Enable at-rest encryption for the ElastiCache Redis cluster.
    elasticache_in_transit_encryption:
        Enable TLS for in-transit encryption between ElastiCache nodes
        and between clients and the cluster.
    ecs_secrets_from:
        How ECS tasks receive secrets — ``"aws_secrets_manager"`` injects
        values from Secrets Manager rather than embedding them as plaintext
        environment variables.
    """

    # S3
    s3_sse_algorithm: str = "aws:kms"         # KMS-managed keys
    s3_bucket_key_enabled: bool = True         # S3 Bucket Key reduces KMS API calls

    # RDS
    rds_storage_encrypted: bool = True
    rds_kms_key_alias: str = "alias/graphclaw-rds"

    # ElastiCache (Redis)
    elasticache_at_rest_encryption: bool = True
    elasticache_in_transit_encryption: bool = True   # TLS between nodes

    # ECS secrets (env vars)
    ecs_secrets_from: str = "aws_secrets_manager"    # not plaintext env vars


PRODUCTION_ENCRYPTION = EncryptionConfig()


def build_s3_bucket_encryption(config: EncryptionConfig = PRODUCTION_ENCRYPTION) -> dict:
    """Return an S3 PutBucketEncryption request dict.

    Parameters
    ----------
    config:
        ``EncryptionConfig`` to serialise. Defaults to
        ``PRODUCTION_ENCRYPTION``.

    Returns
    -------
    dict:
        A dict suitable for use as the ``ServerSideEncryptionConfiguration``
        argument to ``boto3``'s ``s3.put_bucket_encryption``.
    """
    return {
        "ServerSideEncryptionConfiguration": {
            "Rules": [
                {
                    "ApplyServerSideEncryptionByDefault": {
                        "SSEAlgorithm": config.s3_sse_algorithm,
                    },
                    "BucketKeyEnabled": config.s3_bucket_key_enabled,
                }
            ]
        }
    }

# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.infra.logging.handlers.cloudwatch — CloudWatchHandler.

Wraps watchtower.CloudWatchLogHandler with JsonFormatter and a configurable
minimum level. Falls back gracefully if watchtower is not installed.
"""

from __future__ import annotations

import logging


def build_cloudwatch_handler(
    region: str,
    log_group_prefix: str = "/graphclaw",
    service_name: str = "gateway",
    min_level: str = "WARNING",
) -> logging.Handler:
    """Build a CloudWatch log handler, or a NullHandler if watchtower is absent.

    Args:
        region: AWS region for CloudWatch.
        log_group_prefix: Prefix for the CloudWatch log group name.
        service_name: Appended to log group: {prefix}/{service_name}.
        min_level: Minimum log level to send to CloudWatch (default WARNING).

    Returns:
        A configured CloudWatchLogHandler or NullHandler.
    """
    try:
        import boto3
        import watchtower

        boto3_session = boto3.Session(region_name=region)
        handler = watchtower.CloudWatchLogHandler(
            boto3_session=boto3_session,
            log_group_name=f"{log_group_prefix}/{service_name}",
            stream_name="%(asctime)s",
        )
        handler.setLevel(getattr(logging, min_level.upper(), logging.WARNING))
        return handler
    except ImportError:
        null = logging.NullHandler()
        null.setLevel(getattr(logging, min_level.upper(), logging.WARNING))
        return null

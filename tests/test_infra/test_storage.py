# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_infra.test_storage — Unit tests for StorageClient / S3StorageClient.

Description
-----------
Tests for the ``S3StorageClient`` implementation using a mocked boto3 client.
All boto3 I/O is intercepted via ``unittest.mock.patch`` so no real AWS or
MinIO connection is required.

Design Patterns
---------------
- Arrange/Act/Assert: Each test sets up a mock, calls the client, and asserts
  the expected boto3 method was called with the correct arguments.
- Dependency Injection via Patching: boto3.client is patched at the module
  import level to return a mock S3 client.

Dependencies
------------
- pytest, pytest-asyncio: Test runner with async support.
- unittest.mock: MagicMock, patch, AsyncMock.
- graphclaw.infra.storage: S3StorageClient under test.
"""

from __future__ import annotations

from io import BytesIO
from unittest.mock import MagicMock

import pytest

from graphclaw.infra.storage import S3StorageClient


def _make_client(mock_s3: MagicMock) -> S3StorageClient:
    """Return an S3StorageClient whose internal boto3 client is *mock_s3*."""
    client = S3StorageClient(bucket="test-bucket", endpoint_url="http://localhost:9000")
    client._client = mock_s3  # inject pre-built mock
    return client


# ---------------------------------------------------------------------------
# test_write_calls_put_object
# ---------------------------------------------------------------------------


async def test_write_calls_put_object() -> None:
    mock_s3 = MagicMock()
    client = _make_client(mock_s3)

    await client.write("agents/user1/state.json", b'{"x": 1}', content_type="application/json")

    mock_s3.put_object.assert_called_once_with(
        Bucket="test-bucket",
        Key="agents/user1/state.json",
        Body=b'{"x": 1}',
        ContentType="application/json",
    )


# ---------------------------------------------------------------------------
# test_read_calls_get_object
# ---------------------------------------------------------------------------


async def test_read_calls_get_object() -> None:
    mock_s3 = MagicMock()
    mock_s3.get_object.return_value = {"Body": BytesIO(b"hello")}
    client = _make_client(mock_s3)

    result = await client.read("agents/user1/state.json")

    assert result == b"hello"
    mock_s3.get_object.assert_called_once_with(Bucket="test-bucket", Key="agents/user1/state.json")


# ---------------------------------------------------------------------------
# test_delete_calls_delete_object
# ---------------------------------------------------------------------------


async def test_delete_calls_delete_object() -> None:
    mock_s3 = MagicMock()
    client = _make_client(mock_s3)

    await client.delete("agents/user1/state.json")

    mock_s3.delete_object.assert_called_once_with(
        Bucket="test-bucket", Key="agents/user1/state.json"
    )


# ---------------------------------------------------------------------------
# test_list_objects_returns_keys
# ---------------------------------------------------------------------------


async def test_list_objects_returns_keys() -> None:
    mock_s3 = MagicMock()
    # Simulate paginator returning two pages
    page1 = {"Contents": [{"Key": "agents/user1/a.json"}, {"Key": "agents/user1/b.json"}]}
    page2 = {"Contents": [{"Key": "agents/user1/c.json"}]}
    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = [page1, page2]
    mock_s3.get_paginator.return_value = mock_paginator
    client = _make_client(mock_s3)

    keys = await client.list_objects("agents/user1/")

    assert keys == [
        "agents/user1/a.json",
        "agents/user1/b.json",
        "agents/user1/c.json",
    ]
    mock_s3.get_paginator.assert_called_once_with("list_objects_v2")
    mock_paginator.paginate.assert_called_once_with(Bucket="test-bucket", Prefix="agents/user1/")


async def test_list_objects_empty_prefix() -> None:
    mock_s3 = MagicMock()
    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = [{}]  # no Contents key
    mock_s3.get_paginator.return_value = mock_paginator
    client = _make_client(mock_s3)

    keys = await client.list_objects("nonexistent/")

    assert keys == []


# ---------------------------------------------------------------------------
# test_exists_true_and_false
# ---------------------------------------------------------------------------


async def test_exists_returns_true_when_object_present() -> None:
    mock_s3 = MagicMock()
    # head_object succeeds → exists
    mock_s3.head_object.return_value = {"ContentLength": 42}
    client = _make_client(mock_s3)

    result = await client.exists("agents/user1/state.json")

    assert result is True
    mock_s3.head_object.assert_called_once_with(Bucket="test-bucket", Key="agents/user1/state.json")


async def test_exists_returns_false_when_object_missing() -> None:
    from botocore.exceptions import ClientError

    mock_s3 = MagicMock()
    error_response = {"Error": {"Code": "404", "Message": "Not Found"}}
    mock_s3.head_object.side_effect = ClientError(error_response, "HeadObject")
    client = _make_client(mock_s3)

    result = await client.exists("agents/user1/missing.json")

    assert result is False


async def test_exists_reraises_non_404_errors() -> None:
    from botocore.exceptions import ClientError

    mock_s3 = MagicMock()
    error_response = {"Error": {"Code": "403", "Message": "Forbidden"}}
    mock_s3.head_object.side_effect = ClientError(error_response, "HeadObject")
    client = _make_client(mock_s3)

    with pytest.raises(ClientError):
        await client.exists("agents/user1/secret.json")

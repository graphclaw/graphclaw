# ADR-0002: Fakes over mocks for boundary types

**Status**: Accepted  
**Date**: 2026-05-04

## Decision

Use in-memory fake implementations (`FakeGraphStore`, `FakeStorageClient`, `FakeSecretsClient`) rather than `unittest.mock.Mock` / `MagicMock` for the primary boundary types (graph, storage, secrets). Mocks are reserved for third-party libraries that have no equivalent fake in the codebase.

## Context

Mocks couple tests to implementation details (which methods were called, with what arguments) rather than to behaviour (the system stored the value and retrieved it correctly). When an internal refactor changes which private method is called, mock-based tests fail even when behaviour is unchanged. This creates noisy CI and erodes trust in the test suite.

In-memory fakes implement the same interface as the production dependency. They respond to real method calls with real (in-memory) data. Tests that pass against a fake are more likely to pass against the real dependency.

## Consequences

- `FakeGraphStore` lives in `tests/fixtures/fakes.py` and implements the full `GraphStore` protocol. Tests call real methods (`create_node`, `get_node`, `run_cypher`) against in-memory state.
- `FakeStorageClient` implements the `StorageClient` protocol in memory. `get_object` returns the bytes that `put_object` stored.
- FastAPI tests in `tests/test_api/` use `app.dependency_overrides` to inject fakes: `app.dependency_overrides[get_graph_store] = lambda: FakeGraphStore()`.
- New boundary types (e.g., a caching layer) must have a fake added to `tests/fixtures/fakes.py` before tests are written against them. The fake is the specification.
- `unittest.mock.Mock` is still appropriate for: third-party HTTP clients (e.g., the Telegram bot SDK), time/random for determinism, and asserting that a side-effect-only function was called exactly once.

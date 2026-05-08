# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
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
"""tests.test_infra.test_cicd — Structural tests for CI/CD pipeline artefacts.

Verifies that all GitHub Actions workflow files, stub Dockerfiles, ruff
configuration, and the migrate script are present in the repository.  These
are purely file-existence / content-structure checks — no processes are
spawned and no cloud credentials are required.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Resolve the repository root relative to this test file's location so the
# tests remain correct regardless of the working directory at invocation time.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _repo(rel: str) -> Path:
    """Return an absolute path under the repository root."""
    return _REPO_ROOT / rel


# ---------------------------------------------------------------------------
# GitHub Actions workflow existence tests
# ---------------------------------------------------------------------------


def test_ci_workflow_exists() -> None:
    """ci.yml must exist in .github/workflows/."""
    assert _repo(".github/workflows/ci.yml").is_file(), "Missing .github/workflows/ci.yml"


def test_build_push_workflow_exists() -> None:
    """build-push.yml must exist in .github/workflows/."""
    assert _repo(".github/workflows/build-push.yml").is_file(), (
        "Missing .github/workflows/build-push.yml"
    )


def test_deploy_workflow_exists() -> None:
    """deploy.yml must exist in .github/workflows/."""
    assert _repo(".github/workflows/deploy.yml").is_file(), "Missing .github/workflows/deploy.yml"


# ---------------------------------------------------------------------------
# ci.yml content checks
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ci_yml_text() -> str:
    """Return the raw text of ci.yml."""
    return _repo(".github/workflows/ci.yml").read_text(encoding="utf-8")


def test_ci_workflow_has_test_job(ci_yml_text: str) -> None:
    """ci.yml must define a 'test:' job."""
    assert "test:" in ci_yml_text, "ci.yml is missing the 'test:' job"


def test_ci_workflow_has_lint_job(ci_yml_text: str) -> None:
    """ci.yml must define a 'lint:' job."""
    assert "lint:" in ci_yml_text, "ci.yml is missing the 'lint:' job"


def test_ci_workflow_has_security_scan(ci_yml_text: str) -> None:
    """ci.yml must reference bandit for security scanning."""
    assert "bandit" in ci_yml_text, "ci.yml is missing the bandit security scan"


# ---------------------------------------------------------------------------
# Dockerfile existence tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dockerfile",
    [
        "docker/Dockerfile.api",
        "docker/Dockerfile.gateway",
        "docker/Dockerfile.agent",
        "docker/Dockerfile.trigger",
    ],
)
def test_all_dockerfiles_exist(dockerfile: str) -> None:
    """All four service stub Dockerfiles must be present."""
    assert _repo(dockerfile).is_file(), f"Missing {dockerfile}"


# ---------------------------------------------------------------------------
# Tooling config existence tests
# ---------------------------------------------------------------------------


def test_ruff_config_exists() -> None:
    """Ruff configuration must exist — either ruff.toml or [tool.ruff] in pyproject.toml."""
    ruff_toml = _repo("ruff.toml")
    pyproject = _repo("pyproject.toml")
    if ruff_toml.is_file():
        return  # standalone ruff.toml found
    assert pyproject.is_file(), "Missing both ruff.toml and pyproject.toml"
    assert "[tool.ruff]" in pyproject.read_text(encoding="utf-8"), (
        "pyproject.toml exists but contains no [tool.ruff] section"
    )


# ---------------------------------------------------------------------------
# Migration script existence test
# ---------------------------------------------------------------------------


def test_migrate_script_exists() -> None:
    """scripts/migrate.py must exist (created in WS-5-F)."""
    assert _repo("scripts/migrate.py").is_file(), "Missing scripts/migrate.py"

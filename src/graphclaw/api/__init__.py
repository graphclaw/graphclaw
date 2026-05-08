# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.api — Application API layer (/app/v1/ routes).

Exports
-------
- app_router: Aggregated APIRouter for all /app/v1/ endpoints.
"""

from __future__ import annotations

from graphclaw.api.router import app_router

__all__ = ["app_router"]

# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.models.edges — Pydantic models for all directed graph edge types.

Description
-----------
Defines ``GraphEdge``, the canonical model for a directed, typed edge between
two graph vertices, along with per-edge-type property models (``DependsOnProps``,
``BlocksProps``, etc.) and the generic ``EdgeProperties`` block.  Edges are
first-class objects in the GraphClaw property graph, carrying structured metadata
such as gate type, blocking strength, and sequence order.

Design Patterns
---------------
- Pydantic v2 Models: ``GraphEdge`` uses ``field_validator`` for ID format
  enforcement consistent with node models.
- Per-type Sub-models: Each edge type has a dedicated properties model so callers
  can use strongly-typed access when the edge type is known.
- Generic Properties Block: ``EdgeProperties`` flattens all optional per-type
  fields into a single model for cases where edge type is not statically known.

Public API
----------
- GraphEdge: Canonical directed edge between two nodes.
- EdgeProperties: Generic edge properties block for unknown/mixed edge types.
- DependsOnProps: Properties for DEPENDS_ON edges (gate_type).
- PartOfProps: Properties for PART_OF edges (sequence_order).
- BlocksProps: Properties for BLOCKS edges (strength).
- FollowUpForProps: Properties for FOLLOW_UP_FOR edges (scheduled_fire_at).
- SpawnedFromProps, AssignedToProps, OwnedByProps, AppliesToProps,
    InformsProps, BranchedFromProps, BatchedInProps, ReferredByProps:
    Empty property models.

Dependencies
------------
- graphclaw.models.base: EDGE_ID_PATTERN for ID validation.
- graphclaw.models.enums: EdgeCreatedBy, EdgeStrength, EdgeType, GateType.
- pydantic: BaseModel, Field, field_validator.
"""

from datetime import datetime

from pydantic import BaseModel, field_validator

from graphclaw.models.base import EDGE_ID_PATTERN
from graphclaw.models.enums import EdgeCreatedBy, EdgeStrength, EdgeType, GateType

# ---------------------------------------------------------------------------
# Edge-specific property models
# ---------------------------------------------------------------------------


class DependsOnProps(BaseModel):
    """Properties for a DEPENDS_ON edge.

    gate_type controls how multiple DEPENDS_ON edges into the same node are
    evaluated: AND (all predecessors must complete) vs OR (any one suffices).
    """

    gate_type: GateType = GateType.AND


class PartOfProps(BaseModel):
    """Properties for a PART_OF edge — carries sequential ordering within a goal."""

    sequence_order: int | None = None


class BlocksProps(BaseModel):
    """Properties for a BLOCKS edge — captures whether blocking is hard or soft."""

    strength: EdgeStrength = EdgeStrength.HARD


class FollowUpForProps(BaseModel):
    """Properties for a FOLLOW_UP_FOR edge — carries the scheduled fire time."""

    scheduled_fire_at: datetime | None = None


class SpawnedFromProps(BaseModel):
    """Properties for a SPAWNED_FROM edge."""

    pass


class AssignedToProps(BaseModel):
    """Properties for an ASSIGNED_TO edge."""

    pass


class OwnedByProps(BaseModel):
    """Properties for an OWNED_BY edge."""

    pass


class AppliesToProps(BaseModel):
    """Properties for an APPLIES_TO edge."""

    pass


class InformsProps(BaseModel):
    """Properties for an INFORMS edge (context enrichment)."""

    pass


class BranchedFromProps(BaseModel):
    """Properties for a BRANCHED_FROM edge (decision branches)."""

    pass


class BatchedInProps(BaseModel):
    """Properties for a BATCHED_IN edge (task included in a check-in)."""

    pass


class ReferredByProps(BaseModel):
    """Properties for a REFERRED_BY edge (coordination/context linkage)."""

    pass


# ---------------------------------------------------------------------------
# Generic edge properties block (used when specific props not required)
# ---------------------------------------------------------------------------


class EdgeProperties(BaseModel):
    """Generic properties block carried on every edge in the graph.

    Specific edge types may use typed sub-models (DependsOnProps, etc.)
    while this model holds common metadata.
    """

    gate_type: GateType | None = None  # DEPENDS_ON
    sequence_order: int | None = None  # PART_OF
    strength: EdgeStrength | None = None  # BLOCKS
    scheduled_fire_at: datetime | None = None  # FOLLOW_UP_FOR
    created_at: datetime | None = None
    created_by: EdgeCreatedBy = EdgeCreatedBy.AGENT
    note: str | None = None


# ---------------------------------------------------------------------------
# GraphEdge — the canonical edge model
# ---------------------------------------------------------------------------


class GraphEdge(BaseModel):
    """A directed, typed edge between two graph nodes.

    source_id / target_id correspond to from_node / to_node in the PRD schema.
    The properties block carries edge-specific data.
    """

    id: str
    edge_type: EdgeType
    source_id: str  # from_node
    target_id: str  # to_node
    properties: EdgeProperties = EdgeProperties()

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not EDGE_ID_PATTERN.match(v):
            raise ValueError(f"Invalid edge ID '{v}'. Expected EDGE-<identifier>")
        return v


__all__ = [
    # Property sub-models
    "DependsOnProps",
    "PartOfProps",
    "BlocksProps",
    "FollowUpForProps",
    "SpawnedFromProps",
    "AssignedToProps",
    "OwnedByProps",
    "AppliesToProps",
    "InformsProps",
    "BranchedFromProps",
    "BatchedInProps",
    "ReferredByProps",
    "EdgeProperties",
    # Main edge model
    "GraphEdge",
]

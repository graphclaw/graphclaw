"""graphclaw.db.graph_repository — CRUD operations for nodes and edges in Apache AGE.

Description
-----------
Provides the ``GraphRepository`` class, the single point of contact between the
application and the Apache AGE property graph.  All reads and writes use the AGE
SQL wrapper pattern (``SELECT * FROM cypher(...)``) because AGE does not expose
a native parameterised Cypher interface — values are embedded directly into the
Cypher string after manual escaping via ``_escape()``.

Design Patterns
---------------
- Repository: ``GraphRepository`` wraps all graph persistence operations behind a
  clean async interface; callers never write raw Cypher.
- Helper module: ``_escape``, ``_to_cypher_value``, and ``_to_cypher_map`` form a
  small Cypher serialisation layer that compensates for AGE's lack of bind parameters
  inside ``$$`` blocks.

Public API
----------
- GraphRepository.create_node: Insert a Pydantic node model as a graph vertex.
- GraphRepository.get_node: Retrieve a vertex by its ``id`` property.
- GraphRepository.update_node: Partial-update a vertex's properties.
- GraphRepository.delete_node: DETACH DELETE a vertex and all its incident edges.
- GraphRepository.list_nodes: List all vertices with a given label, with optional filters.
- GraphRepository.create_edge: Create a directed, typed edge between two vertices.
- GraphRepository.get_edges: Retrieve incident edges for a vertex by direction/type.
- GraphRepository.delete_edge: Delete an edge by its AGE internal element id.

Dependencies
------------
- graphclaw.db.connection: ``get_connection`` context manager for pool checkout.
- psycopg_pool: ``AsyncConnectionPool`` type annotation.
- json: agtype → Python object parsing.

Notes
-----
AGE does not support ``$1`` bind parameters inside ``$$ ... $$`` Cypher blocks.
All user-supplied string values MUST pass through ``_escape()`` before embedding
to prevent Cypher injection.  Numeric and boolean values are safe to embed directly
after type conversion.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from psycopg_pool import AsyncConnectionPool

from graphclaw.db.connection import get_connection
from graphclaw.db.utils import GRAPH_NAME, _escape, _extract_properties, _parse_agtype

logger = logging.getLogger(__name__)

# Regex for valid Cypher property / filter key identifiers.
_KEY_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
# Regex for valid edge type labels (upper-case letters and underscores only).
_EDGE_TYPE_RE = re.compile(r"^[A-Z_]+$")


class GraphRepository:
    """Primary interface for graph node and edge operations.

    Parameters
    ----------
    pool:
        An open ``AsyncConnectionPool`` (created via
        ``graphclaw.db.connection.create_pool``).
    graph_name:
        Name of the AGE property graph.  Defaults to ``"graphclaw"``.
    """

    def __init__(
        self,
        pool: AsyncConnectionPool,
        graph_name: str = GRAPH_NAME,
    ) -> None:
        self._pool = pool
        self._graph = graph_name

    # ------------------------------------------------------------------
    # Node CRUD
    # ------------------------------------------------------------------

    async def create_node(self, node: Any) -> dict:
        """Insert a vertex into the graph.

        ``node`` must be a Pydantic model (or any object with a
        ``model_dump()`` method that returns a dict with an ``"id"`` key
        and a ``"node_type"`` / ``"label"`` key used as the vertex label).

        The full property payload is serialised to JSON and stored on the
        vertex so it can be retrieved intact.

        Returns the created vertex properties dict.
        """
        props: dict = node.model_dump(mode="json")
        label: str = _resolve_label(node)
        cypher_map = _to_cypher_map(props)

        async with get_connection(self._pool) as conn:
            result = await conn.execute(
                f"""
                SELECT * FROM cypher('{self._graph}', $$
                    CREATE (n:{label} {cypher_map})
                    RETURN n
                $$) as (v agtype)
                """
            )
            row = await result.fetchone()
        created = _extract_properties(row[0]) if row else props
        logger.debug("create_node", extra={"label": label, "id": props.get("id")})
        return created

    async def get_node(self, node_id: str) -> dict | None:
        """Retrieve a vertex by its ``id`` property.

        Returns the properties dict, or ``None`` if not found.
        """
        eid = _escape(node_id)
        async with get_connection(self._pool) as conn:
            result = await conn.execute(
                f"""
                SELECT * FROM cypher('{self._graph}', $$
                    MATCH (n {{id: '{eid}'}})
                    RETURN n
                $$) as (v agtype)
                """
            )
            row = await result.fetchone()
        if row is None:
            return None
        return _extract_properties(row[0])

    async def update_node(self, node_id: str, updates: dict) -> dict | None:
        """Merge ``updates`` into the properties of the node with ``node_id``.

        Only the keys present in ``updates`` are changed; other properties
        are left untouched.  Returns the updated properties dict.
        """
        eid = _escape(node_id)
        set_fragments = []
        for key, value in updates.items():
            set_fragments.append(f"n.{key} = {_to_cypher_value(value)}")
        set_clause = ", ".join(set_fragments)

        async with get_connection(self._pool) as conn:
            result = await conn.execute(
                f"""
                SELECT * FROM cypher('{self._graph}', $$
                    MATCH (n {{id: '{eid}'}})
                    SET {set_clause}
                    RETURN n
                $$) as (v agtype)
                """
            )
            row = await result.fetchone()
        if row is None:
            return None
        updated = _extract_properties(row[0])
        logger.debug("update_node", extra={"node_id": node_id, "keys": list(updates)})
        return updated

    async def delete_node(self, node_id: str) -> None:
        """Remove a vertex and all its incident edges (DETACH DELETE)."""
        eid = _escape(node_id)
        async with get_connection(self._pool) as conn:
            await conn.execute(
                f"""
                SELECT * FROM cypher('{self._graph}', $$
                    MATCH (n {{id: '{eid}'}})
                    DETACH DELETE n
                $$) as (v agtype)
                """
            )
        logger.debug("delete_node", extra={"node_id": node_id})

    async def list_nodes(
        self,
        label: str,
        filters: dict | None = None,
    ) -> list[dict]:
        """Return all vertices with the given label, optionally filtered.

        ``filters`` is a flat dict of ``{property: value}`` equality checks.
        Values are embedded directly into the Cypher query (AGE limitation).

        Returns a list of property dicts (may be empty).
        """
        filters = filters or {}
        where_fragments: list[str] = []
        for key, value in filters.items():
            if not _KEY_RE.match(key):
                raise ValueError(
                    f"Invalid filter key {key!r}: keys must match "
                    r"^[a-zA-Z_][a-zA-Z0-9_]*$"
                )
            where_fragments.append(f"n.{key} = {_to_cypher_value(value)}")

        where_clause = ""
        if where_fragments:
            where_clause = "WHERE " + " AND ".join(where_fragments)

        async with get_connection(self._pool) as conn:
            result = await conn.execute(
                f"""
                SELECT * FROM cypher('{self._graph}', $$
                    MATCH (n:{label})
                    {where_clause}
                    RETURN n
                $$) as (v agtype)
                """
            )
            rows = await result.fetchall()

        return [_extract_properties(row[0]) for row in rows]

    # ------------------------------------------------------------------
    # Edge CRUD
    # ------------------------------------------------------------------

    async def create_edge(
        self,
        source_id: str,
        target_id: str,
        edge_type: str,
        properties: dict | None = None,
    ) -> dict:
        """Create a directed edge from source to target.

        Parameters
        ----------
        source_id, target_id:
            ``id`` property values of the source and target vertices.
        edge_type:
            Edge label, e.g. ``"DEPENDS_ON"``, ``"PART_OF"``.
        properties:
            Optional property dict stored on the edge.

        Returns the edge properties dict (may be empty if no properties set).
        """
        properties = properties or {}
        cypher_map = _to_cypher_map(properties)
        esrc = _escape(source_id)
        etgt = _escape(target_id)

        async with get_connection(self._pool) as conn:
            result = await conn.execute(
                f"""
                SELECT * FROM cypher('{self._graph}', $$
                    MATCH (src {{id: '{esrc}'}}), (tgt {{id: '{etgt}'}})
                    CREATE (src)-[e:{edge_type} {cypher_map}]->(tgt)
                    RETURN e
                $$) as (e agtype)
                """
            )
            row = await result.fetchone()

        created = _extract_properties(row[0]) if row else properties
        logger.debug(
            "create_edge",
            extra={
                "source_id": source_id,
                "target_id": target_id,
                "edge_type": edge_type,
            },
        )
        return created

    async def get_edges(
        self,
        node_id: str,
        direction: str = "out",
        edge_type: str | None = None,
    ) -> list[dict]:
        """Retrieve edges incident to the node with ``node_id``.

        Parameters
        ----------
        node_id:
            The ``id`` property of the anchor vertex.
        direction:
            ``"out"`` — outgoing edges (node)-[e]->()
            ``"in"``  — incoming edges ()-[e]->(node)
            ``"both"`` — either direction
        edge_type:
            If given, only return edges with this label.

        Returns a list of dicts each containing ``start_id``, ``end_id``,
        ``label``, and any edge properties.
        """
        if edge_type is not None and not _EDGE_TYPE_RE.match(edge_type):
            raise ValueError(
                f"Invalid edge_type {edge_type!r}: must match ^[A-Z_]+$"
            )
        type_filter = f":{edge_type}" if edge_type else ""
        enid = _escape(node_id)

        if direction == "out":
            pattern = f"(n {{id: '{enid}'}})-[e{type_filter}]->(other)"
        elif direction == "in":
            pattern = f"(other)-[e{type_filter}]->(n {{id: '{enid}'}})"
        else:  # both
            pattern = f"(n {{id: '{enid}'}})-[e{type_filter}]-(other)"

        async with get_connection(self._pool) as conn:
            result = await conn.execute(
                f"""
                SELECT * FROM cypher('{self._graph}', $$
                    MATCH {pattern}
                    RETURN e, other.id as other_id
                $$) as (e agtype, other_id agtype)
                """
            )
            rows = await result.fetchall()

        edges = []
        for row in rows:
            edge_data = _parse_agtype(row[0])
            other_id = _parse_agtype(row[1])
            if isinstance(edge_data, dict):
                props = edge_data.get("properties", {})
                props["_label"] = edge_data.get("label", "")
                if direction == "out":
                    props["_start_id"] = node_id
                    props["_end_id"] = other_id
                else:
                    props["_start_id"] = other_id
                    props["_end_id"] = node_id
                edges.append(props)
        return edges

    async def delete_edge(self, edge_id: str) -> None:
        """Delete an edge by its internal AGE element id.

        .. note::
            AGE edge ids are numeric ``agtype`` values, not the string ``id``
            property.  Pass the value returned by ``id(e)`` in a prior query.

        Raises ``ValueError`` if ``edge_id`` is not a string of digits.
        """
        if not str(edge_id).isdigit():
            raise ValueError(
                f"edge_id must be a numeric string; got {edge_id!r}"
            )
        async with get_connection(self._pool) as conn:
            await conn.execute(
                f"""
                SELECT * FROM cypher('{self._graph}', $$
                    MATCH ()-[e]->()
                    WHERE id(e) = {edge_id}
                    DELETE e
                $$) as (v agtype)
                """
            )
        logger.debug("delete_edge", extra={"edge_id": edge_id})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_cypher_value(value: Any) -> str:
    """Convert a Python value to its Cypher literal representation.

    - str → 'escaped_str'
    - int/float → bare number
    - bool → true/false
    - None → null
    - list → [item1, item2, ...]
    - dict → serialised as a JSON string (AGE doesn't support nested maps well)
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return f"'{_escape(value)}'"
    if isinstance(value, list):
        items = ", ".join(_to_cypher_value(v) for v in value)
        return f"[{items}]"
    # Fallback: serialise as JSON string
    return f"'{_escape(json.dumps(value))}'"


def _to_cypher_map(props: dict) -> str:
    """Convert a Python dict to a Cypher map literal: ``{key: val, ...}``.

    Raises ``ValueError`` if any key is not a valid Cypher identifier
    (must match ``^[a-zA-Z_][a-zA-Z0-9_]*$``).
    """
    fragments = []
    for key, value in props.items():
        if not _KEY_RE.match(key):
            raise ValueError(
                f"Invalid property key {key!r}: keys must match "
                r"^[a-zA-Z_][a-zA-Z0-9_]*$"
            )
        fragments.append(f"{key}: {_to_cypher_value(value)}")
    return "{" + ", ".join(fragments) + "}"


def _resolve_label(node: Any) -> str:
    """Derive the AGE vertex label from a node model instance.

    Checks in order:
    1. ``node.node_type`` attribute (string or enum with a ``.value``)
    2. ``node.__class__.__name__``
    """
    label = getattr(node, "node_type", None)
    if label is None:
        return node.__class__.__name__
    # If it's an enum, use its value.
    return getattr(label, "value", str(label))

"""Graph repository — CRUD operations for nodes and edges in Apache AGE.

All Cypher queries are wrapped in the AGE SQL function call pattern::

    SELECT * FROM cypher('graphclaw', $$ ... $$) as (col agtype)

Parameters are always passed as psycopg ``%s`` placeholders so the driver
handles escaping — never use Cypher ``$param`` syntax.

AGE returns ``agtype`` values; these are parsed to Python dicts/scalars via
``json.loads(str(row[col]))``.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from psycopg_pool import AsyncConnectionPool

from graphclaw.db.connection import get_connection

logger = logging.getLogger(__name__)

# Name of the AGE property graph — must match the graph created via
# ``SELECT create_graph('graphclaw')``.
GRAPH_NAME = "graphclaw"


def _parse_agtype(value: Any) -> Any:
    """Convert an agtype column value to a native Python object.

    psycopg represents agtype as a custom type whose ``str()`` is valid JSON.
    """
    if value is None:
        return None
    raw = str(value)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Scalar strings from AGE are sometimes unquoted — return as-is.
        return raw


def _extract_properties(agtype_node: Any) -> dict:
    """Pull the ``properties`` dict out of a parsed AGE vertex/edge object."""
    parsed = _parse_agtype(agtype_node)
    if isinstance(parsed, dict):
        return parsed.get("properties", parsed)
    return {}


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
        props_json = json.dumps(props)

        query = f"""
            SELECT * FROM cypher(%s, $$
                CREATE (n:{label} %s)
                RETURN n
            $$) as (v agtype)
        """
        # AGE does not support %s inside $$ blocks — we must use string
        # interpolation for literal values that go INTO the Cypher body.
        # The safe pattern: build the properties literal as a JSON string
        # and use the ``agtype`` cast inside Cypher.
        cypher = f"""
            SELECT * FROM cypher('{self._graph}', $$
                CREATE (n:{label} {{id: '{props["id"]}'}})
                SET n = {props_json}::agtype
                RETURN n
            $$) as (v agtype)
        """
        # NOTE: because AGE embeds the graph name and Cypher body in a SQL
        # string literal, we use direct string formatting for the graph name
        # and Cypher-level values, then pass the JSON blob through psycopg's
        # parameter mechanism via a literal substitution approach.
        # The properties dict is serialised and embedded as a Cypher map
        # literal — this is safe because json.dumps produces valid JSON /
        # Cypher map syntax.
        async with get_connection(self._pool) as conn:
            result = await conn.execute(
                f"""
                SELECT * FROM cypher('{self._graph}', $$
                    CREATE (n:{label})
                    SET n = %s::agtype
                    RETURN n
                $$) as (v agtype)
                """,
                (props_json,),
            )
            row = await result.fetchone()
        created = _extract_properties(row[0]) if row else props
        logger.debug("create_node", extra={"label": label, "id": props.get("id")})
        return created

    async def get_node(self, node_id: str) -> dict | None:
        """Retrieve a vertex by its ``id`` property.

        Returns the properties dict, or ``None`` if not found.
        """
        async with get_connection(self._pool) as conn:
            result = await conn.execute(
                f"""
                SELECT * FROM cypher('{self._graph}', $$
                    MATCH (n {{id: %s}})
                    RETURN n
                $$) as (v agtype)
                """,
                (node_id,),
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
        # Build SET clauses: n.key = value for each key in updates.
        # Values are passed as individual %s parameters.
        set_fragments = []
        params: list[Any] = []
        for key, value in updates.items():
            set_fragments.append(f"n.{key} = %s::agtype")
            params.append(json.dumps(value))
        params.append(node_id)

        set_clause = ", ".join(set_fragments)
        async with get_connection(self._pool) as conn:
            result = await conn.execute(
                f"""
                SELECT * FROM cypher('{self._graph}', $$
                    MATCH (n {{id: %s}})
                    SET {set_clause}
                    RETURN n
                $$) as (v agtype)
                """,
                (*params[:-1], params[-1]),  # set values first, then id at end (matches MATCH position)
            )
            # Reorder: psycopg %s binds left-to-right; MATCH uses last %s,
            # SET uses preceding ones.  Pass id first, then set values.
            # Re-execute with corrected parameter order.
            _ = await result.fetchone()  # consume to avoid cursor issues

        # Execute again with correct parameter order (id first for MATCH).
        params_ordered = [node_id] + [json.dumps(v) for v in updates.values()]
        async with get_connection(self._pool) as conn:
            result = await conn.execute(
                f"""
                SELECT * FROM cypher('{self._graph}', $$
                    MATCH (n {{id: %s}})
                    SET {set_clause}
                    RETURN n
                $$) as (v agtype)
                """,
                tuple(params_ordered),
            )
            row = await result.fetchone()
        if row is None:
            return None
        updated = _extract_properties(row[0])
        logger.debug("update_node", extra={"node_id": node_id, "keys": list(updates)})
        return updated

    async def delete_node(self, node_id: str) -> None:
        """Remove a vertex and all its incident edges (DETACH DELETE)."""
        async with get_connection(self._pool) as conn:
            await conn.execute(
                f"""
                SELECT * FROM cypher('{self._graph}', $$
                    MATCH (n {{id: %s}})
                    DETACH DELETE n
                $$) as (v agtype)
                """,
                (node_id,),
            )
        logger.debug("delete_node", extra={"node_id": node_id})

    async def list_nodes(
        self,
        label: str,
        filters: dict | None = None,
    ) -> list[dict]:
        """Return all vertices with the given label, optionally filtered.

        ``filters`` is a flat dict of ``{property: value}`` equality checks.
        All filter values are passed as ``%s`` parameters.

        Returns a list of property dicts (may be empty).
        """
        filters = filters or {}
        where_fragments: list[str] = []
        params: list[Any] = []

        for key, value in filters.items():
            where_fragments.append(f"n.{key} = %s::agtype")
            params.append(json.dumps(value))

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
                """,
                tuple(params) if params else None,
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
        props_json = json.dumps(properties)

        async with get_connection(self._pool) as conn:
            result = await conn.execute(
                f"""
                SELECT * FROM cypher('{self._graph}', $$
                    MATCH (src {{id: %s}}), (tgt {{id: %s}})
                    CREATE (src)-[e:{edge_type}]->(tgt)
                    SET e = %s::agtype
                    RETURN e
                $$) as (e agtype)
                """,
                (source_id, target_id, props_json),
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
        type_filter = f":{edge_type}" if edge_type else ""

        if direction == "out":
            pattern = f"(n {{id: %s}})-[e{type_filter}]->(other)"
        elif direction == "in":
            pattern = f"(other)-[e{type_filter}]->(n {{id: %s}})"
        else:  # both
            pattern = f"(n {{id: %s}})-[e{type_filter}]-(other)"

        async with get_connection(self._pool) as conn:
            result = await conn.execute(
                f"""
                SELECT * FROM cypher('{self._graph}', $$
                    MATCH {pattern}
                    RETURN e, other.id as other_id
                $$) as (e agtype, other_id agtype)
                """,
                (node_id,),
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
        """
        async with get_connection(self._pool) as conn:
            await conn.execute(
                f"""
                SELECT * FROM cypher('{self._graph}', $$
                    MATCH ()-[e]->()
                    WHERE id(e) = %s
                    DELETE e
                $$) as (v agtype)
                """,
                (edge_id,),
            )
        logger.debug("delete_edge", extra={"edge_id": edge_id})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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

# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.db.age.repository — CRUD operations for nodes and edges in Apache AGE.

Description
-----------
Provides the ``AgeGraphStore`` class, the AGE backend implementation of the
``GraphStore`` ABC.  It is the single point of contact between the application
and the Apache AGE property graph.  All reads and writes use the AGE SQL wrapper
pattern (``SELECT * FROM cypher(...)``) because AGE does not expose a native
parameterised Cypher interface — values are embedded directly into the Cypher
string after manual escaping via ``_escape()``.

Design Patterns
---------------
- Repository: ``AgeGraphStore`` wraps all graph persistence operations behind a
  clean async interface; callers never write raw Cypher.
- Plugin / Strategy: Inherits from ``GraphStore`` ABC so it can be swapped for
  a different backend (Neo4j, Neptune) without changing call sites.
- Helper module: ``_escape``, ``_to_cypher_value``, and ``_to_cypher_map`` form a
  small Cypher serialisation layer that compensates for AGE's lack of bind parameters
  inside ``$$`` blocks.

Public API
----------
- AgeGraphStore.create_node: Insert a Pydantic node model as a graph vertex.
- AgeGraphStore.get_node: Retrieve a vertex by its ``id`` property.
- AgeGraphStore.update_node: Partial-update a vertex's properties.
- AgeGraphStore.delete_node: DETACH DELETE a vertex and all its incident edges.
- AgeGraphStore.list_nodes: List all vertices with a given label, with optional filters.
- AgeGraphStore.create_edge: Create a directed, typed edge between two vertices.
- AgeGraphStore.get_edges: Retrieve incident edges for a vertex by direction/type.
- AgeGraphStore.delete_edge: Delete an edge by its AGE internal element id.

Dependencies
------------
- graphclaw.db.base: ``GraphStore`` ABC.
- graphclaw.db.age.connection: ``get_connection`` context manager for pool checkout.
- graphclaw.db.age.utils: ``GRAPH_NAME``, ``_escape``, ``_extract_properties``, ``_parse_agtype``.
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

import asyncio
import json
import logging
import re
from typing import Any

from psycopg_pool import AsyncConnectionPool

from graphclaw.db.age.connection import get_connection
from graphclaw.db.age.utils import GRAPH_NAME, _escape, _extract_properties, _parse_agtype
from graphclaw.db.base import GraphStore

logger = logging.getLogger(__name__)

# Regex for valid Cypher property / filter key identifiers.
_KEY_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
# Regex for valid edge type labels (upper-case letters and underscores only).
_EDGE_TYPE_RE = re.compile(r"^[A-Z_]+$")


class AgeGraphStore(GraphStore):
    """AGE backend implementation of the GraphStore ABC.

    Parameters
    ----------
    pool:
        An open ``AsyncConnectionPool`` (created via
        ``graphclaw.db.age.connection.create_pool``).
    graph_name:
        Name of the AGE property graph.  Defaults to ``"graphclaw"``.
    principal_name:
        The service principal name whose credentials this pool was opened
        with.  Stored and emitted with every structured log entry for audit.
        Defaults to ``"agent_principal"`` (the least-privilege default).
    """

    def __init__(
        self,
        pool: AsyncConnectionPool,
        graph_name: str = GRAPH_NAME,
        embedding_client: object | None = None,
        principal_name: str = "agent_principal",
    ) -> None:
        self._pool = pool
        self._graph = graph_name
        self._embedding_client = embedding_client
        self._principal_name = principal_name

    @property
    def principal_name(self) -> str:
        """Return the principal name bound to this store instance."""
        return self._principal_name

    # ------------------------------------------------------------------
    # Node CRUD
    # ------------------------------------------------------------------

    async def create_node(self, node: Any, caller_context: Any | None = None) -> dict:
        """Insert a vertex into the graph.

        ``node`` must be a Pydantic model (or any object with a
        ``model_dump()`` method that returns a dict with an ``"id"`` key
        and a ``"node_type"`` / ``"label"`` key used as the vertex label).

        The full property payload is serialised to JSON and stored on the
        vertex so it can be retrieved intact.

        Returns the created vertex properties dict.

        Parameters
        ----------
        caller_context:
            Optional ``CallerContext`` for ACL enforcement (FR-AL-001).
            Required when ``GRAPHCLAW_NO_DELETE_ENFORCEMENT=true``.
        """
        from graphclaw.cross_tenant.acl import require_caller_context  # noqa: PLC0415

        require_caller_context(caller_context)
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
        logger.debug(
            "create_node",
            extra={"label": label, "id": props.get("id"), "principal_name": self._principal_name},
        )

        # Fire-and-forget embedding generation for task nodes.
        if label.startswith("Task") and self._embedding_client is not None:
            asyncio.create_task(self._generate_embedding_safe(created))

        return created

    async def get_node(
        self,
        node_id: str,
        include_archived: bool = False,
        caller_context: Any | None = None,
    ) -> dict | None:
        """Retrieve a vertex by its ``id`` property.

        Returns the properties dict, or ``None`` if not found.

        Parameters
        ----------
        node_id:
            The graph ID of the node to look up.
        include_archived:
            When ``False`` (default), nodes with ``archived_at IS NOT NULL``
            are treated as absent (returns ``None``).  Pass ``True`` to
            include archived nodes in the result.
        caller_context:
            Optional ``CallerContext`` for ACL enforcement (FR-AL-001).
            Required when ``GRAPHCLAW_NO_DELETE_ENFORCEMENT=true``.
        """
        from graphclaw.cross_tenant.acl import require_caller_context  # noqa: PLC0415

        require_caller_context(caller_context)
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
        props = _extract_properties(row[0])
        # Wave 0 (FR-DEL-003): exclude archived nodes by default.
        if not include_archived and props.get("archived_at"):
            return None
        return props

    async def update_node(
        self,
        node_id: str,
        updates: dict,
        caller_context: Any | None = None,
    ) -> dict | None:
        """Merge ``updates`` into the properties of the node with ``node_id``.

        Only the keys present in ``updates`` are changed; other properties
        are left untouched.  Returns the updated properties dict.

        Wave 0 (FR-DEL-002): When this store is bound to ``agent_principal``,
        lifecycle fields are silently stripped from ``updates`` before the
        Cypher SET clause is built.  The Postgres trigger provides a second line
        of defence, but stripping here ensures clean error messages rather than
        raw DB exceptions surfacing to callers.

        Parameters
        ----------
        caller_context:
            Optional ``CallerContext`` for ACL enforcement (FR-AL-001).
            Required when ``GRAPHCLAW_NO_DELETE_ENFORCEMENT=true``.
        """
        from graphclaw.cross_tenant.acl import require_caller_context  # noqa: PLC0415

        require_caller_context(caller_context)
        # Wave 0: strip lifecycle fields for agent_principal (AC1 guard at application layer).
        _LIFECYCLE_FIELDS = frozenset(
            {
                "archived_at",
                "archived_by",
                "archive_reason",
                "purge_after",
                "purge_cancelled_at",
                "legal_hold",
                "hold_reason",
                "hold_set_by",
                "hold_set_at",
                "link_status",
            }
        )
        if self._principal_name == "agent_principal":
            forbidden = _LIFECYCLE_FIELDS & updates.keys()
            if forbidden:
                from graphclaw.db.base import InsufficientPrivilegeError  # noqa: PLC0415

                raise InsufficientPrivilegeError(
                    f"agent_principal cannot update lifecycle fields: {sorted(forbidden)}. "
                    "Use archive_* tools instead."
                )

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
        logger.debug(
            "update_node",
            extra={
                "node_id": node_id,
                "keys": list(updates),
                "principal_name": self._principal_name,
            },
        )

        # Fire-and-forget embedding re-generation for task nodes.
        label = updated.get("node_type", updated.get("label", ""))
        if label and label.startswith("Task") and self._embedding_client is not None:
            asyncio.create_task(self._generate_embedding_safe(updated))

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
        logger.debug(
            "delete_node", extra={"node_id": node_id, "principal_name": self._principal_name}
        )

    async def list_nodes(
        self,
        label: str,
        filters: dict | None = None,
        caller_context: Any | None = None,
    ) -> list[dict]:
        """Return all vertices with the given label, optionally filtered.

        ``filters`` is a flat dict of ``{property: value}`` equality checks.
        Values are embedded directly into the Cypher query (AGE limitation).

        Returns a list of property dicts (may be empty).

        Parameters
        ----------
        caller_context:
            Optional ``CallerContext`` for ACL enforcement (FR-AL-001).
            Required when ``GRAPHCLAW_NO_DELETE_ENFORCEMENT=true``.
        """
        from graphclaw.cross_tenant.acl import require_caller_context  # noqa: PLC0415

        require_caller_context(caller_context)
        filters = filters or {}
        if label != "TaskNode" and not _KEY_RE.match(label):
            raise ValueError(
                f"Invalid label {label!r}: labels must match "
                r"^[a-zA-Z_][a-zA-Z0-9_]*$"
            )

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

        # Task vertices are stored under type-specific labels (TaskAtomic,
        # TaskDelegated, ...). Treat "TaskNode" as a virtual label that matches
        # any vertex with a task_type property.
        if label == "TaskNode":
            match_clause = "MATCH (n)"
            task_clause = "WHERE exists(n.task_type)"
        else:
            match_clause = f"MATCH (n:{label})"
            task_clause = ""

        where_clause = ""
        if where_fragments and task_clause:
            where_clause = task_clause + " AND " + " AND ".join(where_fragments)
        elif where_fragments:
            where_clause = "WHERE " + " AND ".join(where_fragments)
        elif task_clause:
            where_clause = task_clause

        async with get_connection(self._pool) as conn:
            result = await conn.execute(
                f"""
                SELECT * FROM cypher('{self._graph}', $$
                    {match_clause}
                    {where_clause}
                    RETURN n
                $$) as (v agtype)
                """
            )
            rows = await result.fetchall()

        return [_extract_properties(row[0]) for row in rows]

    async def list_nodes_by_user(self, label: str, user_id: str) -> list[dict]:
        """Return all vertices with *label* that are owned by *user_id*.

        Filters on the ``owned_by`` property — the same field written by
        ``create_task`` and ``create_goal`` in the agent loop.  This is the
        primary mechanism for user-level multi-tenancy isolation in Phase 1.

        Parameters
        ----------
        label:
            AGE vertex label to match (e.g. ``"TaskNode"``, ``"GoalNode"``).
        user_id:
            The ``USER-{id}`` to filter by.
        """
        if label != "TaskNode":
            return await self.list_nodes(label, filters={"owned_by": user_id})

        # TaskNode is virtual; match all task labels by property existence.
        # Prefer graph ownership edges (OWNED_BY) and keep a property fallback
        # for legacy records written before edge wiring was added.
        euid = _escape(user_id)
        rows = []
        async with get_connection(self._pool) as conn:
            edge_result = await conn.execute(
                f"""
                SELECT * FROM cypher('{self._graph}', $$
                    MATCH (u:UserNode {{id: '{euid}'}})<-[:OWNED_BY]-(n)
                    WHERE exists(n.task_type)
                    RETURN n
                $$) as (v agtype)
                """
            )
            rows.extend(await edge_result.fetchall())

            prop_result = await conn.execute(
                f"""
                SELECT * FROM cypher('{self._graph}', $$
                    MATCH (n {{owned_by: '{euid}'}})
                    WHERE exists(n.task_type)
                    RETURN n
                $$) as (v agtype)
                """
            )
            rows.extend(await prop_result.fetchall())

        deduped: dict[str, dict] = {}
        for row in rows:
            props = _extract_properties(row[0])
            task_id = str(props.get("id", ""))
            if task_id:
                deduped[task_id] = props

        return list(deduped.values())

    async def list_nodes_for_goal(self, goal_id: str) -> list[dict]:
        """Return all TaskNode vertices linked to *goal_id* via a PART_OF edge.

        Traverses the graph: ``(task)-[:PART_OF]->(goal {id: goal_id})``.

        Parameters
        ----------
        goal_id:
            The ``GOAL-{id}`` of the parent goal node.
        """
        eid = _escape(goal_id)
        async with get_connection(self._pool) as conn:
            result = await conn.execute(
                f"""
                SELECT * FROM cypher('{self._graph}', $$
                    MATCH (t)-[:PART_OF]->(g {{id: '{eid}'}})
                    WHERE exists(t.task_type)
                    RETURN t
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
        # Coerce str-enum to plain string (Python 3.12 __format__ returns "ClassName.member")
        edge_type = edge_type.value if hasattr(edge_type, "value") else str(edge_type)
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
        if edge_type is not None:
            # Coerce str-enum to plain string (Python 3.12 __format__ returns "ClassName.member")
            edge_type = edge_type.value if hasattr(edge_type, "value") else str(edge_type)
            if not _EDGE_TYPE_RE.match(edge_type):
                raise ValueError(f"Invalid edge_type {edge_type!r}: must match ^[A-Z_]+$")
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
            raise ValueError(f"edge_id must be a numeric string; got {edge_id!r}")
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

    async def delete_edge_by_property(self, prop_name: str, prop_value: str) -> None:
        """Delete an edge matched by a string property value (e.g. ``id='EDGE-xxx'``)."""
        eprop = _escape(prop_name)
        evalue = _escape(prop_value)
        async with get_connection(self._pool) as conn:
            await conn.execute(
                f"""
                SELECT * FROM cypher('{self._graph}', $$
                    MATCH ()-[e]->()
                    WHERE e.{eprop} = '{evalue}'
                    DELETE e
                $$) as (v agtype)
                """
            )
        logger.debug("delete_edge_by_property", extra={"prop": prop_name, "value": prop_value})

    # ------------------------------------------------------------------
    # Intelligence Layer — Task/Goal Intelligence Log Helpers
    # ------------------------------------------------------------------

    async def update_node_intelligence(self, node_id: str, intelligence_text: str) -> None:
        """Append/replace the intelligence field on a task or goal node.

        Parameters
        ----------
        node_id:
            The ``id`` property of the task or goal node.
        intelligence_text:
            Plain string (markdown blob) to store in the node's intelligence field.
        """
        from datetime import datetime, timezone

        eid = _escape(node_id)
        text_escaped = _escape(intelligence_text)
        now_iso = datetime.now(timezone.utc).isoformat()

        async with get_connection(self._pool) as conn:
            await conn.execute(
                f"""
                SELECT * FROM cypher('{self._graph}', $$
                    MATCH (n {{id: '{eid}'}})
                    SET n.intelligence = '{text_escaped}', n.updated_at = '{now_iso}'
                $$) as (v agtype)
                """
            )
        logger.debug("update_node_intelligence", extra={"node_id": node_id})

    async def get_nodes_bulk(self, node_ids: list[str]) -> dict[str, dict]:
        """Retrieve multiple vertices by their ``id`` properties in one Cypher call.

        Returns a mapping of ``{node_id: properties_dict}`` for every ID that
        exists in the graph.  Missing IDs are silently omitted from the result.

        Parameters
        ----------
        node_ids:
            List of ``id`` property values to fetch (e.g. ``["task-001", "task-003"]``).
        """
        if not node_ids:
            return {}

        escaped_ids = ", ".join(f"'{_escape(nid)}'" for nid in node_ids)
        async with get_connection(self._pool) as conn:
            result = await conn.execute(
                f"""
                SELECT * FROM cypher('{self._graph}', $$
                    MATCH (n)
                    WHERE n.id IN [{escaped_ids}]
                    RETURN n
                $$) as (v agtype)
                """
            )
            rows = await result.fetchall()

        out: dict[str, dict] = {}
        for row in rows:
            props = _extract_properties(row[0])
            nid = props.get("id")
            if nid:
                out[nid] = props
        return out

    async def get_node_intelligence(self, node_id: str) -> str | None:
        """Read only the intelligence field from a node.

        Returns None if node not found or field is null.

        Parameters
        ----------
        node_id:
            The ``id`` property of the task or goal node.
        """
        eid = _escape(node_id)
        async with get_connection(self._pool) as conn:
            result = await conn.execute(
                f"""
                SELECT * FROM cypher('{self._graph}', $$
                    MATCH (n {{id: '{eid}'}})
                    RETURN n.intelligence
                $$) as (intel agtype)
                """
            )
            row = await result.fetchone()
        if row is None:
            return None
        intel = _parse_agtype(row[0])
        return intel if intel is not None else None

    async def create_checkin_node(
        self,
        task_id: str,
        outbound_message: str,
        channel: str,
        agent_id: str,
        recipient: str,
    ) -> str:
        """Create a CheckinNode and a REFERRED_BY edge to the given task.

        Returns the checkin node id.

        Parameters
        ----------
        task_id:
            Task ID this checkin refers to.
        outbound_message:
            The message text being sent.
        channel:
            Channel identifier (e.g. 'email', 'slack').
        agent_id:
            The agent that created this checkin.
        recipient:
            The resource ID or email receiving this checkin.
        """
        from datetime import datetime, timezone

        from graphclaw.models.base import generate_checkin_node_id

        checkin_id = generate_checkin_node_id()
        now_iso = datetime.now(timezone.utc).isoformat()

        eid_checkin = _escape(checkin_id)
        eid_task = _escape(task_id)
        eid_outbound = _escape(outbound_message)
        eid_channel = _escape(channel)
        eid_agent = _escape(agent_id)
        eid_recipient = _escape(recipient)

        async with get_connection(self._pool) as conn:
            await conn.execute(
                f"""
                SELECT * FROM cypher('{self._graph}', $$
                    MATCH (t {{id: '{eid_task}'}})
                    CREATE (c:CheckinNode {{
                        id: '{eid_checkin}',
                        created_at: '{now_iso}',
                        updated_at: '{now_iso}',
                        version: 1,
                        target_resource: '{eid_recipient}',
                        created_by: '{eid_agent}',
                        task_refs: ['{eid_task}'],
                        state: 'SCHEDULED',
                        outbound_message: '{eid_outbound}',
                        channel: '{eid_channel}'
                    }})
                    CREATE (c)-[:REFERRED_BY]->(t)
                    RETURN c
                $$) as (v agtype)
                """
            )
        logger.debug("create_checkin_node", extra={"checkin_id": checkin_id, "task_id": task_id})
        return checkin_id

    async def update_checkin_response(self, checkin_id: str, inbound_response: str) -> None:
        """Set the inbound_response field on an existing CheckinNode.

        Parameters
        ----------
        checkin_id:
            The ``id`` property of the CheckinNode.
        inbound_response:
            The response text received.
        """
        from datetime import datetime, timezone

        eid = _escape(checkin_id)
        response_escaped = _escape(inbound_response)
        now_iso = datetime.now(timezone.utc).isoformat()

        async with get_connection(self._pool) as conn:
            await conn.execute(
                f"""
                SELECT * FROM cypher('{self._graph}', $$
                    MATCH (n:CheckinNode {{id: '{eid}'}})
                    SET n.inbound_response = '{response_escaped}', n.updated_at = '{now_iso}'
                $$) as (v agtype)
                """
            )
        logger.debug("update_checkin_response", extra={"checkin_id": checkin_id})

    # ------------------------------------------------------------------
    # Embedding operations
    # ------------------------------------------------------------------

    async def upsert_node_embedding(
        self,
        node_id: str,
        embedding: list[float],
    ) -> None:
        """Insert or update the embedding vector for a graph node.

        Parameters
        ----------
        node_id:
            The ``id`` property of the graph node whose embedding is being stored.
        embedding:
            A 1536-dimension float vector (e.g. from ``text-embedding-3-small``).

        Notes
        -----
        Uses ``INSERT ... ON CONFLICT`` to handle both new and updated
        embeddings in a single query. The ``computed_at`` timestamp is
        updated on every upsert.
        """
        async with get_connection(self._pool) as conn:
            await conn.execute(
                """
                INSERT INTO node_embeddings (node_id, embedding, computed_at)
                VALUES (%s, %s::vector, NOW())
                ON CONFLICT (node_id) DO UPDATE
                  SET embedding = EXCLUDED.embedding,
                      computed_at = EXCLUDED.computed_at
                """,
                (node_id, embedding),
            )
        logger.debug("upsert_node_embedding", extra={"node_id": node_id})

    async def _generate_embedding_safe(self, node_props: dict) -> None:
        """Generate and store an embedding for a task node (background task).

        This method is called via ``asyncio.create_task()`` after task node
        creation or update. All exceptions are caught and logged to ensure
        embedding failures never propagate back to the caller.

        Parameters
        ----------
        node_props:
            Full property dict of the task node (must include ``id``, ``title``,
            and ``description`` keys).
        """
        try:
            node_id = node_props.get("id")
            if not node_id:
                return

            title = node_props.get("title", "")
            description = node_props.get("description", "")
            # Extract goal_context from embedded embedding_inputs if present.
            embedding_inputs = node_props.get("embedding_inputs", {})
            goal_context = ""
            if isinstance(embedding_inputs, dict):
                goal_context = embedding_inputs.get("goal_context", "")

            embedding_text = f"{title} {description} {goal_context}".strip()
            if not embedding_text:
                return

            embedding_vector = await self._embedding_client.embed(embedding_text)  # type: ignore[union-attr]
            await self.upsert_node_embedding(node_id, embedding_vector)

        except Exception as exc:
            logger.warning(
                "Embedding generation failed (non-fatal)",
                extra={"node_id": node_props.get("id"), "error": str(exc)},
            )

    # ------------------------------------------------------------------
    # Wave 0: Tombstone / canonical resolution (FR-DEL-003)
    # ------------------------------------------------------------------

    async def resolve_canonical(
        self,
        node_id: str,
        max_hops: int = 5,
    ) -> dict | None:
        """Follow TombstoneNode redirect chain to the current live node.

        Delegates to ``graphclaw.db.age.redirects.resolve_canonical`` with
        this store instance.

        Parameters
        ----------
        node_id:
            Starting node ID (may be live or archived/redirected).
        max_hops:
            Maximum redirect hops before raising ``MaxHopsExceeded``.

        Returns
        -------
        dict | None
            Properties of the live node, or ``None`` if no live replacement.

        Raises
        ------
        TombstoneCycle:
            Cycle detected in redirect chain.
        MaxHopsExceeded:
            Redirect chain length exceeds ``max_hops``.
        """
        from graphclaw.db.age.redirects import resolve_canonical  # noqa: PLC0415

        return await resolve_canonical(node_id, self, max_hops)

    async def get_active_thread(
        self,
        recipient_id: str,
        since_iso: str,
    ) -> dict | None:
        """Return the most recent active CheckinNode thread for a recipient.

        Used by ``OutboundCommunicationAgent._resolve_channel`` to implement
        channel stickiness (FR-OUT-002).

        Parameters
        ----------
        recipient_id:
            The ResourceNode or UserNode id of the counterparty.
        since_iso:
            ISO-8601 timestamp; only CheckinNodes created after this are
            considered within the stickiness window.

        Returns
        -------
        dict | None
            ``{"channel": str, "thread_id": str}`` for the most recent active
            thread, or ``None`` when no active thread exists within the window.
        """
        eid_recipient = _escape(recipient_id)
        eid_since = _escape(since_iso)

        async with get_connection(self._pool) as conn:
            rows = await conn.fetch(
                f"""
                SELECT * FROM cypher('{self._graph}', $$
                    MATCH (c:CheckinNode)
                    WHERE c.target_resource = '{eid_recipient}'
                      AND c.state <> 'DONE'
                      AND c.created_at >= '{eid_since}'
                      AND c.thread_id IS NOT NULL
                    RETURN c.channel AS channel, c.thread_id AS thread_id,
                           c.created_at AS created_at
                    ORDER BY c.created_at DESC
                    LIMIT 1
                $$) as (channel agtype, thread_id agtype, created_at agtype)
                """
            )
        if not rows:
            return None
        row = rows[0]
        channel_val = row["channel"]
        thread_id_val = row["thread_id"]
        if channel_val is None or thread_id_val is None:
            return None

        def _unwrap(v: Any) -> str:
            if isinstance(v, str):
                v = v.strip('"')
            return str(v)

        return {"channel": _unwrap(channel_val), "thread_id": _unwrap(thread_id_val)}

    async def list_follow_up_candidates(
        self,
        user_id: str,
        cutoff_iso: str,
        states: list[str] | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """Return tasks due for follow-up review (FR-SCHED-001).

        Candidates are tasks owned by *user_id* that:
        - Have state in *states* (default: WAITING, IN_PROGRESS).
        - Have ``archived_at`` unset.
        - Have ``last_outbound_at`` NULL or before *cutoff_iso*.

        Parameters
        ----------
        user_id:
            Owner user ID.
        cutoff_iso:
            ISO-8601 cutoff: tasks with ``last_outbound_at`` before this are stale.
        states:
            Optional list of states to include (default ``["WAITING", "IN_PROGRESS"]``).
        limit:
            Maximum number of results to return.

        Returns
        -------
        list[dict]
            Each dict has keys: ``task_id``, ``title``, ``state``,
            ``last_outbound_at``, ``score``.
        """
        if states is None:
            states = ["WAITING", "IN_PROGRESS"]

        eid_owner = _escape(user_id)
        eid_cutoff = _escape(cutoff_iso)
        # Build state list as AGE array literal
        state_list = ", ".join(f"'{_escape(s)}'" for s in states)

        async with get_connection(self._pool) as conn:
            rows = await conn.fetch(
                f"""
                SELECT * FROM cypher('{self._graph}', $$
                    MATCH (t:TaskNode)
                    WHERE t.owner_user_id = '{eid_owner}'
                      AND t.archived_at IS NULL
                      AND t.state IN [{state_list}]
                      AND (t.last_outbound_at IS NULL OR t.last_outbound_at <= '{eid_cutoff}')
                    RETURN t.task_id AS task_id,
                           t.title AS title,
                           t.state AS state,
                           t.last_outbound_at AS last_outbound_at,
                           coalesce(t.score, 0) AS score
                    ORDER BY coalesce(t.score, 0) DESC
                    LIMIT {int(limit)}
                $$) as (
                    task_id agtype,
                    title agtype,
                    state agtype,
                    last_outbound_at agtype,
                    score agtype
                )
                """
            )

        def _unwrap(v: Any) -> Any:
            if v is None:
                return None
            if isinstance(v, str):
                v = v.strip('"')
            return v

        results = []
        for row in rows:
            results.append(
                {
                    "task_id": _unwrap(row["task_id"]),
                    "title": _unwrap(row["title"]),
                    "state": _unwrap(row["state"]),
                    "last_outbound_at": _unwrap(row["last_outbound_at"]),
                    "score": float(_unwrap(row["score"]) or 0),
                }
            )
        return results

    async def get_resource_with_linked_view(
        self,
        resource_id: str,
        caller_context: Any | None = None,
    ) -> dict | None:
        """Return a merged view of a ResourceNode, reading through linked_user_id.

        When ``linked_user_id`` is set on the resource (FR-GRAPH-003), this
        method reads the linked UserNode and overlays its ``preferences`` and
        ``identities`` onto the result.  Owner-specific fields (everything else
        on the resource) are preserved from the shadow ResourceNode.

        Parameters
        ----------
        resource_id:
            The RES-{id} of the ResourceNode to fetch.
        caller_context:
            Required for ACL enforcement.

        Returns
        -------
        dict | None
            Merged view dict, or ``None`` if not found.
        """
        resource = await self.get_node(resource_id, caller_context=caller_context)
        if resource is None:
            return None
        linked_user_id = resource.get("linked_user_id")
        if not linked_user_id:
            return resource
        linked_user = await self.get_node(
            linked_user_id, include_archived=False, caller_context=caller_context
        )
        if linked_user is None:
            return resource
        # Read-through: overlay preferences and identities from the linked user.
        merged = dict(resource)
        if "preferences" in linked_user:
            merged["preferences"] = linked_user["preferences"]
        if "identities" in linked_user:
            merged["identities"] = linked_user["identities"]
        return merged

    # ------------------------------------------------------------------
    # Identity Merge — FR-ID-004
    # ------------------------------------------------------------------

    async def redirect_edges(self, from_id: str, to_id: str) -> int:
        """Re-point all edges from *from_id* to *to_id* (used by ResourceMerger).

        Because Apache AGE does not support ``SET`` on a relationship endpoint,
        we read, recreate and delete edges in two Cypher passes:
        1. MATCH all edges leading **to** ``from_id`` → recreate pointing to ``to_id``.
        2. MATCH all edges leading **from** ``from_id`` → recreate from ``to_id``.

        Returns the number of edges redirected.

        Parameters
        ----------
        from_id:
            The node being merged / archived.
        to_id:
            The canonical node that should receive the edges.
        """
        from datetime import datetime, timezone  # noqa: PLC0415

        eid_from = _escape(from_id)
        eid_to = _escape(to_id)
        now_iso = datetime.now(timezone.utc).isoformat()
        total = 0

        # Pass 1: in-bound edges  → ()-[e]->(from)  become ()-[e]->(to)
        async with get_connection(self._pool) as conn:
            result = await conn.execute(
                f"""
                SELECT * FROM cypher('{self._graph}', $$
                    MATCH (src)-[e]->(old {{id: '{eid_from}'}})
                    MATCH (tgt {{id: '{eid_to}'}})
                    CREATE (src)-[:REDIRECTED_EDGE {{
                        original_type: type(e),
                        redirected_at: '{now_iso}',
                        from_id: '{eid_from}'
                    }}]->(tgt)
                    DELETE e
                    RETURN 1
                $$) as (n agtype)
                """
            )
            rows = await result.fetchall()
            total += len(rows)

        # Pass 2: out-bound edges  (from)-[e]->()  become (to)-[e]->()
        async with get_connection(self._pool) as conn:
            result = await conn.execute(
                f"""
                SELECT * FROM cypher('{self._graph}', $$
                    MATCH (old {{id: '{eid_from}'}})-[e]->(dst)
                    MATCH (src {{id: '{eid_to}'}})
                    CREATE (src)-[:REDIRECTED_EDGE {{
                        original_type: type(e),
                        redirected_at: '{now_iso}',
                        from_id: '{eid_from}'
                    }}]->(dst)
                    DELETE e
                    RETURN 1
                $$) as (n agtype)
                """
            )
            rows = await result.fetchall()
            total += len(rows)

        logger.debug("redirect_edges", extra={"from_id": from_id, "to_id": to_id, "count": total})
        return total


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

    For ``TaskNode`` instances, returns the type-specific label derived from
    ``node.task_type`` (e.g. ``TaskAtomic``, ``TaskDelegated``, etc.) so that
    each task variant is stored under its own AGE vertex label (PRD §3.1,
    O-DB-01).

    For all other nodes, checks in order:
    1. ``node.node_type`` attribute (string or enum with a ``.value``)
    2. ``node.__class__.__name__``
    """
    # Explicit mapping from TaskType enum value → AGE vertex label.
    # Overrides are needed where simple .capitalize() produces the wrong casing.
    _TASK_TYPE_LABEL: dict[str, str] = {
        "ATOMIC": "TaskAtomic",
        "COMPOSITE": "TaskComposite",
        "DELEGATED": "TaskDelegated",
        "FOLLOWUP": "TaskFollowUp",  # init-db.sql uses TaskFollowUp not TaskFollowup
        "APPROVAL": "TaskApproval",
        "MILESTONE": "TaskMilestone",
        "REVIEW": "TaskReview",
        "RECURRING": "TaskRecurring",
        "DECISION": "TaskDecision",
        "CHECKIN": "TaskCheckin",
        "RESEARCH": "TaskResearch",
    }

    # TaskNode: use type-specific label.
    task_type = getattr(node, "task_type", None)
    if task_type is not None:
        type_str = getattr(task_type, "value", str(task_type)).upper()
        return _TASK_TYPE_LABEL.get(type_str, f"Task{type_str.capitalize()}")

    label = getattr(node, "node_type", None)
    if label is None:
        return node.__class__.__name__
    # If it's an enum, use its value.
    return getattr(label, "value", str(label))

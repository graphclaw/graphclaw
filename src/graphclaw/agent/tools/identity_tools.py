# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.agent.tools.identity_tools — Identity resolution tools (FR-ID-002..005).

Description
-----------
Agent-callable tools for identity management:
- ``resolve_user``: Ranked candidate lookup (FR-ID-002).
- ``start_create_person_dialog``: Begin the create-person FSM (FR-ID-003).
- ``merge_resource``: Post-hoc deduplication (FR-ID-004).
- ``register_alias``: Add alias with provenance (FR-ID-005).

Public API
----------
- IDENTITY_TOOLS: list of tool dicts compatible with tool_registry format.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool: resolve_user (FR-ID-002)
# ---------------------------------------------------------------------------


async def resolve_user(
    query: str,
    caller_user_id: str,
    store: Any,
    caller_org_ids: list[str] | None = None,
    hints: dict | None = None,
    directory_search: Any | None = None,
    **_: Any,
) -> dict:
    """Ranked user/resource identity lookup (FR-ID-002).

    Parameters
    ----------
    query:
        Name or alias to search for.
    caller_user_id:
        Calling user's ID (scope enforcement).
    store:
        GraphStore with async list_nodes / get_node.
    caller_org_ids:
        Orgs the caller belongs to (for directory scoping).
    hints:
        Optional search hints e.g. ``{"channel": "telegram", "value": "+44…"}``.
    directory_search:
        Optional org-directory callable.

    Returns
    -------
    dict
        ``{"candidates": [...], "query": query}``
    """
    try:
        from graphclaw.identity.resolver import UserResolver  # noqa: PLC0415

        resolver = UserResolver(store, directory_search=directory_search)
        candidates = await resolver.resolve(
            query,
            caller_user_id=caller_user_id,
            caller_org_ids=caller_org_ids,
            hints=hints,
        )
        return {
            "candidates": [c.model_dump() for c in candidates],
            "query": query,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("resolve_user tool failed: %s", exc)
        return {"candidates": [], "query": query, "error": str(exc)}


# ---------------------------------------------------------------------------
# Tool: start_create_person_dialog (FR-ID-003)
# ---------------------------------------------------------------------------


async def start_create_person_dialog(
    query: str,
    caller_user_id: str,
    store: Any,
    session_context: dict | None = None,
    **_: Any,
) -> dict:
    """Start or continue the create-person dialog FSM (FR-ID-003).

    Runs ``resolve_user`` first; if confident candidates exist, DISAMBIGUATE
    state shows them.

    Returns
    -------
    dict
        ``{"prompt": str, "state": str, "session_key": str}``
    """
    try:
        from graphclaw.agent.identity.create_person import CreatePersonFSM  # noqa: PLC0415
        from graphclaw.identity.resolver import UserResolver  # noqa: PLC0415

        session_key = f"create_person_{caller_user_id}"

        # Resume existing FSM if in session_context
        if session_context and session_key in session_context:
            fsm = CreatePersonFSM.from_dict(session_context[session_key], store)
        else:
            # Resolve top candidates first
            resolver = UserResolver(store)
            candidates_objs = await resolver.resolve(query, caller_user_id=caller_user_id)
            candidates = [c.model_dump() for c in candidates_objs[:5]]

            fsm = CreatePersonFSM(
                store=store,
                caller_user_id=caller_user_id,
                candidates=candidates,
            )
            fsm._collected["original_query"] = query  # noqa: SLF001

        # Store updated state back
        if session_context is not None:
            session_context[session_key] = fsm.to_dict()

        if fsm.is_done and fsm.result:
            return {
                "state": "DONE",
                "action": fsm.result.action,
                "node_id": fsm.result.node_id,
                "display_name": fsm.result.display_name,
            }

        return {
            "state": fsm.state.value,
            "prompt": fsm.get_prompt(),
            "session_key": session_key,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("start_create_person_dialog failed: %s", exc)
        return {"error": str(exc)}


async def respond_to_create_person_dialog(
    session_key: str,
    user_input: str,
    store: Any,
    session_context: dict | None = None,
    caller_user_id: str = "",
    **_: Any,
) -> dict:
    """Continue the create-person dialog with user input (FR-ID-003)."""
    try:
        from graphclaw.agent.identity.create_person import CreatePersonFSM  # noqa: PLC0415

        if not session_context or session_key not in session_context:
            return {"error": "no_active_dialog", "session_key": session_key}

        fsm = CreatePersonFSM.from_dict(session_context[session_key], store)
        prompt = await fsm.step(user_input)

        session_context[session_key] = fsm.to_dict()

        if fsm.is_done and fsm.result:
            return {
                "state": "DONE",
                "action": fsm.result.action,
                "node_id": fsm.result.node_id,
                "display_name": fsm.result.display_name,
                "aliases_added": fsm.result.aliases_added,
            }

        return {"state": fsm.state.value, "prompt": prompt}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Tool: merge_resource (FR-ID-004)
# ---------------------------------------------------------------------------


async def merge_resource(
    keep_id: str,
    merge_id: str,
    canonical_name: str | None = None,
    store: Any = None,
    storage: Any = None,
    broker: Any = None,
    **_: Any,
) -> dict:
    """Post-hoc deduplication — merge merge_id into keep_id (FR-ID-004).

    Returns
    -------
    dict
        ``{"tombstone_id", "edges_redirected", "aliases_merged", "intelligence_lines_merged"}``
    """
    try:
        from graphclaw.identity.merger import ResourceMerger  # noqa: PLC0415

        merger = ResourceMerger(store=store, storage=storage)
        result = await merger.merge(
            keep_id=keep_id,
            merge_id=merge_id,
            canonical_name=canonical_name,
            broker=broker,
        )
        return {
            "tombstone_id": result.tombstone_id,
            "edges_redirected": result.edges_redirected,
            "aliases_merged": result.aliases_merged,
            "intelligence_lines_merged": result.intelligence_lines_merged,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("merge_resource tool failed: %s", exc)
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Tool: register_alias (FR-ID-005 — alias-drift autoload)
# ---------------------------------------------------------------------------


async def register_alias(
    node_id: str,
    alias: str,
    source: str = "manual",
    added_by: str = "SYSTEM",
    store: Any = None,
    caller_context: Any = None,
    **_: Any,
) -> dict:
    """Append an alias to a node with provenance (FR-ID-002, FR-ID-005).

    Returns
    -------
    dict
        ``{"alias_count": int}``
    """
    if store is None:
        return {"alias_count": 0, "error": "store not provided"}
    try:
        from graphclaw.models.base import utcnow  # noqa: PLC0415

        node_raw = await store.get_node(node_id, caller_context=caller_context)
        if node_raw is None:
            return {"alias_count": 0, "error": f"node {node_id} not found"}

        existing_aliases = node_raw.get("aliases", []) if isinstance(node_raw, dict) else []
        existing_values: set[str] = set()
        for a in existing_aliases:
            if isinstance(a, dict):
                existing_values.add(a.get("value", "").lower())
            elif isinstance(a, str):
                stripped = a.strip()
                if stripped.startswith("{"):
                    try:
                        import json as _json  # noqa: PLC0415

                        parsed = _json.loads(stripped)
                        if isinstance(parsed, dict):
                            existing_values.add(parsed.get("value", "").lower())
                            continue
                    except (ValueError, TypeError):
                        pass
                existing_values.add(stripped.lower())
        if alias.lower() in existing_values:
            return {"alias_count": len(existing_aliases), "added": False, "reason": "duplicate"}

        new_alias = {
            "value": alias,
            "added_at": utcnow().isoformat(),
            "added_by": added_by,
            "source": source,
        }
        updated = list(existing_aliases) + [new_alias]
        await store.update_node(node_id, {"aliases": updated}, caller_context=caller_context)
        return {"alias_count": len(updated), "added": True}
    except Exception as exc:  # noqa: BLE001
        logger.warning("register_alias failed: %s", exc)
        return {"alias_count": 0, "error": str(exc)}


# ---------------------------------------------------------------------------
# Tool registry entries
# ---------------------------------------------------------------------------

IDENTITY_TOOLS: list[dict] = [
    {
        "name": "resolve_user",
        "description": (
            "Resolve a person's name/alias to known contacts. "
            "Returns ranked candidates from local contacts and org directory."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Name or alias to search for."},
                "hints": {
                    "type": "object",
                    "description": "Optional hints like {channel, value} to narrow search.",
                },
            },
            "required": ["query"],
        },
        "fn": resolve_user,
    },
    {
        "name": "start_create_person_dialog",
        "description": (
            "Start an interactive dialog to identify or create a new contact. "
            "Offers disambiguation against existing contacts first."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The name/alias the user mentioned."},
            },
            "required": ["query"],
        },
        "fn": start_create_person_dialog,
    },
    {
        "name": "respond_to_create_person_dialog",
        "description": "Respond to an active create-person dialog with the user's input.",
        "parameters": {
            "type": "object",
            "properties": {
                "session_key": {"type": "string"},
                "user_input": {"type": "string"},
            },
            "required": ["session_key", "user_input"],
        },
        "fn": respond_to_create_person_dialog,
    },
    {
        "name": "merge_resource",
        "description": (
            "Merge a duplicate contact (merge_id) into a canonical node (keep_id). "
            "Redirects edges, merges aliases and intelligence, archives the duplicate."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "keep_id": {"type": "string"},
                "merge_id": {"type": "string"},
                "canonical_name": {"type": "string"},
            },
            "required": ["keep_id", "merge_id"],
        },
        "fn": merge_resource,
    },
    {
        "name": "register_alias",
        "description": "Add an alias or nickname to a contact node.",
        "parameters": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string"},
                "alias": {"type": "string"},
            },
            "required": ["node_id", "alias"],
        },
        "fn": register_alias,
    },
]

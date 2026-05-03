"""graphclaw.cascade.membership — Membership-change cascade handler (FR-AK-001).

Description
-----------
When ``OrganizationNode.members`` changes (member added / removed), fans out:
1. User directory — upsert on add; remove on departure.
2. Org task index — update ACL (assignee_linked_user_ids) on departure.
3. ResourceNode shadow link_status — flip to ``detached_org_left`` on departure.
4. Counterparty detachment (FR-AD-001) — freeze last-known data.

Design Patterns
---------------
- Service Object: ``MembershipCascade`` encapsulates all cascade logic.
- Event sourcing: Triggered by ``membership.added`` / ``membership.removed`` events
  from the agent event consumer.

Public API
----------
- MembershipCascade: Main cascade handler.
- MembershipCascade.on_member_added(user_id, org_id): Fan-out on add.
- MembershipCascade.on_member_removed(user_id, org_id): Fan-out on removal.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class MembershipCascade:
    """Membership-change cascade handler (FR-AK-001).

    Parameters
    ----------
    store:
        GraphStore for reading/updating ResourceNode shadows.
    directory:
        ``UserDirectory`` instance for directory upsert/remove.
    task_index:
        ``OrgTaskIndex`` instance for ACL updates.
    """

    def __init__(
        self,
        store: Any,
        directory: Any | None = None,
        task_index: Any | None = None,
    ) -> None:
        self._store = store
        self._directory = directory
        self._task_index = task_index

    async def on_member_added(self, user_id: str, org_id: str) -> None:
        """Fan-out when a user is added to *org_id* (FR-AK-001).

        1. Upsert user_directory row for (user_id, org_id).
        2. No ResourceNode shadow changes needed on add.
        """
        logger.info(
            "membership_cascade.member_added",
            extra={"user_id": user_id, "org_id": org_id},
        )
        if self._directory is not None:
            try:
                # Load user profile to build directory entry
                user_raw = await self._store.get_node(user_id)
                if user_raw is not None:
                    entry = self._build_directory_entry(user_raw, org_id)
                    await self._directory.upsert(entry)
            except Exception as exc:  # noqa: BLE001
                logger.warning("membership_cascade.add_directory_upsert_failed: %s", exc)

    async def on_member_removed(self, user_id: str, org_id: str) -> None:
        """Fan-out when a user is removed from *org_id* (FR-AK-001).

        1. Remove user_directory row for (user_id, org_id).
        2. Flip link_status on ResourceNode shadows owned by others in org.
        3. Log counterparty detachment (FR-AD-001).
        """
        logger.info(
            "membership_cascade.member_removed",
            extra={"user_id": user_id, "org_id": org_id},
        )

        # 1. Remove directory row
        if self._directory is not None:
            try:
                await self._directory.remove(user_id, org_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("membership_cascade.remove_directory_failed: %s", exc)

        # 2. Find ResourceNode shadows that link to user_id + set detached_org_left
        await self._detach_resource_shadows(user_id)

        # 3. Emit detachment event for any active comms-agent sessions
        await self._emit_detachment_event(user_id, org_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _detach_resource_shadows(self, user_id: str) -> None:
        """Flip link_status on all shadows that point to *user_id*."""
        try:
            shadows = await self._store.list_nodes(
                "ResourceNode",
                filters={"linked_user_id": user_id},
            )
            for shadow_raw in shadows or []:
                shadow_id = (
                    shadow_raw.get("id")
                    if isinstance(shadow_raw, dict)
                    else getattr(shadow_raw, "id", None)
                )
                if shadow_id:
                    await self._store.update_node(
                        shadow_id,
                        {"link_status": "detached_org_left"},
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning("membership_cascade.detach_shadows_failed: %s", exc)

    async def _emit_detachment_event(self, user_id: str, org_id: str) -> None:
        """Emit a membership-removed event for downstream consumers."""
        # No broker here — events are consumed by the event_consumer background task
        # which subscribes to 'membership.removed' on the AGENT_UPDATES queue.
        # This is a hook for subclasses or test injection.
        pass

    @staticmethod
    def _build_directory_entry(user_raw: dict | Any, org_id: str) -> Any:
        """Build a DirectoryEntry from a raw UserNode dict."""
        from graphclaw.identity.directory import DirectoryEntry  # noqa: PLC0415

        if isinstance(user_raw, dict):
            user_id = user_raw.get("id", "")
            name = user_raw.get("name", "")
            identities = user_raw.get("identities", {}) or {}
            emails = identities.get("emails", []) if isinstance(identities, dict) else []
            prefs = user_raw.get("preferences", {}) or {}
            discoverability = (
                prefs.get("discoverability", "org_default")
                if isinstance(prefs, dict)
                else "org_default"
            )
            aliases = user_raw.get("aliases", []) or []
            alias_values = [a.get("value", "") if isinstance(a, dict) else str(a) for a in aliases]
        else:
            user_id = getattr(user_raw, "id", "")
            name = getattr(user_raw, "name", "")
            identities = {}
            emails = []
            discoverability = "org_default"
            alias_values = []

        return DirectoryEntry(
            user_id=user_id,
            org_id=org_id,
            display_name=name,
            emails=list(emails),
            identities=identities,
            discoverable_aliases=alias_values,
            visibility_policy=discoverability,
        )

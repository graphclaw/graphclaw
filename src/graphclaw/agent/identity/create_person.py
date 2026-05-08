# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.agent.identity.create_person — Create-person-via-dialog FSM (FR-ID-003).

Description
-----------
When ``resolve_user`` returns no confident match, the agent walks the user
through a disambiguation / creation dialog:

  DISAMBIGUATE → NAME → ROLE → CHANNEL → CONTACT → ALIASES → DONE

The DISAMBIGUATE state first offers existing top-N local candidates so the user
can pick an existing person instead of creating a duplicate (closes the
"Mr. Smith / Bob" gap from FR-ID-003 AC1).

Design Patterns
---------------
- State Machine: ``CreatePersonFSM`` holds dialog state in memory; serialisable
  to dict for persistence in session context.
- Result object: ``CreatePersonResult`` returned when DONE.

Public API
----------
- CreatePersonState: Enum of FSM states.
- CreatePersonResult: Result when DONE (new node created or existing picked).
- CreatePersonFSM: Dialog state machine.
- CreatePersonFSM.start(candidates): Initialise with top-N existing candidates.
- CreatePersonFSM.step(user_input): Process one turn, return next prompt.
- CreatePersonFSM.to_dict / from_dict: Serialisation for session storage.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State enum
# ---------------------------------------------------------------------------


class CreatePersonState(str, Enum):
    DISAMBIGUATE = "DISAMBIGUATE"
    NAME = "NAME"
    ROLE = "ROLE"
    CHANNEL = "CHANNEL"
    CONTACT = "CONTACT"
    ALIASES = "ALIASES"
    DONE = "DONE"


_STATE_ORDER = [
    CreatePersonState.DISAMBIGUATE,
    CreatePersonState.NAME,
    CreatePersonState.ROLE,
    CreatePersonState.CHANNEL,
    CreatePersonState.CONTACT,
    CreatePersonState.ALIASES,
    CreatePersonState.DONE,
]


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass
class CreatePersonResult:
    """Outcome when the create-person FSM reaches DONE.

    Attributes
    ----------
    action:
        ``"picked_existing"`` or ``"created_new"``.
    node_id:
        The node ID of the picked or newly created person.
    display_name:
        Canonical display name.
    aliases_added:
        List of alias values added during the dialog.
    """

    action: str  # "picked_existing" | "created_new"
    node_id: str
    display_name: str
    aliases_added: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# FSM
# ---------------------------------------------------------------------------


class CreatePersonFSM:
    """Create-person-via-dialog FSM (FR-ID-003).

    Parameters
    ----------
    store:
        GraphStore for creating/updating nodes.
    caller_user_id:
        The user initiating the dialog (used to create the ResourceNode in their substrate).
    candidates:
        Top-N existing candidates from ``resolve_user`` to show in DISAMBIGUATE.
    """

    def __init__(
        self,
        store: Any,
        caller_user_id: str,
        candidates: list[dict] | None = None,
    ) -> None:
        self._store = store
        self._caller_user_id = caller_user_id
        self._candidates: list[dict] = candidates or []
        self._state = CreatePersonState.DISAMBIGUATE
        self._collected: dict[str, Any] = {}
        self._result: CreatePersonResult | None = None

    @property
    def state(self) -> CreatePersonState:
        return self._state

    @property
    def is_done(self) -> bool:
        return self._state == CreatePersonState.DONE

    @property
    def result(self) -> CreatePersonResult | None:
        return self._result

    def get_prompt(self) -> str:
        """Return the prompt to show the user for the current state."""
        if self._state == CreatePersonState.DISAMBIGUATE:
            if self._candidates:
                names = [
                    f"{i + 1}. {c.get('display_name', c.get('node_id', '?'))} "
                    f"(confidence {c.get('confidence', 0):.0%})"
                    for i, c in enumerate(self._candidates[:5])
                ]
                return (
                    "I found some existing contacts that might be who you mean:\n\n"
                    + "\n".join(names)
                    + "\n\nType a number to select an existing person, "
                    "or type 'new' to create a new contact."
                )
            return "No existing contacts found. Type 'new' to create a new contact."

        prompts = {
            CreatePersonState.NAME: "What is this person's full name?",
            CreatePersonState.ROLE: "What is their role or relationship to you? (e.g. 'client', 'team member', 'vendor') — or type 'skip'.",
            CreatePersonState.CHANNEL: "What channel do you typically use to reach them? (email / telegram / whatsapp / slack / other) — or type 'skip'.",
            CreatePersonState.CONTACT: "What is their contact address for that channel (email address, phone number, username)? — or type 'skip'.",
            CreatePersonState.ALIASES: "Any aliases or nicknames I should recognise for this person? (comma-separated) — or type 'done'.",
        }
        return prompts.get(self._state, "")

    async def step(self, user_input: str) -> str:
        """Process one turn of the dialog.

        Returns the next prompt string.  When done, returns a confirmation
        message and sets ``self.result``.
        """
        inp = user_input.strip()

        if self._state == CreatePersonState.DISAMBIGUATE:
            await self._handle_disambiguate(inp)

        elif self._state == CreatePersonState.NAME:
            self._collected["name"] = inp
            self._state = CreatePersonState.ROLE

        elif self._state == CreatePersonState.ROLE:
            if inp.lower() != "skip":
                self._collected["role"] = inp
            self._state = CreatePersonState.CHANNEL

        elif self._state == CreatePersonState.CHANNEL:
            if inp.lower() != "skip":
                self._collected["channel"] = inp
            self._state = CreatePersonState.CONTACT

        elif self._state == CreatePersonState.CONTACT:
            if inp.lower() != "skip":
                self._collected["contact"] = inp
            self._state = CreatePersonState.ALIASES

        elif self._state == CreatePersonState.ALIASES:
            if inp.lower() not in ("done", "skip", ""):
                self._collected["aliases"] = [a.strip() for a in inp.split(",") if a.strip()]
            await self._create_node()
            self._state = CreatePersonState.DONE
            return f"Done! Created contact: {self._result.display_name}"  # type: ignore[union-attr]

        return self.get_prompt()

    async def _handle_disambiguate(self, inp: str) -> None:
        """Handle the DISAMBIGUATE step."""
        if inp.lower() == "new" or not self._candidates:
            # User wants to create a new person
            self._state = CreatePersonState.NAME
            return

        # Try to parse a number
        try:
            idx = int(inp) - 1
            if 0 <= idx < len(self._candidates):
                candidate = self._candidates[idx]
                node_id = candidate.get("node_id", "")
                display_name = candidate.get("display_name", node_id)

                # Alias-drift autoload (FR-ID-005) — add the query as an alias
                query = self._collected.get("original_query", "")
                aliases_added: list[str] = []
                if query and query.lower() != display_name.lower():
                    try:
                        from graphclaw.models.base import utcnow  # noqa: PLC0415

                        aliases_added.append(query)
                        node_raw = await self._store.get_node(node_id)
                        existing_aliases = (
                            node_raw.get("aliases", []) if isinstance(node_raw, dict) else []
                        )
                        existing_values = {
                            (a.get("value", "") if isinstance(a, dict) else str(a)).lower()
                            for a in existing_aliases
                        }
                        if query.lower() not in existing_values:
                            new_alias = {
                                "value": query,
                                "added_at": utcnow().isoformat(),
                                "added_by": self._caller_user_id,
                                "source": "auto-fuzzy",
                            }
                            await self._store.update_node(
                                node_id,
                                {"aliases": list(existing_aliases) + [new_alias]},
                            )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("create_person.alias_drift_failed: %s", exc)

                self._result = CreatePersonResult(
                    action="picked_existing",
                    node_id=node_id,
                    display_name=display_name,
                    aliases_added=aliases_added,
                )
                self._state = CreatePersonState.DONE
                return
        except (ValueError, IndexError):
            pass

        # Unrecognised input — re-prompt
        # (state stays DISAMBIGUATE; caller will call get_prompt() again)

    async def _create_node(self) -> None:
        """Create a new ResourceNode for the collected data."""
        from graphclaw.models.base import generate_id, utcnow  # noqa: PLC0415
        from graphclaw.models.enums import ResourceType  # noqa: PLC0415
        from graphclaw.models.nodes import ResourceNode  # noqa: PLC0415

        node_id = generate_id("RES")
        name = self._collected.get("name", "Unknown Contact")
        contact = self._collected.get("contact")
        role = self._collected.get("role")
        channel = self._collected.get("channel")

        # Build aliases list
        raw_aliases = self._collected.get("aliases", [])
        now = utcnow()
        aliases = [
            {
                "value": alias,
                "added_at": now.isoformat(),
                "added_by": self._caller_user_id,
                "source": "onboarding_dialog",
            }
            for alias in raw_aliases
        ]

        node = ResourceNode(
            id=node_id,
            resource_type=ResourceType.HUMAN,
            name=name,
            contact=contact,
            created_at=now,
            updated_at=now,
        )

        node_dict = node.model_dump(mode="json")
        if role:
            node_dict["role"] = role
        if aliases:
            node_dict["aliases"] = aliases
        if channel:
            node_dict.setdefault("communication_preferences", {})["preferred_channel"] = channel

        await self._store.create_node(node)
        # Update with extra fields
        extra: dict = {}
        if aliases:
            extra["aliases"] = aliases
        if role:
            extra["role"] = role
        if channel:
            extra["communication_preferences"] = {"preferred_channel": channel}
        if extra:
            try:
                await self._store.update_node(node_id, extra)
            except Exception as exc:  # noqa: BLE001
                logger.debug("create_person.update_extra_fields_failed: %s", exc)

        self._result = CreatePersonResult(
            action="created_new",
            node_id=node_id,
            display_name=name,
            aliases_added=[a["value"] for a in aliases],
        )

    def to_dict(self) -> dict:
        """Serialise FSM state for session storage."""
        return {
            "state": self._state.value,
            "collected": self._collected,
            "candidates": self._candidates,
            "caller_user_id": self._caller_user_id,
        }

    @classmethod
    def from_dict(cls, data: dict, store: Any) -> CreatePersonFSM:
        """Deserialise FSM state from session storage."""
        fsm = cls(
            store=store,
            caller_user_id=data.get("caller_user_id", ""),
            candidates=data.get("candidates", []),
        )
        try:
            fsm._state = CreatePersonState(data.get("state", CreatePersonState.DISAMBIGUATE.value))
        except ValueError:
            fsm._state = CreatePersonState.DISAMBIGUATE
        fsm._collected = data.get("collected", {})
        return fsm

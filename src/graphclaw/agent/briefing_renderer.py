# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.agent.briefing_renderer — Entity-grouped briefing renderer.

Description
-----------
Supplements ``briefing.py`` with an entity-grouped view of tasks.  Instead of
listing tasks by rank, this renderer groups them by the resolved assignee entity
(``assignee.node_id``).  When a person appears under multiple display names
within the briefing window, all aliases are surfaced using the canonical format:

    Bob (also: Mr. Smith) — TSK-Y, TSK-Z

This satisfies FR-BRF-001 AC1: the canonical entity name is shown first, with
parenthetical aliases when more than one alias is present.

Design Patterns
---------------
- Pure Function: ``render_entity_grouped`` has no I/O dependencies.
- Builder: ``EntityGroup`` accumulates task IDs and aliases before rendering.

Public API
----------
- EntityGroup: Data container for one resolved entity's tasks + aliases.
- render_entity_grouped: Group tasks by assignee node_id and render.
- build_duplicate_suspicion_prompt: FR-BRF-002 merge-prompt generator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class EntityGroup:
    """Accumulates task IDs and display aliases for a single resolved entity.

    Attributes
    ----------
    node_id:
        Canonical AGE node ID for this entity (e.g. ``RES-abc-001``).
    canonical_name:
        The primary display name used by this entity in the current window.
    aliases:
        Additional display names seen for the same node_id in this window,
        ordered by first appearance.  Does not include ``canonical_name``.
    task_ids:
        Ordered list of task IDs assigned to this entity in this window.
    """

    node_id: str
    canonical_name: str
    aliases: list[str] = field(default_factory=list)
    task_ids: list[str] = field(default_factory=list)

    def add_alias(self, name: str) -> None:
        """Record *name* as an alias if it is not already known."""
        if name != self.canonical_name and name not in self.aliases:
            self.aliases.append(name)

    def render_label(self) -> str:
        """Return the human-readable label for this entity.

        When aliases are present the format is::

            Canonical (also: Alias1, Alias2)

        When no aliases the format is just the canonical name.
        """
        if self.aliases:
            also = ", ".join(self.aliases)
            return f"{self.canonical_name} (also: {also})"
        return self.canonical_name

    def render_line(self) -> str:
        """Return a single briefing line: ``<label> — TSK-A, TSK-B``."""
        label = self.render_label()
        tasks = ", ".join(self.task_ids) if self.task_ids else "(no tasks)"
        return f"{label} — {tasks}"


# ---------------------------------------------------------------------------
# Primary renderer
# ---------------------------------------------------------------------------


def render_entity_grouped(
    tasks: list[dict[str, Any]],
    *,
    assignee_key: str = "assigned_to",
    canonical_name_key: str = "assignee_display_name",
    task_id_key: str = "id",
) -> str:
    """Render a task list grouped by resolved assignee entity.

    Parameters
    ----------
    tasks:
        List of task dicts.  Each must have the fields named by *assignee_key*,
        *canonical_name_key*, and *task_id_key*.  Tasks with no assignee are
        grouped under the sentinel label ``(unassigned)``.
    assignee_key:
        Dict key for the assignee's stable node_id.  Defaults to
        ``"assigned_to"``.
    canonical_name_key:
        Dict key for the assignee's display name.  Defaults to
        ``"assignee_display_name"``.  When absent, the node_id is used.
    task_id_key:
        Dict key for the task's ID.  Defaults to ``"id"``.

    Returns
    -------
    str
        Multi-line string; one line per entity group, with a header.
        Returns a short message when *tasks* is empty.

    Examples
    --------
    >>> tasks = [
    ...     {"id": "TSK-Y", "assigned_to": "RES-001", "assignee_display_name": "Bob"},
    ...     {"id": "TSK-Z", "assigned_to": "RES-001", "assignee_display_name": "Mr. Smith"},
    ... ]
    >>> print(render_entity_grouped(tasks))
    ## Tasks by Assignee
    <BLANKLINE>
    Bob (also: Mr. Smith) — TSK-Y, TSK-Z
    """
    if not tasks:
        return "## Tasks by Assignee\n\n(no tasks)\n"

    # First pass: build groups preserving insertion order (Python 3.7+ dicts).
    groups: dict[str, EntityGroup] = {}
    unassigned_ids: list[str] = []

    for task in tasks:
        node_id: str = task.get(assignee_key) or ""
        display_name: str = task.get(canonical_name_key) or node_id or "(unassigned)"
        task_id: str = task.get(task_id_key) or ""

        if not node_id:
            unassigned_ids.append(task_id)
            continue

        if node_id not in groups:
            groups[node_id] = EntityGroup(node_id=node_id, canonical_name=display_name)
        else:
            groups[node_id].add_alias(display_name)

        if task_id:
            groups[node_id].task_ids.append(task_id)

    lines: list[str] = ["## Tasks by Assignee", ""]
    for group in groups.values():
        lines.append(group.render_line())

    if unassigned_ids:
        lines.append(f"(unassigned) — {', '.join(unassigned_ids)}")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# FR-BRF-002 — Duplicate-suspicion prompt builder
# ---------------------------------------------------------------------------


def build_duplicate_suspicion_prompt(
    candidate_pairs: list[tuple[str, str, str, str, float]],
) -> str:
    """Build a "possible duplicates — merge?" section for the briefing.

    Parameters
    ----------
    candidate_pairs:
        Each tuple is ``(node_id_a, name_a, node_id_b, name_b, similarity_score)``
        where *similarity_score* is in ``[0.0, 1.0]``.  Only pairs with
        ``similarity_score >= 0.75`` are surfaced (configurable via caller).

    Returns
    -------
    str
        A formatted multi-line string listing each suspicious pair, or an
        empty string when *candidate_pairs* is empty.

    Examples
    --------
    >>> pairs = [("RES-001", "Bob", "RES-002", "Robert", 0.88)]
    >>> print(build_duplicate_suspicion_prompt(pairs))
    ## Possible Duplicate Resources — Review Needed
    <BLANKLINE>
      • Bob (RES-001) ≈ Robert (RES-002)  [similarity: 0.88]
        → Consider merging via /admin/identity/resolve
    <BLANKLINE>
    """
    if not candidate_pairs:
        return ""

    lines: list[str] = ["## Possible Duplicate Resources — Review Needed", ""]
    for node_id_a, name_a, node_id_b, name_b, score in candidate_pairs:
        lines.append(
            f"  \u2022 {name_a} ({node_id_a}) \u2248 {name_b} ({node_id_b})"
            f"  [similarity: {score:.2f}]"
        )
        lines.append("    \u2192 Consider merging via /admin/identity/resolve")
    lines.append("")
    return "\n".join(lines)

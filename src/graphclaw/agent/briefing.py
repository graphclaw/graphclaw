"""graphclaw.agent.briefing — Human-readable briefing formatter for the agent action queue.

Description
-----------
Converts a ranked ``ActionQueueEntry`` list into a structured plain-text briefing
suitable for terminal display or embedding in a notification.  The briefing shows
the top N priorities with their scores, recommended actions, top scoring factor,
and summary text.  Topology notes and check-in batching are included when present.

All 5 sections from PRD §12.1 are generated when the corresponding data is provided:
  1. Critical — ranked action queue, max 3 items surfaced to user (§12.1, O-BRF-02)
  2. Inferences to Confirm — LOW-confidence and NEEDS_REVIEW tasks
  3. Completed Since Last — tasks completed since the previous briefing
  4. Ahead of the Curve — low-urgency tasks with approaching deadlines
  5. Deferred Items Check — SNOOZED tasks due for re-evaluation

Design Patterns
---------------
- Pure Function: ``format_briefing`` has no I/O dependencies; it only transforms
  the input data into a string.

Public API
----------
- format_briefing: Generate a structured text briefing from the action queue.
- BriefingContext: Optional data container for sections 2-5.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from graphclaw.models.scoring import ActionQueueEntry

# Maximum critical items surfaced to the user (PRD §12.1, O-BRF-02)
MAX_CRITICAL_ITEMS = 3


# ---------------------------------------------------------------------------
# Context for sections 2-5
# ---------------------------------------------------------------------------


@dataclass
class BriefingContext:
    """Optional data for generating briefing sections 2-5.

    Callers populate only the fields they have available; empty lists produce
    no output for that section.

    Attributes
    ----------
    inferences_to_confirm:
        Tasks with LOW confidence, NEEDS_REVIEW state, or pending approval.
        Each item is a dict with at least ``id``, ``title``, ``state``, ``reason``.
    completed_since_last:
        Tasks that reached COMPLETE since the previous briefing run.
        Each item is a dict with at least ``id``, ``title``, ``completed_at``.
    ahead_of_curve:
        Proactive items: tasks currently low-urgency but with deadlines inside
        the planning horizon.  Each item is a dict with at least ``id``, ``title``,
        ``deadline``, ``score``.
    deferred_items:
        SNOOZED tasks.  Each item is a dict with at least ``id``, ``title``,
        ``snooze_until``.
    """

    inferences_to_confirm: list[dict[str, Any]] = field(default_factory=list)
    completed_since_last: list[dict[str, Any]] = field(default_factory=list)
    ahead_of_curve: list[dict[str, Any]] = field(default_factory=list)
    deferred_items: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def format_briefing(
    queue: list[ActionQueueEntry],
    top_n: int = MAX_CRITICAL_ITEMS,
    context: BriefingContext | None = None,
    interrupt_threshold: float | None = None,
) -> str:
    """Generate a structured 5-section text briefing from the action queue.

    Parameters
    ----------
    queue:
        Ranked ActionQueueEntry list (sorted descending by final_score).
    top_n:
        Maximum number of entries in the Critical section. Default is 3
        per PRD §12.1 cognitive-load cap (O-BRF-02).
    context:
        Optional BriefingContext with data for sections 2-5.  If None,
        only section 1 (Critical) is generated.
    interrupt_threshold:
        When provided, any queue entry with ``final_score`` exceeding this
        value is flagged as an interrupt candidate (PRD §12.3, O-BRF-03).
        Items above the threshold are marked ``[INTERRUPT]`` in the briefing.
        Defaults to None (no interrupt filtering).

    Returns
    -------
    str
        A multi-line briefing string suitable for terminal display or
        embedding in a notification.
    """
    if not queue and not context:
        return "No actionable tasks found in the current scoring cycle.\n"

    ctx = context or BriefingContext()
    lines: list[str] = ["Agent Briefing", "=" * 50, ""]

    # -----------------------------------------------------------------------
    # Section 1: Critical (top_n items, O-BRF-02: max 3 surfaced to user)
    # -----------------------------------------------------------------------
    critical_entries = queue[:top_n]
    handled_autonomously = queue[top_n:]

    if critical_entries:
        lines.append("## 1. Critical — Action Required")
        lines.append("")
        for entry in critical_entries:
            explanation = entry.explanation
            top_factor = (
                max(explanation.factors, key=lambda f: f.weighted_score)
                if explanation.factors
                else None
            )
            lines.append(
                f"#{entry.rank}  {entry.node_id}  "
                f"[score: {entry.final_score:.3f}]  "
                f"[autonomy: {entry.autonomy_level.value}]"
                + (
                    "  [INTERRUPT]"
                    if interrupt_threshold is not None and entry.final_score > interrupt_threshold
                    else ""
                )
            )
            lines.append(f"    Action:  {entry.recommended_action}")
            if top_factor:
                lines.append(
                    f"    Top factor: {top_factor.factor_name} "
                    f"(weighted {top_factor.weighted_score:.3f})"
                )
                lines.append(f"    Reason:  {top_factor.plain_english}")
            lines.append(f"    Summary: {explanation.summary}")
            if explanation.topology_note:
                lines.append(f"    Topology: {explanation.topology_note}")
            if entry.batched_with:
                lines.append(f"    Batched with: {', '.join(entry.batched_with)}")
            lines.append("")

        if handled_autonomously:
            lines.append(
                f"    (Agent handling {len(handled_autonomously)} additional "
                f"lower-priority items autonomously.)"
            )
            lines.append("")
    else:
        lines.append("## 1. Critical — No urgent items at this time.")
        lines.append("")

    # -----------------------------------------------------------------------
    # Section 2: Inferences to Confirm
    # -----------------------------------------------------------------------
    if ctx.inferences_to_confirm:
        lines.append("## 2. Inferences to Confirm")
        lines.append("")
        for item in ctx.inferences_to_confirm:
            tid = item.get("id", "")
            title = item.get("title", "")
            state = item.get("state", "")
            reason = item.get("reason", "")
            lines.append(f"  - [{tid}] {title}  (state: {state})")
            if reason:
                lines.append(f"    Reason: {reason}")
        lines.append("")
    else:
        lines.append("## 2. Inferences to Confirm — None pending.")
        lines.append("")

    # -----------------------------------------------------------------------
    # Section 3: Completed Since Last Briefing
    # -----------------------------------------------------------------------
    if ctx.completed_since_last:
        lines.append("## 3. Completed Since Last Briefing")
        lines.append("")
        for item in ctx.completed_since_last:
            tid = item.get("id", "")
            title = item.get("title", "")
            completed_at = item.get("completed_at", "")
            lines.append(f"  ✓ [{tid}] {title}  (completed: {completed_at})")
        lines.append("")
    else:
        lines.append("## 3. Completed Since Last Briefing — None.")
        lines.append("")

    # -----------------------------------------------------------------------
    # Section 4: Ahead of the Curve
    # -----------------------------------------------------------------------
    if ctx.ahead_of_curve:
        lines.append("## 4. Ahead of the Curve — Proactive Items")
        lines.append("")
        for item in ctx.ahead_of_curve:
            tid = item.get("id", "")
            title = item.get("title", "")
            deadline = item.get("deadline", "")
            score_raw = item.get("score", 0.0)
            try:
                score = float(score_raw)
            except (TypeError, ValueError):
                score = 0.0
            lines.append(f"  - [{tid}] {title}  (deadline: {deadline}, score: {score:.3f})")
        lines.append("")
    else:
        lines.append("## 4. Ahead of the Curve — No proactive items identified.")
        lines.append("")

    # -----------------------------------------------------------------------
    # Section 5: Deferred Items Check
    # -----------------------------------------------------------------------
    if ctx.deferred_items:
        lines.append("## 5. Deferred Items Check")
        lines.append("")
        for item in ctx.deferred_items:
            tid = item.get("id", "")
            title = item.get("title", "")
            snooze_until = item.get("snooze_until", "")
            lines.append(f"  ⏸ [{tid}] {title}  (snoozed until: {snooze_until})")
        lines.append("")
    else:
        lines.append("## 5. Deferred Items Check — No snoozed items.")
        lines.append("")

    # Footer
    lines.append(f"Total tasks in queue: {len(queue)}")
    return "\n".join(lines)


def has_interrupt_items(queue: list[ActionQueueEntry], interrupt_threshold: float) -> bool:
    """Return True if any entry in *queue* exceeds *interrupt_threshold*.

    Callers (e.g. ``TriggerEngine``) use this to decide whether a mid-day
    briefing interrupt is warranted (PRD §12.3, O-BRF-03).

    Parameters
    ----------
    queue:
        Ranked ActionQueueEntry list.
    interrupt_threshold:
        The ``UserNode.preferences.interrupt_threshold`` value for the user.

    Returns
    -------
    bool
    """
    return any(e.final_score > interrupt_threshold for e in queue)


def find_duplicate_resource_candidates(
    resources: list[dict[str, Any]],
    *,
    threshold: float = 0.75,
    name_key: str = "display_name",
    id_key: str = "id",
) -> list[tuple[str, str, str, str, float]]:
    """Fuzzy-match ResourceNode display names and surface suspicious pairs.

    Implements FR-BRF-002: scans recently-touched resources for name pairs
    whose normalised Levenshtein similarity exceeds *threshold*, then returns
    them as candidate pairs for the duplicate-suspicion section of the briefing.

    The similarity metric is the ratio of the Levenshtein distance to the
    maximum of the two lengths, clamped to ``[0.0, 1.0]`` and then inverted
    (``1 - distance_ratio``) so that 1.0 means identical.

    Parameters
    ----------
    resources:
        List of resource dicts; each must have *name_key* and *id_key* fields.
    threshold:
        Minimum similarity score (inclusive) for a pair to be included.
        Default 0.75.
    name_key:
        Dict key for the display name.  Default ``"display_name"``.
    id_key:
        Dict key for the node ID.  Default ``"id"``.

    Returns
    -------
    list[tuple[str, str, str, str, float]]
        Each element is ``(node_id_a, name_a, node_id_b, name_b, similarity)``.
        Empty when no suspicious pairs found.
    """
    pairs: list[tuple[str, str, str, str, float]] = []
    n = len(resources)
    for i in range(n):
        for j in range(i + 1, n):
            a = resources[i]
            b = resources[j]
            name_a: str = str(a.get(name_key) or "")
            name_b: str = str(b.get(name_key) or "")
            id_a: str = str(a.get(id_key) or "")
            id_b: str = str(b.get(id_key) or "")

            # Skip identical node IDs (same entity, not a duplicate).
            if id_a and id_b and id_a == id_b:
                continue

            sim = _name_similarity(name_a, name_b)
            if sim >= threshold:
                pairs.append((id_a, name_a, id_b, name_b, sim))

    return pairs


def _name_similarity(a: str, b: str) -> float:
    """Return normalised name similarity in ``[0.0, 1.0]``.

    Uses Levenshtein distance on lowercased, whitespace-stripped strings.
    Identical strings → 1.0; completely dissimilar → 0.0.
    """
    a = a.strip().lower()
    b = b.strip().lower()
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    max_len = max(len(a), len(b))
    dist = _levenshtein(a, b)
    return max(0.0, 1.0 - dist / max_len)


def _levenshtein(a: str, b: str) -> int:
    """Iterative Levenshtein distance (Wagner–Fischer)."""
    m, n = len(a), len(b)
    # Use two-row DP to keep memory O(n).
    prev = list(range(n + 1))
    curr = [0] * (n + 1)
    for i in range(1, m + 1):
        curr[0] = i
        for j in range(1, n + 1):
            if a[i - 1] == b[j - 1]:
                curr[j] = prev[j - 1]
            else:
                curr[j] = 1 + min(prev[j], curr[j - 1], prev[j - 1])
        prev, curr = curr, [0] * (n + 1)
    return prev[n]


__all__ = [
    "format_briefing",
    "has_interrupt_items",
    "find_duplicate_resource_candidates",
    "BriefingContext",
    "MAX_CRITICAL_ITEMS",
]

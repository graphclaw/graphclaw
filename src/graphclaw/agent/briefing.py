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


__all__ = [
    "format_briefing",
    "has_interrupt_items",
    "BriefingContext",
    "MAX_CRITICAL_ITEMS",
]

"""graphclaw.agent.briefing — Human-readable briefing formatter for the agent action queue.

Description
-----------
Converts a ranked ``ActionQueueEntry`` list into a structured plain-text briefing
suitable for terminal display or embedding in a notification.  The briefing shows
the top N priorities with their scores, recommended actions, top scoring factor,
and summary text.  Topology notes and check-in batching are included when present.

Design Patterns
---------------
- Pure Function: ``format_briefing`` has no I/O dependencies; it only transforms
  the action queue data into a string.

Public API
----------
- format_briefing: Generate a structured text briefing from the action queue.

Dependencies
------------
- graphclaw.models.scoring: ActionQueueEntry.
"""
from __future__ import annotations

from graphclaw.models.scoring import ActionQueueEntry


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def format_briefing(queue: list[ActionQueueEntry], top_n: int = 5) -> str:
    """Generate a structured text briefing from the action queue.

    Parameters
    ----------
    queue:
        Ranked ActionQueueEntry list (sorted descending by final_score).
    top_n:
        Maximum number of entries to include in the briefing.

    Returns
    -------
    str
        A multi-line briefing string suitable for terminal display or
        embedding in a notification.
    """
    if not queue:
        return "No actionable tasks found in the current scoring cycle.\n"

    entries = queue[:top_n]
    lines: list[str] = [
        f"Agent Briefing — Top {len(entries)} Priorities",
        "=" * 50,
        "",
    ]

    for entry in entries:
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

    lines.append(f"Total tasks in queue: {len(queue)}")
    return "\n".join(lines)


__all__ = ["format_briefing"]

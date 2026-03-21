"""graphclaw.cli.formatters — Rich formatting utilities for the GraphClaw CLI.

Description
-----------
Provides reusable display helpers that render GraphClaw domain objects as Rich
tables and panels.  All functions accept an optional ``console`` parameter for
testability; if omitted, a module-level console is used.  This module is the
single place where terminal formatting decisions are made, keeping the command
modules focused on data retrieval.

Design Patterns
---------------
- Presenter: Each ``format_*`` function handles the rendering of a single domain
  object type, separating formatting concerns from data access.

Public API
----------
- format_task_table: Print a Rich table of TaskNode objects.
- format_task_panel: Print a detailed Rich panel for a single TaskNode.
- format_goal_table: Print a Rich table of GoalNode objects.
- format_goal_panel: Print a detailed Rich panel for a single GoalNode.
- format_score_explanation: Print a Rich panel with full score factor breakdown.
- format_action_queue: Print the agent action queue as a Rich table.
- format_briefing: Wrap a pre-formatted briefing string in a Rich panel.

Dependencies
------------
- graphclaw.models.nodes: GoalNode, TaskNode.
- graphclaw.models.scoring: ActionQueueEntry, ScoreExplanation.
- rich: Console, Panel, Table.
"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from graphclaw.models.nodes import GoalNode, TaskNode
from graphclaw.models.scoring import ActionQueueEntry, ScoreExplanation

# ---------------------------------------------------------------------------
# Module-level console instance (callers may pass their own Console)
# ---------------------------------------------------------------------------

_console = Console()


# ---------------------------------------------------------------------------
# Task formatting
# ---------------------------------------------------------------------------


def format_task_table(
    tasks: list[TaskNode],
    scores: dict[str, float] | None = None,
    console: Console | None = None,
    title: str = "Tasks",
) -> None:
    """Print a Rich table of tasks to the console.

    Parameters
    ----------
    tasks:
        Task list to display.
    scores:
        Optional mapping of task_id → final_score for the Score column.
    console:
        Rich Console to print to.  Defaults to the module console.
    title:
        Table title string.
    """
    out = console or _console
    scores = scores or {}

    table = Table(title=title, show_lines=False)
    table.add_column("Rank", style="bold", justify="right", width=6)
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Title", min_width=20)
    table.add_column("Type", style="dim", width=12)
    table.add_column("State", style="green", width=14)
    table.add_column("Deadline", width=12)
    table.add_column("Score", style="yellow", justify="right", width=7)

    for i, task in enumerate(tasks, 1):
        deadline_str = (
            task.timeline.deadline.strftime("%Y-%m-%d") if task.timeline.deadline else "—"
        )
        score_str = f"{scores[task.id]:.3f}" if task.id in scores else "—"
        table.add_row(
            str(i),
            task.id,
            task.title,
            task.task_type.value,
            task.state.value,
            deadline_str,
            score_str,
        )

    out.print(table)


def format_task_panel(task: TaskNode, console: Console | None = None) -> None:
    """Print a detailed Rich panel for a single task.

    Parameters
    ----------
    task:
        The TaskNode to display.
    console:
        Rich Console to print to.  Defaults to the module console.
    """
    out = console or _console

    deadline_str = (
        task.timeline.deadline.strftime("%Y-%m-%d %H:%M UTC") if task.timeline.deadline else "None"
    )
    effort_str = (
        f"{task.timeline.estimated_effort_days}d"
        if task.timeline.estimated_effort_days is not None
        else "—"
    )
    override_str = (
        f"{task.override.override_type.value} (by {task.override.set_by})"
        if task.override.is_overridden and task.override.override_type
        else "None"
    )

    lines = [
        f"[bold cyan]{task.id}[/bold cyan]  [dim]{task.task_type.value}[/dim]",
        f"[bold]{task.title}[/bold]",
        "",
        f"[dim]Description:[/dim] {task.description}",
        f"[dim]State:[/dim]       [green]{task.state.value}[/green]",
        f"[dim]Owner:[/dim]       {task.owned_by or '—'}",
        f"[dim]Assigned:[/dim]    {task.assigned_to or '—'}",
        f"[dim]Deadline:[/dim]    {deadline_str}",
        f"[dim]Effort:[/dim]      {effort_str}",
        f"[dim]Critical:[/dim]    {'Yes' if task.on_critical_path else 'No'}",
        f"[dim]Override:[/dim]    {override_str}",
        f"[dim]Autonomy:[/dim]    {task.autonomy.level.value}",
        f"[dim]Progress:[/dim]    {task.progress.percentage:.0f}% "
        f"({task.progress.confidence.value} confidence)",
    ]

    if task.tags:
        lines.append(f"[dim]Tags:[/dim]       {', '.join(task.tags)}")

    if task.state_history:
        last = task.state_history[-1]
        lines.append(
            f"[dim]Last change:[/dim] "
            f"{last.from_state.value} → {last.to_state.value} "
            f"by {last.changed_by.value}"
        )

    out.print(
        Panel(
            "\n".join(lines),
            title=f"Task: {task.id}",
            border_style="cyan",
        )
    )


# ---------------------------------------------------------------------------
# Goal formatting
# ---------------------------------------------------------------------------


def format_goal_table(
    goals: list[GoalNode],
    console: Console | None = None,
    title: str = "Goals",
) -> None:
    """Print a Rich table of goals to the console.

    Parameters
    ----------
    goals:
        Goal list to display.
    console:
        Rich Console to print to.  Defaults to the module console.
    title:
        Table title string.
    """
    out = console or _console

    table = Table(title=title, show_lines=False)
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Title", min_width=20)
    table.add_column("Priority", style="bold", width=10)
    table.add_column("State", style="green", width=12)
    table.add_column("Progress", justify="right", width=10)
    table.add_column("Target Date", width=12)

    for goal in goals:
        target_str = (
            goal.timeline.target_date.strftime("%Y-%m-%d") if goal.timeline.target_date else "—"
        )
        progress_str = f"{goal.progress.derived_percentage:.0f}%"
        table.add_row(
            goal.id,
            goal.title,
            goal.priority.value,
            goal.state.value,
            progress_str,
            target_str,
        )

    out.print(table)


def format_goal_panel(goal: GoalNode, console: Console | None = None) -> None:
    """Print a detailed Rich panel for a single goal.

    Parameters
    ----------
    goal:
        The GoalNode to display.
    console:
        Rich Console to print to.  Defaults to the module console.
    """
    out = console or _console

    target_str = (
        goal.timeline.target_date.strftime("%Y-%m-%d") if goal.timeline.target_date else "None"
    )

    lines = [
        f"[bold cyan]{goal.id}[/bold cyan]",
        f"[bold]{goal.title}[/bold]",
        "",
        f"[dim]Description:[/dim]  {goal.description}",
        f"[dim]State:[/dim]        [green]{goal.state.value}[/green]",
        f"[dim]Priority:[/dim]     [bold]{goal.priority.value}[/bold]",
        f"[dim]Owner:[/dim]        {goal.owner or '—'}",
        f"[dim]Origin:[/dim]       {goal.origin.value}",
        f"[dim]Target Date:[/dim]  {target_str}",
        f"[dim]Progress:[/dim]     "
        f"{goal.progress.milestones_done}/{goal.progress.milestone_count} milestones "
        f"({goal.progress.derived_percentage:.0f}%)",
        f"[dim]Confirmed:[/dim]    {'Yes' if goal.confirmed_by_user else 'No'}",
    ]

    out.print(
        Panel(
            "\n".join(lines),
            title=f"Goal: {goal.id}",
            border_style="green",
        )
    )


# ---------------------------------------------------------------------------
# Scoring / queue formatting
# ---------------------------------------------------------------------------


def format_score_explanation(
    explanation: ScoreExplanation,
    console: Console | None = None,
) -> None:
    """Print a Rich panel with full score breakdown for one task.

    Parameters
    ----------
    explanation:
        The ScoreExplanation to display.
    console:
        Rich Console to print to.  Defaults to the module console.
    """
    out = console or _console

    lines: list[str] = [
        f"[bold]{explanation.summary}[/bold]",
        "",
        "[dim]Factor breakdown:[/dim]",
    ]

    for factor in sorted(explanation.factors, key=lambda f: f.weighted_score, reverse=True):
        bar_len = max(0, min(20, int(factor.weighted_score * 20)))
        bar = "[yellow]" + "█" * bar_len + "[/yellow]" + "░" * (20 - bar_len)
        lines.append(
            f"  {factor.factor_name:<24} "
            f"[yellow]{factor.weighted_score:>5.3f}[/yellow]  "
            f"{bar}  [dim]{factor.plain_english}[/dim]"
        )

    if explanation.modifiers:
        lines.append("")
        lines.append("[dim]Modifiers:[/dim]")
        for mod in explanation.modifiers:
            lines.append(
                f"  {mod.modifier_type:<24} x{mod.multiplier:.2f}  [dim]{mod.plain_english}[/dim]"
            )

    if explanation.topology_note:
        lines.append("")
        lines.append(f"[dim]Topology: {explanation.topology_note}[/dim]")

    out.print(
        Panel(
            "\n".join(lines),
            title=(
                f"Score: [yellow]{explanation.final_score:.3f}[/yellow]  "
                f"Rank: [bold]#{explanation.rank}[/bold]  "
                f"Task: [cyan]{explanation.node_id}[/cyan]"
            ),
            border_style="yellow",
        )
    )


def format_action_queue(
    queue: list[ActionQueueEntry],
    console: Console | None = None,
    title: str = "Action Queue",
    top_n: int | None = None,
) -> None:
    """Print the agent action queue as a Rich table.

    Parameters
    ----------
    queue:
        Sorted ActionQueueEntry list.
    console:
        Rich Console to print to.  Defaults to the module console.
    title:
        Table title string.
    top_n:
        If set, only show the top N entries.
    """
    out = console or _console
    entries = queue[:top_n] if top_n else queue

    table = Table(title=title, show_lines=False)
    table.add_column("Rank", style="bold", justify="right", width=6)
    table.add_column("Task ID", style="cyan", no_wrap=True)
    table.add_column("Score", style="yellow", justify="right", width=8)
    table.add_column("Recommended Action", width=20)
    table.add_column("Autonomy", style="dim", width=16)
    table.add_column("Top Factor", width=24)
    table.add_column("Summary", min_width=30)

    for entry in entries:
        explanation = entry.explanation
        top_factor = (
            max(explanation.factors, key=lambda f: f.weighted_score)
            if explanation.factors
            else None
        )
        top_factor_str = (
            f"{top_factor.factor_name} ({top_factor.weighted_score:.3f})" if top_factor else "—"
        )

        table.add_row(
            f"#{entry.rank}",
            entry.node_id,
            f"{entry.final_score:.3f}",
            entry.recommended_action,
            entry.autonomy_level.value,
            top_factor_str,
            explanation.summary,
        )

    out.print(table)
    out.print(f"[dim]Total in queue: {len(queue)}[/dim]")


def format_briefing(
    briefing_text: str,
    console: Console | None = None,
) -> None:
    """Print a pre-formatted briefing string inside a Rich panel.

    Parameters
    ----------
    briefing_text:
        Text returned by agent.briefing.format_briefing().
    console:
        Rich Console to print to.  Defaults to the module console.
    """
    out = console or _console
    out.print(Panel(briefing_text, title="Agent Briefing", border_style="blue"))


__all__ = [
    "format_task_table",
    "format_task_panel",
    "format_goal_table",
    "format_goal_panel",
    "format_score_explanation",
    "format_action_queue",
    "format_briefing",
]

# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.cli.goal_commands — Goal management CLI sub-commands.

Description
-----------
Implements the two ``graphclaw goal`` sub-commands: ``list`` and ``show``.
``list`` retrieves all GoalNode vertices (optionally filtered by state) and
displays them as a Rich table.  ``show`` fetches a single goal by ID and
displays it as a Rich panel with full details.

Design Patterns
---------------
- Async Bridge: Each Typer command delegates to an async helper via ``run_async()``.

Public API
----------
- app: The ``typer.Typer`` instance for the ``goal`` sub-group.

Dependencies
------------
- graphclaw.cli.formatters: format_goal_panel, format_goal_table.
- graphclaw.cli._shared: cli_pool.
- graphclaw.models.nodes: GoalNode.
- typer: CLI framework.
- rich: Console output.
"""

from __future__ import annotations

import typer
from rich.console import Console

from graphclaw.cli._shared import cli_pool, run_async
from graphclaw.cli.formatters import format_goal_panel, format_goal_table

app = typer.Typer(help="Goal management commands")
console = Console()
err_console = Console(stderr=True, style="bold red")


# ---------------------------------------------------------------------------
# Async helpers
# ---------------------------------------------------------------------------


async def _list_goals_async(state_filter: str | None) -> None:
    from graphclaw.models.nodes import GoalNode

    async with cli_pool() as (_, repo):
        filters = {}
        if state_filter:
            filters["state"] = state_filter.upper()
        raw_nodes = await repo.list_nodes("GoalNode", filters=filters)
        goals: list[GoalNode] = []
        for props in raw_nodes:
            try:
                goals.append(GoalNode.model_validate(props))
            except Exception:
                pass
        if not goals:
            console.print("[dim]No goals found.[/dim]")
            return
        title = f"Goals{f' (state={state_filter.upper()})' if state_filter else ''}"
        format_goal_table(goals, title=title, console=console)


async def _show_goal_async(goal_id: str) -> None:
    from graphclaw.models.nodes import GoalNode

    async with cli_pool() as (_, repo):
        props = await repo.get_node(goal_id)
        if props is None:
            err_console.print(f"Goal '{goal_id}' not found.")
            raise typer.Exit(code=1)
        try:
            goal = GoalNode.model_validate(props)
        except Exception as exc:
            err_console.print(f"Could not parse goal data: {exc}")
            raise typer.Exit(code=1)
        format_goal_panel(goal, console=console)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command("list")
def goal_list(
    state: str | None = typer.Option(
        None,
        "--state",
        "-s",
        help="Filter by goal state (e.g. ACTIVE, COMPLETE, ON_HOLD).",
    ),
) -> None:
    """List goals, optionally filtered by state."""
    try:
        run_async(_list_goals_async(state))
    except SystemExit:
        raise
    except Exception as exc:
        err_console.print(f"Error: {exc}")
        raise typer.Exit(code=1)


@app.command("show")
def goal_show(
    goal_id: str = typer.Argument(..., help="Goal ID (e.g. GOAL-abc123)"),
) -> None:
    """Show details of a single goal."""
    try:
        run_async(_show_goal_async(goal_id))
    except SystemExit:
        raise
    except Exception as exc:
        err_console.print(f"Error: {exc}")
        raise typer.Exit(code=1)

"""CLI goal subcommands for GraphClaw.

Commands
--------
goal list  — list all goals
goal show  — show details of a single goal
"""
from __future__ import annotations

import asyncio
from typing import Optional

import typer
from rich.console import Console

from graphclaw.cli.formatters import format_goal_panel, format_goal_table

app = typer.Typer(help="Goal management commands")
console = Console()
err_console = Console(stderr=True, style="bold red")


# ---------------------------------------------------------------------------
# Async helpers
# ---------------------------------------------------------------------------


async def _list_goals_async(state_filter: str | None) -> None:
    import os

    from graphclaw.db.connection import create_pool
    from graphclaw.db.graph_repository import GraphRepository
    from graphclaw.models.nodes import GoalNode

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        err_console.print(
            "DATABASE_URL is not set. "
            "Set it in your environment or .env file before running CLI commands."
        )
        raise typer.Exit(code=1)

    pool = await create_pool(dsn)
    try:
        repo = GraphRepository(pool)
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
    finally:
        await pool.close()


async def _show_goal_async(goal_id: str) -> None:
    import os

    from graphclaw.db.connection import create_pool
    from graphclaw.db.graph_repository import GraphRepository
    from graphclaw.models.nodes import GoalNode

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        err_console.print("DATABASE_URL is not set.")
        raise typer.Exit(code=1)

    pool = await create_pool(dsn)
    try:
        repo = GraphRepository(pool)
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
    finally:
        await pool.close()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command("list")
def goal_list(
    state: Optional[str] = typer.Option(
        None,
        "--state",
        "-s",
        help="Filter by goal state (e.g. ACTIVE, COMPLETE, ON_HOLD).",
    ),
) -> None:
    """List goals, optionally filtered by state."""
    try:
        asyncio.run(_list_goals_async(state))
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
        asyncio.run(_show_goal_async(goal_id))
    except SystemExit:
        raise
    except Exception as exc:
        err_console.print(f"Error: {exc}")
        raise typer.Exit(code=1)

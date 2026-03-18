"""CLI task subcommands for GraphClaw.

Commands
--------
task list   — list tasks, optionally filtered by state
task show   — show detailed view of a single task
task create — create a new task (interactive prompts or flags)
task transition — transition a task to a new state
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional

import typer
from rich.console import Console

from graphclaw.cli.formatters import format_task_panel, format_task_table

app = typer.Typer(help="Task management commands")
console = Console()
err_console = Console(stderr=True, style="bold red")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_repo_and_pool():
    """Create a GraphRepository from environment config.

    Returns (pool, GraphRepository).  Raises SystemExit on failure.
    """
    import os

    from graphclaw.db.connection import create_pool
    from graphclaw.db.graph_repository import GraphRepository

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        err_console.print(
            "DATABASE_URL is not set. "
            "Set it in your environment or .env file before running CLI commands."
        )
        raise typer.Exit(code=1)

    try:
        pool = asyncio.get_event_loop().run_until_complete(create_pool(dsn))
        repo = GraphRepository(pool)
        return pool, repo
    except Exception as exc:
        err_console.print(f"Could not connect to the database: {exc}")
        raise typer.Exit(code=1)


async def _list_tasks_async(state_filter: str | None) -> None:
    import os

    from graphclaw.db.connection import create_pool
    from graphclaw.db.graph_repository import GraphRepository
    from graphclaw.models.nodes import TaskNode

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
        raw_nodes = await repo.list_nodes("TaskNode", filters=filters)
        tasks: list[TaskNode] = []
        for props in raw_nodes:
            try:
                tasks.append(TaskNode.model_validate(props))
            except Exception:
                pass
        if not tasks:
            console.print("[dim]No tasks found.[/dim]")
            return
        title = f"Tasks{f' (state={state_filter.upper()})' if state_filter else ''}"
        format_task_table(tasks, title=title, console=console)
    finally:
        await pool.close()


async def _show_task_async(task_id: str) -> None:
    import os

    from graphclaw.db.connection import create_pool
    from graphclaw.db.graph_repository import GraphRepository
    from graphclaw.models.nodes import TaskNode

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        err_console.print("DATABASE_URL is not set.")
        raise typer.Exit(code=1)

    pool = await create_pool(dsn)
    try:
        repo = GraphRepository(pool)
        props = await repo.get_node(task_id)
        if props is None:
            err_console.print(f"Task '{task_id}' not found.")
            raise typer.Exit(code=1)
        try:
            task = TaskNode.model_validate(props)
        except Exception as exc:
            err_console.print(f"Could not parse task data: {exc}")
            raise typer.Exit(code=1)
        format_task_panel(task, console=console)
    finally:
        await pool.close()


async def _create_task_async(
    task_type: str,
    title: str,
    description: str,
    deadline: str | None,
    effort: float | None,
) -> None:
    import os
    from datetime import datetime, timezone

    from graphclaw.db.connection import create_pool
    from graphclaw.db.graph_repository import GraphRepository
    from graphclaw.models.base import generate_task_id, utcnow
    from graphclaw.models.enums import TaskType
    from graphclaw.models.nodes import TaskNode, Timeline

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        err_console.print("DATABASE_URL is not set.")
        raise typer.Exit(code=1)

    try:
        t_type = TaskType(task_type.upper())
    except ValueError:
        valid = ", ".join(t.value for t in TaskType)
        err_console.print(f"Invalid task type '{task_type}'. Valid: {valid}")
        raise typer.Exit(code=1)

    deadline_dt: Optional[datetime] = None
    if deadline:
        try:
            deadline_dt = datetime.strptime(deadline, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            err_console.print(
                f"Invalid deadline format '{deadline}'. Expected YYYY-MM-DD."
            )
            raise typer.Exit(code=1)

    now = utcnow()
    task = TaskNode(
        id=generate_task_id("XX", t_type),
        task_type=t_type,
        title=title,
        description=description,
        created_at=now,
        updated_at=now,
        timeline=Timeline(
            deadline=deadline_dt,
            estimated_effort_days=effort,
        ),
    )

    pool = await create_pool(dsn)
    try:
        repo = GraphRepository(pool)
        await repo.create_node(task)
        console.print(f"[green]Created task:[/green] [cyan]{task.id}[/cyan]  {task.title}")
    finally:
        await pool.close()


async def _transition_task_async(task_id: str, new_state: str) -> None:
    import os

    from graphclaw.db.connection import create_pool
    from graphclaw.db.graph_repository import GraphRepository
    from graphclaw.models.enums import ChangedBy, TaskState
    from graphclaw.models.nodes import TaskNode
    from graphclaw.state.machine import StateMachine
    from graphclaw.state.transitions import InvalidTransitionError

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        err_console.print("DATABASE_URL is not set.")
        raise typer.Exit(code=1)

    try:
        target_state = TaskState(new_state.upper())
    except ValueError:
        valid = ", ".join(s.value for s in TaskState)
        err_console.print(f"Invalid state '{new_state}'. Valid: {valid}")
        raise typer.Exit(code=1)

    pool = await create_pool(dsn)
    try:
        repo = GraphRepository(pool)
        props = await repo.get_node(task_id)
        if props is None:
            err_console.print(f"Task '{task_id}' not found.")
            raise typer.Exit(code=1)

        try:
            task = TaskNode.model_validate(props)
        except Exception as exc:
            err_console.print(f"Could not parse task: {exc}")
            raise typer.Exit(code=1)

        sm = StateMachine()
        try:
            sm.transition(task, target_state, ChangedBy.HUMAN, "CLI transition")
        except InvalidTransitionError as exc:
            err_console.print(f"Invalid transition: {exc}")
            raise typer.Exit(code=1)

        await repo.update_node(
            task_id,
            {
                "state": task.state.value,
                "state_history": [
                    e.model_dump(mode="json") for e in task.state_history
                ],
                "updated_at": task.updated_at.isoformat(),
            },
        )
        console.print(
            f"[green]Transitioned[/green] [cyan]{task_id}[/cyan] "
            f"to [bold]{target_state.value}[/bold]"
        )
    finally:
        await pool.close()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command("list")
def task_list(
    state: Optional[str] = typer.Option(
        None,
        "--state",
        "-s",
        help="Filter by task state (e.g. ACTIVE, IN_PROGRESS, BLOCKED).",
    ),
) -> None:
    """List tasks, optionally filtered by state."""
    try:
        asyncio.run(_list_tasks_async(state))
    except SystemExit:
        raise
    except Exception as exc:
        err_console.print(f"Error: {exc}")
        raise typer.Exit(code=1)


@app.command("show")
def task_show(
    task_id: str = typer.Argument(..., help="Task ID (e.g. TSK-JD-1234-ATM)"),
) -> None:
    """Show details of a single task."""
    try:
        asyncio.run(_show_task_async(task_id))
    except SystemExit:
        raise
    except Exception as exc:
        err_console.print(f"Error: {exc}")
        raise typer.Exit(code=1)


@app.command("create")
def task_create(
    title: str = typer.Option(..., "--title", "-t", help="Task title."),
    task_type: str = typer.Option(
        "ATOMIC", "--type", "-T", help="Task type (ATOMIC, DELEGATED, etc.)."
    ),
    description: str = typer.Option("", "--description", "-d", help="Task description."),
    deadline: Optional[str] = typer.Option(
        None, "--deadline", help="Deadline date (YYYY-MM-DD)."
    ),
    effort: Optional[float] = typer.Option(
        None, "--effort", "-e", help="Estimated effort in days."
    ),
) -> None:
    """Create a new task."""
    try:
        asyncio.run(_create_task_async(task_type, title, description, deadline, effort))
    except SystemExit:
        raise
    except Exception as exc:
        err_console.print(f"Error: {exc}")
        raise typer.Exit(code=1)


@app.command("transition")
def task_transition(
    task_id: str = typer.Argument(..., help="Task ID to transition."),
    new_state: str = typer.Argument(..., help="Target state (e.g. IN_PROGRESS)."),
) -> None:
    """Transition a task to a new state."""
    try:
        asyncio.run(_transition_task_async(task_id, new_state))
    except SystemExit:
        raise
    except Exception as exc:
        err_console.print(f"Error: {exc}")
        raise typer.Exit(code=1)

"""graphclaw.cli.task_commands — Task management CLI sub-commands.

Description
-----------
Implements the four ``graphclaw task`` sub-commands: ``list``, ``show``,
``create``, and ``transition``.  Each command reads ``DATABASE_URL`` from the
environment, opens a connection pool, performs its operation, and closes the
pool on exit.  Rich is used for all output formatting.

Design Patterns
---------------
- Async Bridge: Each Typer command is synchronous (required by Typer) but delegates
  to an ``async`` helper via ``run_async()``, keeping all DB I/O async.

Public API
----------
- app: The ``typer.Typer`` instance for the ``task`` sub-group.

Dependencies
------------
- graphclaw.cli.formatters: Rich formatting helpers.
- graphclaw.db.connection: create_pool for database connections.
- graphclaw.db.graph_repository: GraphRepository for task CRUD.
- graphclaw.models.base: generate_task_id, utcnow.
- graphclaw.models.enums: ChangedBy, TaskState, TaskType.
- graphclaw.models.nodes: TaskNode, Timeline.
- graphclaw.state.machine: StateMachine for validated transitions.
- graphclaw.state.transitions: InvalidTransitionError.
- typer: CLI framework.
- rich: Console output.

Notes
-----
``DATABASE_URL`` must be set in the environment before running any task command.
The pool is opened and closed on every command invocation (no persistent connection).
"""

from __future__ import annotations

from datetime import datetime, timezone

import typer
from rich.console import Console

from graphclaw.cli._shared import cli_pool, run_async
from graphclaw.cli.formatters import format_task_panel, format_task_table

app = typer.Typer(help="Task management commands")
console = Console()
err_console = Console(stderr=True, style="bold red")


# ---------------------------------------------------------------------------
# Async helpers
# ---------------------------------------------------------------------------


async def _list_tasks_async(state_filter: str | None) -> None:
    from graphclaw.models.nodes import TaskNode

    async with cli_pool() as (_, repo):
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


async def _show_task_async(task_id: str) -> None:
    from graphclaw.models.nodes import TaskNode

    async with cli_pool() as (_, repo):
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


async def _create_task_async(
    task_type: str,
    title: str,
    description: str,
    deadline: str | None,
    effort: float | None,
) -> None:
    from graphclaw.models.base import generate_task_id, utcnow
    from graphclaw.models.enums import TaskType
    from graphclaw.models.nodes import TaskNode, Timeline

    try:
        t_type = TaskType(task_type.upper())
    except ValueError:
        valid = ", ".join(t.value for t in TaskType)
        err_console.print(f"Invalid task type '{task_type}'. Valid: {valid}")
        raise typer.Exit(code=1)

    deadline_dt: datetime | None = None
    if deadline:
        try:
            deadline_dt = datetime.strptime(deadline, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            err_console.print(f"Invalid deadline format '{deadline}'. Expected YYYY-MM-DD.")
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

    async with cli_pool() as (_, repo):
        await repo.create_node(task)
        console.print(f"[green]Created task:[/green] [cyan]{task.id}[/cyan]  {task.title}")


async def _transition_task_async(task_id: str, new_state: str) -> None:
    from graphclaw.models.enums import ChangedBy, TaskState
    from graphclaw.models.nodes import TaskNode
    from graphclaw.state.machine import StateMachine
    from graphclaw.state.transitions import InvalidTransitionError

    try:
        target_state = TaskState(new_state.upper())
    except ValueError:
        valid = ", ".join(s.value for s in TaskState)
        err_console.print(f"Invalid state '{new_state}'. Valid: {valid}")
        raise typer.Exit(code=1)

    async with cli_pool() as (_, repo):
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
                "state_history": [e.model_dump(mode="json") for e in task.state_history],
                "updated_at": task.updated_at.isoformat(),
            },
        )
        console.print(
            f"[green]Transitioned[/green] [cyan]{task_id}[/cyan] "
            f"to [bold]{target_state.value}[/bold]"
        )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command("list")
def task_list(
    state: str | None = typer.Option(
        None,
        "--state",
        "-s",
        help="Filter by task state (e.g. ACTIVE, IN_PROGRESS, BLOCKED).",
    ),
) -> None:
    """List tasks, optionally filtered by state."""
    try:
        run_async(_list_tasks_async(state))
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
        run_async(_show_task_async(task_id))
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
    deadline: str | None = typer.Option(None, "--deadline", help="Deadline date (YYYY-MM-DD)."),
    effort: float | None = typer.Option(None, "--effort", "-e", help="Estimated effort in days."),
) -> None:
    """Create a new task."""
    try:
        run_async(_create_task_async(task_type, title, description, deadline, effort))
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
        run_async(_transition_task_async(task_id, new_state))
    except SystemExit:
        raise
    except Exception as exc:
        err_console.print(f"Error: {exc}")
        raise typer.Exit(code=1)

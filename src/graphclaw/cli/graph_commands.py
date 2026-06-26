# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.cli.graph_commands — Graph inspection CLI sub-commands.

Description
-----------
Implements the two ``graphclaw graph`` sub-commands: ``stats`` and ``query``.
``stats`` counts vertices by label and displays a summary table.  ``query``
executes a raw Cypher string against the AGE graph and pretty-prints results.

Design Patterns
---------------
- Async Bridge: Each Typer command delegates to an async helper via ``run_async()``.

Public API
----------
- app: The ``typer.Typer`` instance for the ``graph`` sub-group.

Dependencies
------------
- graphclaw.cli._shared: cli_pool.
- graphclaw.db.age.connection: get_connection.
- typer: CLI framework.
- rich: Console and Table for output.

Notes
-----
The ``graph query`` command executes the provided Cypher string with no
sanitisation.  It is a developer tool intended for use in controlled environments
with trusted input only.  Never expose this command to untrusted users.
"""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

from graphclaw.cli._shared import cli_pool, run_async

app = typer.Typer(help="Graph inspection commands")
console = Console()
err_console = Console(stderr=True, style="bold red")

# Node labels tracked in the graph.
_TASK_LABELS = [
    "TaskNode",
    "GoalNode",
    "UserNode",
    "ResourceNode",
    "ConstraintNode",
    "CheckinNode",
]


# ---------------------------------------------------------------------------
# Async helpers
# ---------------------------------------------------------------------------


async def _stats_async() -> None:
    async with cli_pool() as (_, repo):
        table = Table(title="Graph Statistics", show_lines=False)
        table.add_column("Label", style="cyan")
        table.add_column("Node Count", justify="right", style="yellow")

        total = 0
        for label in _TASK_LABELS:
            try:
                nodes = await repo.list_nodes(label)
                count = len(nodes)
            except Exception:
                count = 0
            table.add_row(label, str(count))
            total += count

        table.add_section()
        table.add_row("[bold]Total[/bold]", f"[bold]{total}[/bold]")
        console.print(table)


async def _reset_async(labels: list[str]) -> None:
    from graphclaw.db.age.connection import get_connection

    async with cli_pool() as (pool, _):
        for label in labels:
            if label not in _TASK_LABELS:
                err_console.print(f"Unsupported label: {label}")
                continue
            try:
                async with get_connection(pool) as conn:
                    # label is validated against the static _TASK_LABELS allowlist above,
                    # so the interpolation below is not an injection vector.
                    await conn.execute(
                        f"""
                        SELECT * FROM cypher('graphclaw', $$
                            MATCH (n:{label})
                            DETACH DELETE n
                            RETURN count(n)
                        $$) as (deleted agtype)
                        """  # nosec B608 — label constrained to _TASK_LABELS allowlist
                    )
                console.print(f"[green]✓[/green] Deleted all [bold]{label}[/bold] nodes")
            except Exception as exc:
                err_console.print(f"Failed to delete {label}: {exc}")


async def _query_async(cypher: str) -> None:
    from graphclaw.db.age.connection import get_connection

    async with cli_pool() as (pool, _):
        try:
            async with get_connection(pool) as conn:
                result = await conn.execute(cypher)
                rows = await result.fetchall()

            if not rows:
                console.print("[dim]Query returned no rows.[/dim]")
                return

            console.print(f"[dim]Returned {len(rows)} row(s):[/dim]")
            for i, row in enumerate(rows, 1):
                # Format each column value; parse agtype if possible.
                parts = []
                for col in row:
                    if col is None:
                        parts.append("null")
                    else:
                        raw = str(col)
                        try:
                            parsed = json.loads(raw)
                            parts.append(json.dumps(parsed, indent=2))
                        except (json.JSONDecodeError, TypeError):
                            parts.append(raw)
                console.print(f"[cyan]{i}:[/cyan] {' | '.join(parts)}")
        except Exception as exc:
            err_console.print(f"Query failed: {exc}")
            raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command("stats")
def graph_stats() -> None:
    """Show graph node and edge count statistics."""
    try:
        run_async(_stats_async())
    except SystemExit:
        raise
    except Exception as exc:
        err_console.print(f"Error: {exc}")
        raise typer.Exit(code=1)


@app.command("reset")
def graph_reset(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt."),
    labels: list[str] = typer.Option(
        None,
        "--label",
        "-l",
        help="Node label(s) to delete. Defaults to TaskNode and GoalNode.",
    ),
) -> None:
    """Delete all TaskNode and GoalNode vertices from the graph (dev reset).

    Also removes all incident edges via DETACH DELETE.
    Use --label to target specific labels only.
    """
    targets = labels or ["TaskNode", "GoalNode"]
    if not yes:
        typer.confirm(
            f"This will DELETE ALL nodes with labels {targets}. Continue?",
            abort=True,
        )
    try:
        run_async(_reset_async(targets))
    except SystemExit:
        raise
    except Exception as exc:
        err_console.print(f"Error: {exc}")
        raise typer.Exit(code=1)


@app.command("query")
def graph_query(
    cypher: str = typer.Argument(
        ...,
        help="Raw Cypher query to execute (dev tool — no injection protection).",
    ),
    dangerous_allow_raw: bool = typer.Option(
        False,
        "--dangerous-allow-raw",
        help="Must be set to execute a raw Cypher query. Acknowledges that no "
        "injection protection is applied.",
        is_flag=True,
    ),
) -> None:
    """Execute a raw Cypher query against the graph (developer tool).

    Warning: this command executes the query as-is with no sanitisation.
    Use only in development environments with trusted input.
    The --dangerous-allow-raw flag must be explicitly provided to run.
    """
    if not dangerous_allow_raw:
        err_console.print(
            "Refusing to execute raw Cypher without --dangerous-allow-raw. "
            "This flag acknowledges that the query is executed with no injection "
            "protection and should only be used in trusted development environments."
        )
        raise typer.Exit(code=1)
    console.print(
        "[bold yellow]WARNING: executing raw Cypher with no injection protection.[/bold yellow]"
    )
    try:
        run_async(_query_async(cypher))
    except SystemExit:
        raise
    except Exception as exc:
        err_console.print(f"Error: {exc}")
        raise typer.Exit(code=1)

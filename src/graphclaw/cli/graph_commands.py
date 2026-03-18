"""CLI graph subcommands for GraphClaw.

Commands
--------
graph stats  — show node and edge count statistics
graph query  — execute a raw Cypher query (dev tool)
"""
from __future__ import annotations

import asyncio
import json

import typer
from rich.console import Console
from rich.table import Table

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

    pool = await create_pool(dsn)
    try:
        repo = GraphRepository(pool)
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
    finally:
        await pool.close()


async def _query_async(cypher: str) -> None:
    import os

    from graphclaw.db.connection import create_pool, get_connection

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        err_console.print("DATABASE_URL is not set.")
        raise typer.Exit(code=1)

    pool = await create_pool(dsn)
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
    finally:
        await pool.close()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command("stats")
def graph_stats() -> None:
    """Show graph node and edge count statistics."""
    try:
        asyncio.run(_stats_async())
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
) -> None:
    """Execute a raw Cypher query against the graph (developer tool).

    Warning: this command executes the query as-is with no sanitisation.
    Use only in development environments with trusted input.
    """
    console.print("[dim yellow]Running raw Cypher query...[/dim yellow]")
    try:
        asyncio.run(_query_async(cypher))
    except SystemExit:
        raise
    except Exception as exc:
        err_console.print(f"Error: {exc}")
        raise typer.Exit(code=1)

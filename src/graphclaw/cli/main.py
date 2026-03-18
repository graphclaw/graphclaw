"""GraphClaw CLI entry point.

Defines the root Typer app and mounts the four sub-apps:
  graphclaw task   — task management commands
  graphclaw goal   — goal management commands
  graphclaw agent  — agent reasoning loop commands
  graphclaw graph  — graph inspection commands

Entry point: ``graphclaw.cli.main:app``  (configured in pyproject.toml)
"""
from __future__ import annotations

import typer

from graphclaw.cli.agent_commands import app as agent_app
from graphclaw.cli.goal_commands import app as goal_app
from graphclaw.cli.graph_commands import app as graph_app
from graphclaw.cli.task_commands import app as task_app

app = typer.Typer(
    name="graphclaw",
    help="GraphClaw task orchestration CLI",
    no_args_is_help=True,
)

app.add_typer(task_app, name="task")
app.add_typer(goal_app, name="goal")
app.add_typer(agent_app, name="agent")
app.add_typer(graph_app, name="graph")


if __name__ == "__main__":
    app()

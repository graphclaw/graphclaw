"""graphclaw.cli.main — Root Typer application that mounts all CLI sub-commands.

Description
-----------
Defines the top-level ``graphclaw`` Typer application and registers the four
sub-apps (task, goal, agent, graph) as named sub-commands.  This module is the
entry point configured in ``pyproject.toml`` under ``[project.scripts]``.

Design Patterns
---------------
- Composite: The root app uses Typer's ``add_typer`` to delegate routing to
  four independent sub-apps, each in its own module.

Public API
----------
- app: The root ``typer.Typer`` application instance.

Dependencies
------------
- graphclaw.cli.agent_commands: Agent reasoning loop sub-commands.
- graphclaw.cli.goal_commands: Goal management sub-commands.
- graphclaw.cli.graph_commands: Graph inspection sub-commands.
- graphclaw.cli.task_commands: Task management sub-commands.
- typer: CLI framework.
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

---
name: graphclaw-cli-patterns
description: CLI interface patterns for GraphClaw Phase 0 using Typer and Rich. Use when implementing CLI commands for task CRUD, graph inspection, or agent invocation.
---

# GraphClaw CLI Patterns

## Library Choice

- **typer**: Type-hint driven CLI framework, good Pydantic integration
- **rich**: Terminal output formatting (tables, panels, trees)

## Command Structure

```python
import typer

app = typer.Typer(name="graphclaw", help="GraphClaw Task Graph Management")
task_app = typer.Typer(help="Task operations")
goal_app = typer.Typer(help="Goal operations")
agent_app = typer.Typer(help="Agent operations")
graph_app = typer.Typer(help="Graph inspection")

app.add_typer(task_app, name="task")
app.add_typer(goal_app, name="goal")
app.add_typer(agent_app, name="agent")
app.add_typer(graph_app, name="graph")
```

## Commands

### Task CRUD
```
graphclaw task create --type ATOMIC --title "..." --deadline 2026-03-20 --effort 4
graphclaw task list [--state ACTIVE] [--goal GOAL-001]
graphclaw task show TSK-JD-4821-ATM
graphclaw task update TSK-JD-4821-ATM --state IN_PROGRESS
graphclaw task complete TSK-JD-4821-ATM
```

### Goal Operations
```
graphclaw goal create --title "..." --priority P1
graphclaw goal list
graphclaw goal show GOAL-001 --tree    # Show full task tree under goal
```

### Agent Operations
```
graphclaw agent run                    # Full reasoning loop: score -> recommend
graphclaw agent score                  # Score all tasks, display ranked list
graphclaw agent explain TSK-JD-4821    # Show score breakdown for one task
```

### Graph Inspection
```
graphclaw graph show                   # Overview: node counts, edge counts
graphclaw graph path GOAL-001          # Show critical path
graphclaw graph deps TSK-JD-4821       # Show dependency chain (up + down)
```

## Output Formatting

### Task List Table
```python
from rich.console import Console
from rich.table import Table

def display_task_list(tasks: list[TaskNode], scores: dict[str, float]):
    console = Console()
    table = Table(title="Tasks")
    table.add_column("Rank", style="bold")
    table.add_column("ID", style="cyan")
    table.add_column("Title")
    table.add_column("State", style="green")
    table.add_column("Deadline")
    table.add_column("Score", style="yellow", justify="right")

    for i, task in enumerate(tasks, 1):
        table.add_row(
            str(i), task.id, task.title,
            task.state.value,
            task.timeline.deadline.strftime("%Y-%m-%d") if task.timeline.deadline else "—",
            f"{scores.get(task.id, 0):.2f}"
        )
    console.print(table)
```

### Score Explanation Panel
```python
from rich.panel import Panel

def display_explanation(exp: ScoreExplanation):
    console = Console()
    lines = [f"[bold]{exp.summary}[/bold]\n"]
    for f in exp.factors:
        bar = "█" * int(f.weighted_score * 20)
        lines.append(f"  {f.factor_name:<22} {f.weighted_score:>5.2f}  {bar}  {f.plain_english}")
    if exp.topology_note:
        lines.append(f"\n[dim]{exp.topology_note}[/dim]")
    console.print(Panel("\n".join(lines), title=f"Score: {exp.final_score:.2f} (Rank #{exp.rank})"))
```

### Dependency Tree
```python
from rich.tree import Tree

def display_deps(task_id: str, upstream: list, downstream: list):
    console = Console()
    tree = Tree(f"[bold cyan]{task_id}[/bold cyan]")

    up = tree.add("[yellow]Upstream (blocks this)[/yellow]")
    for t in upstream:
        up.add(f"{t.id} [{t.state.value}] {t.title}")

    down = tree.add("[green]Downstream (depends on this)[/green]")
    for t in downstream:
        down.add(f"{t.id} [{t.state.value}] {t.title}")

    console.print(tree)
```

## Agent Run Flow

```python
@agent_app.command("run")
def agent_run():
    """Execute full agent reasoning loop."""
    console = Console()
    with console.status("Loading graph..."):
        graph = load_graph()
        user = load_user()

    with console.status("Scoring tasks..."):
        scores = engine.score_all(graph, user)

    with console.status("Analyzing topology..."):
        action_queue = build_action_queue(scores, graph)

    with console.status("Consulting AI agent..."):
        recommendations = agent.reason(graph, action_queue, user)

    display_recommendations(recommendations)
```

## Conventions

- JSON output for machine consumption: `--json` flag
- Rich tables for human consumption (default)
- Error messages via `typer.echo(style=...)` with clear descriptions
- Async DB operations wrapped in `asyncio.run()` at CLI boundary

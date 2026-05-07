---
name: graphclaw-cli-tests
description: CLI test patterns for GraphClaw using Typer CliRunner — testing Typer command exit codes, stdout output, and error handling. Use when writing tests in tests/test_cli/.
---

# GraphClaw CLI Test Patterns

## When to use
Writing tests under `tests/test_cli/` for Typer command-line commands. These are deterministic, fast, and run in every CI build — no Docker stack required.

---

## File header template

```python
"""
GC-U-CLI-<W>-<NNN> — <Command group> CLI commands

Scenario: Typer CLI commands in the <group> module respond correctly
to valid inputs, invalid inputs, and edge cases.

PRD: docs/prd/NN-cli.md §AC-N.N
Build wave: W<NN>
Layer: CLI (L1 Unit)
Owner: backend-team
Last reviewed: YYYY-MM-DD

Cases covered:
- `task create` outputs new task ID on success
- `task create` exits 1 with message on missing goal
- `task list` outputs JSON array
"""
```

---

## CliRunner pattern

```python
from typer.testing import CliRunner
from graphclaw.cli import app

runner = CliRunner()

def test_task_create_outputs_id():
    result = runner.invoke(app, ["task", "create", "--title", "Ship it", "--goal", "g-001"])
    assert result.exit_code == 0
    assert "task_id=" in result.stdout

def test_task_create_missing_goal_exits_1():
    result = runner.invoke(app, ["task", "create", "--title", "Ship it"])
    assert result.exit_code != 0
    assert "goal" in result.output.lower()

def test_task_list_returns_json():
    import json
    result = runner.invoke(app, ["task", "list", "--format", "json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert isinstance(data, list)
```

---

## What to test

- **Exit codes**: 0 on success, non-zero on error. Always assert `exit_code`.
- **Stdout content**: Key fields present in output (IDs, status labels). Parse JSON if `--format json` is supported.
- **Error messages**: Human-readable error on bad input. Check `result.output` (combines stdout+stderr).
- **`--help`**: Every command should produce help text.

## What NOT to test here

- Real database operations → use `tests/integration/` instead.
- Real API calls → use `tests/integration/` or mock the HTTP client.
- LLM interactions → use `tests/agent_evals/`.

---

## Mocking in CLI tests

For CLI commands that call the API or LLM, patch at the function boundary:

```python
from unittest.mock import patch, AsyncMock

def test_agent_chat_invokes_orchestrator():
    with patch("graphclaw.cli.agent.run_chat_turn", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = "Task created."
        result = runner.invoke(app, ["agent", "chat", "create a task"])
        assert result.exit_code == 0
        mock_chat.assert_called_once()
```

---

## Inventory

Add to `tests/test_cli/inventory.md`:
```
| GC-U-CLI-W08-014 | `task create` prints new task ID | `test_task_commands.py` |
```

"""graphclaw.cli.intelligence_commands — Intelligence Hub CLI sub-commands.

Description
-----------
Provides CLI commands for inspecting and managing agent intelligence objects
stored in object storage (MinIO / S3).  These commands are primarily intended
for local development, debugging, and testing without needing the full cockpit
UI.

Commands
--------
intelligence agents list              — List all user sub-agents in MinIO
intelligence agents delete <agent_id> — Delete all files for a sub-agent
intelligence agents audit             — Report agents in MinIO vs. canvas definitions

intelligence profile show  <agent_id>        — Print agent profile.md
intelligence profile set   <agent_id> <file> — Upload a profile.md file

intelligence memory working show  <agent_id>          — Print working context
intelligence memory working set   <agent_id> <file>   — Write working context
intelligence memory working compact <agent_id>        — Compact working context (interactive)
intelligence memory episodic list <agent_id>          — List episodic entries
intelligence memory episodic show <agent_id> <entry>  — Print one episodic entry
intelligence memory episodic del  <agent_id> <entry>  — Delete one episodic entry
intelligence memory semantic list <agent_id>          — List semantic topics
intelligence memory semantic show <agent_id> <topic>  — Print one semantic topic
intelligence memory semantic del  <agent_id> <topic>  — Delete one semantic topic

intelligence skill list              — List authored skills
intelligence skill show  <skill_id>  — Print authored SKILL.md content
intelligence skill create <file>     — Upload and create an authored skill
intelligence skill update <skill_id> <file> — Overwrite authored skill
intelligence skill delete <skill_id> — Delete authored skill
intelligence skill validate <file>   — Validate a SKILL.md file locally
intelligence skill paths             — Print all StoragePaths for a user (debug)

Design Patterns
---------------
- asyncio.run: Each Typer command is synchronous; async work is done via
  ``run_async()`` so the CLI stays compatible with Typer's sync model.
- Fail-fast on missing env: Storage is configured from environment variables;
  commands print a helpful error if they are missing.

Dependencies
------------
- graphclaw.infra.storage: S3StorageClient, StoragePaths.
- graphclaw.infra.config: StorageConfig.
- graphclaw.skills.parser: SkillParser (for validate command).
- typer: CLI framework.
- rich: Formatted output.
"""

from __future__ import annotations

import os
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from graphclaw.cli._shared import run_async
from graphclaw.infra.storage import S3StorageClient, StoragePaths

console = Console()
err_console = Console(stderr=True)

app = typer.Typer(
    name="intelligence",
    help="Intelligence Hub — agent memory, profiles, and skill authoring.",
    no_args_is_help=True,
)

agents_app = typer.Typer(help="Sub-agent management commands.", no_args_is_help=True)
profile_app = typer.Typer(help="Agent profile commands.", no_args_is_help=True)
memory_app = typer.Typer(help="Agent memory commands.", no_args_is_help=True)
working_app = typer.Typer(help="Working context commands.", no_args_is_help=True)
episodic_app = typer.Typer(help="Episodic memory commands.", no_args_is_help=True)
semantic_app = typer.Typer(help="Semantic memory commands.", no_args_is_help=True)
skill_app = typer.Typer(help="Skill authoring commands.", no_args_is_help=True)

app.add_typer(agents_app, name="agents")
app.add_typer(profile_app, name="profile")
app.add_typer(memory_app, name="memory")
memory_app.add_typer(working_app, name="working")
memory_app.add_typer(episodic_app, name="episodic")
memory_app.add_typer(semantic_app, name="semantic")
app.add_typer(skill_app, name="skill")

# ---------------------------------------------------------------------------
# Storage client factory
# ---------------------------------------------------------------------------


def _storage_client() -> S3StorageClient:
    """Build an S3StorageClient from environment variables.

    Required env vars: STORAGE_BUCKET, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
    Optional env vars: STORAGE_ENDPOINT_URL (MinIO), STORAGE_REGION
    """
    bucket = os.environ.get("STORAGE_BUCKET", "graphclaw")
    endpoint = os.environ.get("STORAGE_ENDPOINT_URL")
    region = os.environ.get("STORAGE_REGION", "us-east-1")
    return S3StorageClient(bucket=bucket, endpoint_url=endpoint, region=region)


def _require_user_id() -> str:
    """Return GRAPHCLAW_USER_ID from env or abort with a helpful message."""
    user_id = os.environ.get("GRAPHCLAW_USER_ID")
    if not user_id:
        err_console.print(
            "[red]ERROR:[/red] GRAPHCLAW_USER_ID env var not set. "
            "Export it before running intelligence commands.\n"
            "  export GRAPHCLAW_USER_ID=your-user-id"
        )
        raise SystemExit(1)
    return user_id


# ---------------------------------------------------------------------------
# Agents management commands
# ---------------------------------------------------------------------------


@agents_app.command("list")
def agents_list() -> None:
    """List all user sub-agents provisioned in MinIO."""

    async def _run() -> None:
        user_id = _require_user_id()
        client = _storage_client()
        prefix = StoragePaths.agents_prefix(user_id)
        keys = await client.list_objects(prefix)

        # Collect agent IDs from any file under {user_id}/agents/{agent_id}/
        agent_ids: dict[str, dict[str, str]] = {}
        for key in keys:
            # key format: {user_id}/agents/{agent_id}/...
            relative = key[len(prefix) :]
            parts = relative.split("/")
            if not parts or not parts[0]:
                continue
            agent_id = parts[0]
            if agent_id not in agent_ids:
                agent_ids[agent_id] = {"name": "", "has_profile": "✗", "has_memory": "✗"}
            if parts[-1] == "profile.md":
                agent_ids[agent_id]["has_profile"] = "✓"
            if len(parts) > 1 and parts[1] == "memory":
                agent_ids[agent_id]["has_memory"] = "✓"

        # Read name from manifest.json where available
        for agent_id, info in agent_ids.items():
            manifest_path = StoragePaths.agent_manifest(user_id, agent_id)
            try:
                import json as _json

                raw = await client.read(manifest_path)
                manifest = _json.loads(raw.decode())
                info["name"] = manifest.get("name", "")
            except (FileNotFoundError, Exception):
                pass

        if not agent_ids:
            console.print("[dim]No sub-agents found.[/dim]")
            return

        table = Table(title=f"Sub-Agents — {user_id}", show_header=True)
        table.add_column("Agent ID", style="cyan")
        table.add_column("Name", style="bold")
        table.add_column("Profile", justify="center")
        table.add_column("Memory", justify="center")
        for agent_id, info in sorted(agent_ids.items()):
            table.add_row(
                agent_id,
                info["name"] or "[dim]—[/dim]",
                f"[green]{info['has_profile']}[/green]"
                if info["has_profile"] == "✓"
                else f"[red]{info['has_profile']}[/red]",
                f"[green]{info['has_memory']}[/green]"
                if info["has_memory"] == "✓"
                else "[dim]✗[/dim]",
            )
        console.print(table)

    run_async(_run())


@agents_app.command("delete")
def agents_delete(
    agent_id: str = typer.Argument(..., help="Agent ID to delete"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Delete all MinIO objects for a sub-agent (profile, config, memory)."""

    async def _run() -> None:
        user_id = _require_user_id()
        if not yes:
            typer.confirm(
                f"Delete ALL files for agent '{agent_id}'? This cannot be undone.",
                abort=True,
            )
        client = _storage_client()
        prefix = StoragePaths.agent_root(user_id, agent_id)
        keys = await client.list_objects(prefix)
        if not keys:
            err_console.print(f"[yellow]⚠[/yellow] No objects found under prefix: {prefix}")
            raise SystemExit(1)
        deleted = 0
        for key in keys:
            try:
                await client.delete(key)
                deleted += 1
            except Exception as exc:
                err_console.print(f"[red]Failed to delete[/red] {key}: {exc}")
        console.print(
            f"[green]✓[/green] Deleted [bold]{deleted}[/bold] object(s) for agent "
            f"[bold]{agent_id}[/bold]"
        )

    run_async(_run())


@agents_app.command("audit")
def agents_audit() -> None:
    """Report sub-agents in MinIO vs. canvas definitions (orphan detection).

    Orphans are agents that exist in MinIO runtime storage but have no
    corresponding canvas definition JSON under agents/{user_id}/definitions/.
    These may be created by the orchestrator or left over from deleted canvas
    definitions.
    """

    async def _run() -> None:
        import json as _json

        user_id = _require_user_id()
        client = _storage_client()

        # Collect runtime agents from {user_id}/agents/
        runtime_prefix = StoragePaths.agents_prefix(user_id)
        runtime_keys = await client.list_objects(runtime_prefix)
        runtime_ids: set[str] = set()
        for key in runtime_keys:
            relative = key[len(runtime_prefix) :]
            parts = relative.split("/")
            if parts and parts[0]:
                runtime_ids.add(parts[0])

        # Collect canvas definitions from agents/{user_id}/definitions/
        canvas_prefix = f"agents/{user_id}/definitions/"
        canvas_keys = await client.list_objects(canvas_prefix)
        canvas_ids: set[str] = set()
        for key in canvas_keys:
            if key.endswith(".json"):
                name = key[len(canvas_prefix) :].removesuffix(".json")
                if "/" not in name and name:
                    canvas_ids.add(name)

        runtime_only = sorted(runtime_ids - canvas_ids)
        canvas_only = sorted(canvas_ids - runtime_ids)
        both = sorted(runtime_ids & canvas_ids)

        table = Table(title=f"Agent Audit — {user_id}", show_header=True)
        table.add_column("Agent ID", style="cyan")
        table.add_column("Runtime (MinIO)", justify="center")
        table.add_column("Canvas Def", justify="center")
        table.add_column("Status")

        for agent_id in both:
            table.add_row(agent_id, "[green]✓[/green]", "[green]✓[/green]", "OK")
        for agent_id in runtime_only:
            table.add_row(
                agent_id,
                "[green]✓[/green]",
                "[red]✗[/red]",
                "[yellow]runtime-only (orphan?)[/yellow]",
            )
        for agent_id in canvas_only:
            table.add_row(
                agent_id,
                "[red]✗[/red]",
                "[green]✓[/green]",
                "[blue]canvas-only (not provisioned)[/blue]",
            )

        console.print(table)

        if runtime_only:
            console.print(
                f"\n[yellow]⚠[/yellow]  {len(runtime_only)} runtime-only agent(s) found. "
                "Use [bold]intelligence agents delete <id>[/bold] to remove orphans."
            )
        else:
            console.print("\n[green]✓[/green]  No orphaned runtime agents found.")

        _ = _json  # suppress unused import warning

    run_async(_run())


# ---------------------------------------------------------------------------
# Profile commands
# ---------------------------------------------------------------------------


@profile_app.command("show")
def profile_show(
    agent_id: str = typer.Argument(default="main", help="Agent ID"),
) -> None:
    """Print the agent's profile.md document."""

    async def _run() -> None:
        user_id = _require_user_id()
        client = _storage_client()
        path = StoragePaths.agent_profile(user_id, agent_id)
        try:
            raw = await client.read(path)
            content = raw.decode()
        except FileNotFoundError:
            content = f"# Agent: {agent_id}\n\n*(no profile defined)*\n"

        console.print(Panel(Syntax(content, "markdown", theme="github-dark"), title=path))

    run_async(_run())


@profile_app.command("set")
def profile_set(
    file: Path = typer.Argument(..., help="Path to profile.md file"),
    agent_id: str = typer.Argument(default="main", help="Agent ID"),
) -> None:
    """Upload a profile.md file for the agent."""

    async def _run() -> None:
        user_id = _require_user_id()
        if not file.exists():
            err_console.print(f"[red]ERROR:[/red] File not found: {file}")
            raise SystemExit(1)
        client = _storage_client()
        path = StoragePaths.agent_profile(user_id, agent_id)
        await client.write(path, file.read_bytes(), content_type="text/markdown")
        console.print(f"[green]✓[/green] Profile written to [bold]{path}[/bold]")

    run_async(_run())


# ---------------------------------------------------------------------------
# Memory — working context
# ---------------------------------------------------------------------------


@working_app.command("show")
def working_show(
    agent_id: str = typer.Argument(default="main", help="Agent ID"),
) -> None:
    """Print the agent's current working context."""

    async def _run() -> None:
        user_id = _require_user_id()
        client = _storage_client()
        path = StoragePaths.agent_memory_working(user_id, agent_id)
        try:
            raw = await client.read(path)
            content = raw.decode()
        except FileNotFoundError:
            content = "*(empty — no working context yet)*"

        console.print(Panel(Syntax(content, "markdown", theme="github-dark"), title=path))

    run_async(_run())


@working_app.command("set")
def working_set(
    file: Path = typer.Argument(..., help="Path to context Markdown file"),
    agent_id: str = typer.Argument(default="main", help="Agent ID"),
) -> None:
    """Write a file's content as the working context."""

    async def _run() -> None:
        user_id = _require_user_id()
        if not file.exists():
            err_console.print(f"[red]ERROR:[/red] File not found: {file}")
            raise SystemExit(1)
        client = _storage_client()
        path = StoragePaths.agent_memory_working(user_id, agent_id)
        await client.write(path, file.read_bytes(), content_type="text/markdown")
        console.print(f"[green]✓[/green] Working context written to [bold]{path}[/bold]")

    run_async(_run())


@working_app.command("compact")
def working_compact(
    agent_id: str = typer.Argument(default="main", help="Agent ID"),
    summary: str | None = typer.Option(None, "--summary", "-s", help="Compact summary text"),
    label: str | None = typer.Option(None, "--label", "-l", help="Session label for archive"),
) -> None:
    """Archive the working context to episodic memory and replace with a summary.

    If --summary is not provided, opens an interactive prompt.
    """

    async def _run(summary_text: str) -> None:
        import uuid
        from datetime import datetime, timezone

        user_id = _require_user_id()
        client = _storage_client()
        working_path = StoragePaths.agent_memory_working(user_id, agent_id)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        session_label = label or uuid.uuid4().hex[:8]
        entry_name = f"{today}-compact-{session_label}.md"
        episodic_path = StoragePaths.agent_memory_episodic_entry(user_id, agent_id, entry_name)
        working_archive_path = StoragePaths.agent_memory_working_archive_entry(
            user_id, agent_id, entry_name
        )

        try:
            original = await client.read(working_path)
            archive = (
                f"# Compacted Context — {today}\n\n"
                f"*Session: {session_label}*\n\n"
                f"## Original Working Context\n\n" + original.decode()
            )
            await client.write(episodic_path, archive.encode(), content_type="text/markdown")
            await client.write(working_archive_path, archive.encode(), content_type="text/markdown")
            console.print(f"[blue]→[/blue] Archived to [bold]{entry_name}[/bold]")
        except FileNotFoundError:
            console.print("[yellow]⚠[/yellow] No existing working context to archive")

        await client.write(working_path, summary_text.encode(), content_type="text/markdown")
        console.print("[green]✓[/green] Working context replaced with compact summary")

    if summary:
        run_async(_run(summary))
    else:
        console.print(
            "[bold]Enter compact summary[/bold] (Ctrl+D or empty line + Enter to finish):"
        )
        lines: list[str] = []
        try:
            while True:
                line = input()
                lines.append(line)
        except EOFError:
            pass
        run_async(_run("\n".join(lines)))


# ---------------------------------------------------------------------------
# Memory — episodic
# ---------------------------------------------------------------------------


@episodic_app.command("list")
def episodic_list(
    agent_id: str = typer.Argument(default="main", help="Agent ID"),
) -> None:
    """List all episodic memory entries for the agent."""

    async def _run() -> None:
        user_id = _require_user_id()
        client = _storage_client()
        prefix = StoragePaths.agent_memory_episodic_prefix(user_id, agent_id)
        keys = await client.list_objects(prefix)
        if not keys:
            console.print("[dim]No episodic entries found.[/dim]")
            return
        table = Table(title=f"Episodic Memory — {agent_id}", show_header=True)
        table.add_column("Entry Name", style="cyan")
        table.add_column("Full Path", style="dim")
        for k in keys:
            if k.endswith(".md"):
                table.add_row(k.split("/")[-1], k)
        console.print(table)

    run_async(_run())


@episodic_app.command("show")
def episodic_show(
    entry_name: str = typer.Argument(..., help="Entry filename"),
    agent_id: str = typer.Argument(default="main", help="Agent ID"),
) -> None:
    """Print the content of an episodic memory entry."""

    async def _run() -> None:
        user_id = _require_user_id()
        client = _storage_client()
        path = StoragePaths.agent_memory_episodic_entry(user_id, agent_id, entry_name)
        try:
            raw = await client.read(path)
        except FileNotFoundError:
            err_console.print(f"[red]Not found:[/red] {path}")
            raise SystemExit(1)
        console.print(Panel(Syntax(raw.decode(), "markdown", theme="github-dark"), title=path))

    run_async(_run())


@episodic_app.command("del")
def episodic_del(
    entry_name: str = typer.Argument(..., help="Entry filename to delete"),
    agent_id: str = typer.Argument(default="main", help="Agent ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Delete an episodic memory entry."""

    async def _run() -> None:
        user_id = _require_user_id()
        if not yes:
            typer.confirm(f"Delete episodic entry '{entry_name}'?", abort=True)
        client = _storage_client()
        path = StoragePaths.agent_memory_episodic_entry(user_id, agent_id, entry_name)
        await client.delete(path)
        console.print(f"[green]✓[/green] Deleted [bold]{entry_name}[/bold]")

    run_async(_run())


# ---------------------------------------------------------------------------
# Memory — semantic
# ---------------------------------------------------------------------------


@semantic_app.command("list")
def semantic_list(
    agent_id: str = typer.Argument(default="main", help="Agent ID"),
) -> None:
    """List all semantic memory topics for the agent."""

    async def _run() -> None:
        user_id = _require_user_id()
        client = _storage_client()
        prefix = StoragePaths.agent_memory_semantic_prefix(user_id, agent_id)
        keys = await client.list_objects(prefix)
        if not keys:
            console.print("[dim]No semantic topics found.[/dim]")
            return
        table = Table(title=f"Semantic Memory — {agent_id}", show_header=True)
        table.add_column("Topic", style="cyan")
        table.add_column("Full Path", style="dim")
        for k in keys:
            if k.endswith(".md"):
                topic = k.split("/")[-1].removesuffix(".md")
                table.add_row(topic, k)
        console.print(table)

    run_async(_run())


@semantic_app.command("show")
def semantic_show(
    topic: str = typer.Argument(..., help="Topic name"),
    agent_id: str = typer.Argument(default="main", help="Agent ID"),
) -> None:
    """Print the content of a semantic memory topic."""

    async def _run() -> None:
        user_id = _require_user_id()
        client = _storage_client()
        path = StoragePaths.agent_memory_semantic_topic(user_id, agent_id, topic)
        try:
            raw = await client.read(path)
        except FileNotFoundError:
            err_console.print(f"[red]Not found:[/red] topic '{topic}'")
            raise SystemExit(1)
        console.print(Panel(Syntax(raw.decode(), "markdown", theme="github-dark"), title=path))

    run_async(_run())


@semantic_app.command("del")
def semantic_del(
    topic: str = typer.Argument(..., help="Topic name to delete"),
    agent_id: str = typer.Argument(default="main", help="Agent ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Delete a semantic memory topic."""

    async def _run() -> None:
        user_id = _require_user_id()
        if not yes:
            typer.confirm(f"Delete semantic topic '{topic}'?", abort=True)
        client = _storage_client()
        path = StoragePaths.agent_memory_semantic_topic(user_id, agent_id, topic)
        await client.delete(path)
        console.print(f"[green]✓[/green] Deleted semantic topic [bold]{topic}[/bold]")

    run_async(_run())


# ---------------------------------------------------------------------------
# Skill authoring commands
# ---------------------------------------------------------------------------


@skill_app.command("list")
def skill_list() -> None:
    """List all user-authored skills."""

    async def _run() -> None:
        user_id = _require_user_id()
        client = _storage_client()
        prefix = StoragePaths.skill_authored_prefix(user_id)
        keys = await client.list_objects(prefix)
        if not keys:
            console.print("[dim]No authored skills found.[/dim]")
            return
        table = Table(title="Authored Skills", show_header=True)
        table.add_column("Skill ID", style="cyan")
        table.add_column("Path", style="dim")
        for k in keys:
            if k.endswith("/SKILL.md"):
                parts = k.split("/")
                skill_id = parts[-2] if len(parts) >= 2 else k
                table.add_row(skill_id, k)
        console.print(table)

    run_async(_run())


@skill_app.command("show")
def skill_show(
    skill_id: str = typer.Argument(..., help="Skill ID"),
) -> None:
    """Print the SKILL.md content of an authored skill."""

    async def _run() -> None:
        user_id = _require_user_id()
        client = _storage_client()
        path = StoragePaths.skill_authored(user_id, skill_id)
        try:
            raw = await client.read(path)
        except FileNotFoundError:
            err_console.print(f"[red]Not found:[/red] authored skill '{skill_id}'")
            raise SystemExit(1)
        console.print(Panel(Syntax(raw.decode(), "markdown", theme="github-dark"), title=path))

    run_async(_run())


@skill_app.command("create")
def skill_create(
    file: Path = typer.Argument(..., help="Path to SKILL.md file"),
    skill_id: str | None = typer.Option(None, "--id", help="Override skill ID"),
) -> None:
    """Upload a SKILL.md file as a new authored skill."""

    async def _run() -> None:
        user_id = _require_user_id()
        if not file.exists():
            err_console.print(f"[red]ERROR:[/red] File not found: {file}")
            raise SystemExit(1)

        content = file.read_bytes()

        from graphclaw.skills.parser import SkillParser

        parser = SkillParser()
        try:
            defn = parser.parse(content.decode())
        except Exception as exc:
            err_console.print(f"[red]Validation failed:[/red] {exc}")
            raise SystemExit(1)

        sid = skill_id or defn.name
        client = _storage_client()
        path = StoragePaths.skill_authored(user_id, sid)

        if await client.exists(path):
            err_console.print(
                f"[red]ERROR:[/red] Skill '{sid}' already exists. Use 'update' to overwrite."
            )
            raise SystemExit(1)

        await client.write(path, content, content_type="text/markdown")
        console.print(
            f"[green]✓[/green] Authored skill [bold]{sid}[/bold] created at [dim]{path}[/dim]"
        )

    run_async(_run())


@skill_app.command("update")
def skill_update(
    skill_id: str = typer.Argument(..., help="Skill ID to overwrite"),
    file: Path = typer.Argument(..., help="Path to updated SKILL.md file"),
) -> None:
    """Overwrite an existing authored skill with a new SKILL.md file."""

    async def _run() -> None:
        user_id = _require_user_id()
        if not file.exists():
            err_console.print(f"[red]ERROR:[/red] File not found: {file}")
            raise SystemExit(1)
        client = _storage_client()
        path = StoragePaths.skill_authored(user_id, skill_id)
        await client.write(path, file.read_bytes(), content_type="text/markdown")
        console.print(f"[green]✓[/green] Authored skill [bold]{skill_id}[/bold] updated")

    run_async(_run())


@skill_app.command("delete")
def skill_delete(
    skill_id: str = typer.Argument(..., help="Skill ID to delete"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Delete a user-authored skill."""

    async def _run() -> None:
        user_id = _require_user_id()
        if not yes:
            typer.confirm(f"Delete authored skill '{skill_id}'?", abort=True)
        client = _storage_client()
        path = StoragePaths.skill_authored(user_id, skill_id)
        await client.delete(path)
        console.print(f"[green]✓[/green] Deleted authored skill [bold]{skill_id}[/bold]")

    run_async(_run())


@skill_app.command("validate")
def skill_validate(
    file: Path = typer.Argument(..., help="Path to SKILL.md file to validate"),
) -> None:
    """Validate a SKILL.md file without uploading it."""
    if not file.exists():
        err_console.print(f"[red]ERROR:[/red] File not found: {file}")
        raise SystemExit(1)

    from graphclaw.skills.parser import SkillParser

    content = file.read_text()
    parser = SkillParser()
    try:
        defn = parser.parse(content)
        console.print("[green]✓ Valid SKILL.md[/green]")
        table = Table(title="Parsed Fields", show_header=True)
        table.add_column("Field", style="cyan")
        table.add_column("Value")
        for field, val in [
            ("name", defn.name),
            ("description", defn.description),
            ("version", defn.version),
            ("model", defn.model),
            ("tags", ", ".join(defn.tags)),
            ("timeout_seconds", str(defn.timeout_seconds)),
            ("max_tokens", str(defn.max_tokens)),
        ]:
            table.add_row(field, val)
        console.print(table)
    except Exception as exc:
        err_console.print(f"[red]✗ Invalid SKILL.md:[/red] {exc}")
        raise SystemExit(1)


@skill_app.command("paths")
def skill_paths(
    agent_id: str = typer.Option("main", "--agent", "-a", help="Agent ID for memory paths"),
    skill_id: str = typer.Option("example-skill", "--skill", "-s", help="Skill ID for skill paths"),
) -> None:
    """Print all StoragePaths for the current user (debugging aid)."""
    user_id = _require_user_id()
    table = Table(title=f"Storage Paths — user_id={user_id}", show_header=True)
    table.add_column("Purpose", style="cyan")
    table.add_column("Path")
    rows = [
        ("user_config", StoragePaths.user_config(user_id)),
        ("user_scoring_weights", StoragePaths.user_scoring_weights(user_id)),
        (f"agent_profile ({agent_id})", StoragePaths.agent_profile(user_id, agent_id)),
        (f"agent_config ({agent_id})", StoragePaths.agent_config(user_id, agent_id)),
        (f"memory_working ({agent_id})", StoragePaths.agent_memory_working(user_id, agent_id)),
        (
            f"memory_episodic prefix ({agent_id})",
            StoragePaths.agent_memory_episodic_prefix(user_id, agent_id),
        ),
        (
            f"memory_semantic prefix ({agent_id})",
            StoragePaths.agent_memory_semantic_prefix(user_id, agent_id),
        ),
        ("skill_registry_sources", StoragePaths.skill_registry_sources(user_id)),
        ("skill_registry_installed", StoragePaths.skill_registry_installed(user_id)),
        (f"skill_authored ({skill_id})", StoragePaths.skill_authored(user_id, skill_id)),
        ("skill_authored prefix", StoragePaths.skill_authored_prefix(user_id)),
        (f"skill_executions ({skill_id})", StoragePaths.skill_executions(user_id, skill_id)),
        ("system_skill (example)", StoragePaths.system_skill_definition("meeting-notes-agent")),
    ]
    for purpose, path in rows:
        table.add_row(purpose, path)
    console.print(table)

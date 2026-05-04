#!/usr/bin/env python3
"""
Walk graphclaw/tests/ tree, parse file headers, and emit inventory.md at each test root.

Legacy files (no canonical header) produce a TODO row — they do not block regen.

Usage:
    python scripts/regen_inventory.py [--dry-run]

Exit codes:
    0  inventory written (or --dry-run completed)
    1  unexpected error
"""
import argparse
import re
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_ROOT = REPO_ROOT / "tests"

# GC test ID: GC-<layer>-<domain>-W<wave>-<seq>
ID_RE = re.compile(r"GC-[ULKIEASLA]-[A-Z]{2,5}-W\d+-\d{3}")

# First non-blank line of docstring: "GC-X-DOM-WNN-NNN — title"
HEADER_FIRST_RE = re.compile(
    r"^(GC-[ULKIEASLA]-[A-Z]{2,5}-W\d+-\d{3})\s*[—\-]+\s*(.+)"
)

# Scenario block (single- or multi-line, ends at blank line or next field)
SCENARIO_RE = re.compile(
    r"Scenario:\s*(.+?)(?=\n\s*\n|\nPRD:|\nBuild wave:|\nLayer:|\nOwner:)",
    re.DOTALL,
)


def _parse_docstring(text: str) -> str | None:
    """Return the content of the leading triple-quoted docstring, or None."""
    stripped = text.lstrip()
    for quote in ('"""', "'''"):
        if stripped.startswith(quote):
            end = stripped.find(quote, len(quote))
            if end != -1:
                return stripped[len(quote):end]
    return None


def parse_header(path: Path) -> dict | None:
    """
    Parse a test file's canonical header.
    Returns {"id": ..., "scenario": ...} or None if no canonical header present.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    docstring = _parse_docstring(text)
    if docstring is None:
        return None

    # Find first non-blank line in docstring — must match the GC ID pattern
    for line in docstring.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        m = HEADER_FIRST_RE.match(stripped)
        if m:
            test_id = m.group(1)
            title = m.group(2).strip()
            # Extract scenario (first sentence, capped at 80 chars)
            sm = SCENARIO_RE.search(docstring)
            if sm:
                raw = sm.group(1).replace("\n", " ").strip()
                # Trim to first sentence
                for punct in (".", "?", "!"):
                    idx = raw.find(punct)
                    if idx != -1:
                        raw = raw[: idx + 1]
                        break
                scenario = (raw[:77] + "...") if len(raw) > 80 else raw
            else:
                scenario = (title[:77] + "...") if len(title) > 80 else title
            return {"id": test_id, "scenario": scenario}
        # Non-empty line didn't match — no canonical header
        break

    return None


def collect_entries(root: Path, inventory_dir: Path) -> list[dict]:
    """
    Collect inventory rows for all test_*.py files under root.
    Returns list of {"id", "scenario", "file"} dicts, sorted by ID.
    Files without a canonical header get id="TODO" and a placeholder scenario.
    """
    entries: list[dict] = []
    for path in sorted(root.rglob("test_*.py")):
        if "__pycache__" in path.parts:
            continue
        header = parse_header(path)
        if header:
            entries.append({
                "id": header["id"],
                "scenario": header["scenario"],
                "file": path,
            })
        else:
            # Legacy — placeholder row
            rel = path.relative_to(inventory_dir)
            entries.append({
                "id": "TODO",
                "scenario": f"(no header) {path.stem}",
                "file": path,
            })

    # Sort: canonical IDs first (alphabetically), then TODO rows
    entries.sort(key=lambda e: (e["id"] == "TODO", e["id"], str(e["file"])))
    return entries


def render_inventory(title: str, entries: list[dict], inventory_dir: Path) -> str:
    today = date.today().isoformat()
    lines: list[str] = [
        f"# Test Inventory — {title}",
        "",
        "| ID | Scenario (1 line) | File |",
        "|---|---|---|",
    ]
    for e in entries:
        rel = str(e["file"].relative_to(inventory_dir)).replace("\\", "/")
        lines.append(f"| {e['id']} | {e['scenario']} | [{rel}]({rel}) |")
    lines.append("")
    lines.append(f"_Last regenerated: {today} by `scripts/regen_inventory.py`._")
    lines.append("")
    return "\n".join(lines)


def write_inventory(inv_path: Path, content: str, dry_run: bool) -> bool:
    """Write inventory.md; return True if content changed."""
    if inv_path.exists():
        existing = inv_path.read_text(encoding="utf-8")
        # Ignore last-regenerated date line when comparing
        def strip_date(s: str) -> str:
            return re.sub(r"_Last regenerated: \d{4}-\d{2}-\d{2} by.*", "", s)
        if strip_date(existing) == strip_date(content):
            return False
    if not dry_run:
        inv_path.parent.mkdir(parents=True, exist_ok=True)
        inv_path.write_text(content, encoding="utf-8")
    return True


# ──────────────────────────────────────────────────────────────────────────────
# Named inventory roots: (scan_dir, title)
# ──────────────────────────────────────────────────────────────────────────────

NAMED_ROOTS: list[tuple[Path, str]] = [
    (TESTS_ROOT / "contract", "tests/contract"),
    (TESTS_ROOT / "integration", "tests/integration"),
    (TESTS_ROOT / "agent_evals", "tests/agent_evals"),
    (TESTS_ROOT / "test_cli", "tests/test_cli"),
    (TESTS_ROOT / "load", "tests/load"),
]

# Top-level unit/domain dirs (test_XXX/) that roll up to tests/inventory.md
UNIT_PREFIXES = ("test_",)


def build_rollup(inventory_dir: Path) -> list[dict]:
    """Collect entries for the top-level tests/inventory.md (domain dirs)."""
    entries: list[dict] = []
    named_dirs = {r[0] for r in NAMED_ROOTS}
    for subdir in sorted(TESTS_ROOT.iterdir()):
        if not subdir.is_dir():
            continue
        if subdir in named_dirs:
            continue
        if not any(subdir.name.startswith(p) for p in UNIT_PREFIXES):
            continue
        entries.extend(collect_entries(subdir, inventory_dir))
    # Also include unit/ if present
    unit_dir = TESTS_ROOT / "unit"
    if unit_dir.exists():
        entries.extend(collect_entries(unit_dir, inventory_dir))
    entries.sort(key=lambda e: (e["id"] == "TODO", e["id"], str(e["file"])))
    return entries


def main() -> int:
    parser = argparse.ArgumentParser(description="Regenerate test inventory.md files.")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without writing.")
    args = parser.parse_args()

    changed: list[str] = []
    unchanged: list[str] = []

    for scan_dir, title in NAMED_ROOTS:
        if not scan_dir.exists():
            continue
        inv_path = scan_dir / "inventory.md"
        entries = collect_entries(scan_dir, scan_dir)
        content = render_inventory(title, entries, scan_dir)
        if write_inventory(inv_path, content, args.dry_run):
            action = "would write" if args.dry_run else "wrote"
            changed.append(f"  {action}: {inv_path.relative_to(REPO_ROOT)}")
        else:
            unchanged.append(f"  unchanged: {inv_path.relative_to(REPO_ROOT)}")

    # Top-level rollup
    rollup_path = TESTS_ROOT / "inventory.md"
    rollup_entries = build_rollup(TESTS_ROOT)
    rollup_content = render_inventory("tests (unit + domain)", rollup_entries, TESTS_ROOT)
    if write_inventory(rollup_path, rollup_content, args.dry_run):
        action = "would write" if args.dry_run else "wrote"
        changed.append(f"  {action}: {rollup_path.relative_to(REPO_ROOT)}")
    else:
        unchanged.append(f"  unchanged: {rollup_path.relative_to(REPO_ROOT)}")

    if changed:
        print("Inventory regenerated:")
        for line in changed:
            print(line)
    if unchanged:
        print("No changes:")
        for line in unchanged:
            print(line)

    return 0


if __name__ == "__main__":
    sys.exit(main())

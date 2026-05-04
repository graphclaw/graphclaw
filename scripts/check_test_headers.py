#!/usr/bin/env python3
"""
Lint test file headers in graphclaw/tests/.

Behavior:
  - New/modified files without a canonical header → ERROR (blocks CI on new files)
  - Legacy files without a header → WARNING (does not block CI)
  - Malformed header (ID present but fields missing) → ERROR
  - Missing inventory registration → ERROR

Exit codes:
    0  all checks pass (warnings allowed)
    1  one or more errors found

Usage:
    python scripts/check_test_headers.py [--files file1.py file2.py ...]
    python scripts/check_test_headers.py          # scan all tests/
"""
import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_ROOT = REPO_ROOT / "tests"

# Canonical ID format
ID_RE = re.compile(r"GC-[ULKIEASLA]-[A-Z]{2,5}-W\d+-\d{3}")
HEADER_FIRST_RE = re.compile(
    r"^(GC-[ULKIEASLA]-[A-Z]{2,5}-W\d+-\d{3})\s*[—\-]+\s*(.+)"
)

REQUIRED_FIELDS = [
    "Scenario:",
    "PRD:",
    "Build wave:",
    "Layer:",
    "Owner:",
    "Last reviewed:",
    "Cases covered:",
]

WARNINGS: list[str] = []
ERRORS: list[str] = []


def warn(path: Path, msg: str) -> None:
    rel = path.relative_to(REPO_ROOT)
    WARNINGS.append(f"  WARN  {rel}: {msg}")


def error(path: Path, msg: str) -> None:
    rel = path.relative_to(REPO_ROOT)
    ERRORS.append(f"  ERROR {rel}: {msg}")


def _extract_docstring(text: str) -> str | None:
    stripped = text.lstrip()
    for quote in ('"""', "'''"):
        if stripped.startswith(quote):
            end = stripped.find(quote, len(quote))
            if end != -1:
                return stripped[len(quote):end]
    return None


def check_file(path: Path, is_new: bool = False) -> None:
    """
    Check a single test file.
    is_new=True means this file is newly added/modified in the current PR
    (errors block; False means legacy, warnings only for missing header).
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        error(path, "cannot read file")
        return

    docstring = _extract_docstring(text)

    if docstring is None:
        if is_new:
            error(path, "missing canonical header (file must start with triple-quoted docstring)")
        else:
            warn(path, "legacy file — no canonical header (add one when next touching this file)")
        return

    # Find first non-blank line
    header_id = None
    for line in docstring.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        m = HEADER_FIRST_RE.match(stripped)
        if m:
            header_id = m.group(1)
        else:
            if is_new:
                error(path, f"first non-blank line of docstring does not match GC ID pattern: {stripped!r}")
            else:
                warn(path, "legacy file — docstring present but no canonical GC ID on first line")
        break

    if header_id is None:
        return

    # Validate ID format
    if not ID_RE.fullmatch(header_id):
        error(path, f"malformed test ID: {header_id!r}")

    # Check required fields
    for field in REQUIRED_FIELDS:
        if field not in docstring:
            error(path, f"missing required header field: {field!r}")


def load_all_registered_ids() -> set[str]:
    """Collect all IDs registered in any inventory.md under tests/."""
    ids: set[str] = set()
    for inv in TESTS_ROOT.rglob("inventory.md"):
        for line in inv.read_text(encoding="utf-8").splitlines():
            m = ID_RE.search(line)
            if m:
                ids.add(m.group(0))
    return ids


def check_inventory_registration(path: Path, header_id: str, registered: set[str]) -> None:
    if header_id not in registered:
        error(path, f"test ID {header_id!r} is not registered in any inventory.md — run `python scripts/regen_inventory.py`")


def find_test_files(root: Path) -> list[Path]:
    return sorted(
        p for p in root.rglob("test_*.py")
        if "__pycache__" not in p.parts
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Lint test file headers.")
    parser.add_argument(
        "--files",
        nargs="*",
        help="Check specific files only (treated as new/modified — errors, not warnings).",
    )
    parser.add_argument(
        "--check-inventory",
        action="store_true",
        default=True,
        help="Also verify IDs are registered in inventory.md (default: True).",
    )
    args = parser.parse_args()

    if args.files:
        # Specific files passed (e.g., from pre-commit or CI on changed files)
        paths = [Path(f).resolve() for f in args.files]
        is_new_map = {p: True for p in paths}
    else:
        # Scan everything
        paths = find_test_files(TESTS_ROOT)
        is_new_map = {p: False for p in paths}

    registered: set[str] = set()
    if args.check_inventory:
        registered = load_all_registered_ids()

    for path in paths:
        if not path.exists():
            error(path, "file not found")
            continue
        check_file(path, is_new=is_new_map.get(path, False))

    # Check inventory registration for files with canonical headers
    if args.check_inventory:
        for path in paths:
            if not path.exists():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            docstring = _extract_docstring(text)
            if docstring is None:
                continue
            for line in docstring.splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                m = HEADER_FIRST_RE.match(stripped)
                if m:
                    check_inventory_registration(path, m.group(1), registered)
                break

    # Report
    total = len(WARNINGS) + len(ERRORS)
    if WARNINGS:
        print(f"Warnings ({len(WARNINGS)}):")
        for w in WARNINGS:
            print(w)
    if ERRORS:
        print(f"Errors ({len(ERRORS)}):")
        for e in ERRORS:
            print(e)
    if not WARNINGS and not ERRORS:
        print(f"✓ All {len(paths)} test file(s) checked — no issues.")

    return 1 if ERRORS else 0


if __name__ == "__main__":
    sys.exit(main())

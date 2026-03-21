"""tests.test_skills.test_parser — Unit tests for graphclaw.skills.parser.SkillParser.

Description
-----------
Verifies that SkillParser correctly parses well-formed SKILL.md content,
applies defaults for missing frontmatter keys, raises ValueError on invalid
format, and correctly separates the YAML body (system prompt) from
frontmatter.

Design Patterns
---------------
- Arrange/Act/Assert: Each test sets up content, calls parse(), and asserts
  the resulting SkillDefinition fields.
- Temporary File: test_parse_file writes a real file via tmp_path and parses
  it to verify the file-based entry point.

Dependencies
------------
- pytest: Test runner and tmp_path fixture.
- graphclaw.skills.parser: SkillParser under test.
- graphclaw.skills.models: SkillDefinition (return type assertions).
"""

from __future__ import annotations

import pytest

from graphclaw.skills.models import SkillDefinition
from graphclaw.skills.parser import SkillParser

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_skill_md(frontmatter: str, body: str = "You are helpful.") -> str:
    """Compose a valid SKILL.md string from frontmatter and body."""
    return f"---\n{frontmatter}\n---\n{body}"


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


def test_parse_valid_skill_md() -> None:
    """parse() should return a SkillDefinition with all specified fields."""
    content = _make_skill_md(
        frontmatter=(
            "name: summariser\n"
            "description: Summarises text\n"
            "version: 1.2.3\n"
            "model: gpt-4o\n"
            "max_tokens: 2048\n"
            "temperature: 0.5\n"
            "tools:\n"
            "  - search\n"
            "  - read_file\n"
            "tags:\n"
            "  - nlp\n"
            "timeout_seconds: 120\n"
        ),
        body="You are a summarisation assistant.",
    )

    parser = SkillParser()
    skill = parser.parse(content)

    assert isinstance(skill, SkillDefinition)
    assert skill.name == "summariser"
    assert skill.description == "Summarises text"
    assert skill.version == "1.2.3"
    assert skill.model == "gpt-4o"
    assert skill.max_tokens == 2048
    assert skill.temperature == 0.5
    assert skill.tools == ["search", "read_file"]
    assert skill.tags == ["nlp"]
    assert skill.timeout_seconds == 120
    assert skill.system_prompt == "You are a summarisation assistant."


def test_parse_minimal_frontmatter() -> None:
    """parse() with only 'name' in frontmatter should use all defaults."""
    content = _make_skill_md(frontmatter="name: minimal-skill")

    parser = SkillParser()
    skill = parser.parse(content)

    assert skill.name == "minimal-skill"
    assert skill.description == ""
    assert skill.version == "1.0.0"
    assert skill.model == "claude-sonnet-4-20250514"
    assert skill.max_tokens == 4096
    assert skill.temperature == 0.0
    assert skill.tools == []
    assert skill.tags == []
    assert skill.timeout_seconds == 300


def test_parse_extracts_body_as_system_prompt() -> None:
    """The markdown body (after closing ---) must become system_prompt."""
    body = "You are a code reviewer.\n\nBe strict and detailed."
    content = _make_skill_md(frontmatter="name: reviewer", body=body)

    parser = SkillParser()
    skill = parser.parse(content)

    assert skill.system_prompt == body.strip()


def test_parse_all_frontmatter_fields() -> None:
    """Every frontmatter field must be read without loss."""
    content = _make_skill_md(
        frontmatter=(
            "name: full-skill\n"
            "description: Full featured skill\n"
            "version: 3.0.0\n"
            "model: claude-opus-4\n"
            "max_tokens: 8192\n"
            "temperature: 1.0\n"
            "tools: [tool_a, tool_b, tool_c]\n"
            "tags: [alpha, beta]\n"
            "timeout_seconds: 600\n"
        ),
        body="System prompt here.",
    )

    parser = SkillParser()
    skill = parser.parse(content)

    assert skill.name == "full-skill"
    assert skill.description == "Full featured skill"
    assert skill.version == "3.0.0"
    assert skill.model == "claude-opus-4"
    assert skill.max_tokens == 8192
    assert skill.temperature == 1.0
    assert skill.tools == ["tool_a", "tool_b", "tool_c"]
    assert skill.tags == ["alpha", "beta"]
    assert skill.timeout_seconds == 600
    assert skill.system_prompt == "System prompt here."


# ---------------------------------------------------------------------------
# Error-path tests
# ---------------------------------------------------------------------------


def test_parse_missing_frontmatter_raises() -> None:
    """parse() should raise ValueError when there is no frontmatter block."""
    parser = SkillParser()

    with pytest.raises(ValueError, match="missing YAML frontmatter"):
        parser.parse("Just a body without any frontmatter.")


def test_parse_missing_opening_fence_raises() -> None:
    """parse() should raise ValueError when the opening --- is absent."""
    parser = SkillParser()
    content = "name: skill\n---\nBody text."

    with pytest.raises(ValueError, match="missing YAML frontmatter"):
        parser.parse(content)


def test_parse_invalid_yaml_raises() -> None:
    """parse() should propagate a yaml.YAMLError for malformed YAML."""
    import yaml

    parser = SkillParser()
    # Indentation inconsistency combined with a plain scalar that looks like a
    # mapping key causes PyYAML to raise ScannerError / parser error.
    content = "---\nname: valid\n  bad_indent: [unclosed\n---\nBody."

    with pytest.raises((ValueError, yaml.YAMLError)):
        parser.parse(content)


def test_parse_non_mapping_frontmatter_raises() -> None:
    """parse() should raise ValueError when the frontmatter is not a mapping."""
    parser = SkillParser()
    # A YAML list is valid YAML but not a mapping
    content = "---\n- item1\n- item2\n---\nBody."

    with pytest.raises(ValueError, match="must be a YAML mapping"):
        parser.parse(content)


# ---------------------------------------------------------------------------
# File-based test
# ---------------------------------------------------------------------------


def test_parse_file(tmp_path) -> None:
    """parse_file() should read a SKILL.md from disk and return SkillDefinition."""
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text(
        "---\n"
        "name: file-skill\n"
        "description: From disk\n"
        "version: 0.5.0\n"
        "---\n"
        "You are a file-based assistant.\n",
        encoding="utf-8",
    )

    parser = SkillParser()
    skill = parser.parse_file(str(skill_file))

    assert skill.name == "file-skill"
    assert skill.description == "From disk"
    assert skill.version == "0.5.0"
    assert skill.system_prompt == "You are a file-based assistant."

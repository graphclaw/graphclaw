# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.skills.parser — SKILL.md file parser.

Description
-----------
Provides ``SkillParser``, which reads SKILL.md files that follow the
GraphClaw convention: a YAML frontmatter block delimited by ``---`` fences,
followed by a markdown body that serves as the LLM system prompt.

The parser validates that frontmatter is present and is a YAML mapping, then
constructs a ``SkillDefinition`` with sensible defaults for any keys that are
omitted.

Design Patterns
---------------
- Strategy: ``parse(content)`` accepts a raw string; ``parse_file(path)``
  reads from disk and delegates to ``parse``.  Callers can test the parser
  in isolation without touching the filesystem.
- Fail Fast: Both entry points raise ``ValueError`` immediately when the
  input is malformed so that misconfigured skills are detected at load time
  rather than at execution time.

Public API
----------
- SkillParser: Parses SKILL.md content into SkillDefinition instances.
- SkillParser.parse: Parse a SKILL.md string.
- SkillParser.parse_file: Read a SKILL.md file from disk and parse it.

Dependencies
------------
- re: Regular expressions for frontmatter extraction.
- yaml: YAML parsing of the frontmatter block (PyYAML >= 6.0).
- graphclaw.skills.models: SkillDefinition.

Notes
-----
The YAML frontmatter must be the very first thing in the file, starting at
column 0 with ``---``.  Trailing whitespace after the closing ``---`` is
tolerated.  The body section (everything after the closing fence) is stripped
of leading/trailing whitespace before being stored as ``system_prompt``.
"""

from __future__ import annotations

import re

import yaml

from graphclaw.skills.models import SkillDefinition


class SkillParser:
    """Parses SKILL.md files with YAML frontmatter into SkillDefinition objects.

    Usage::

        parser = SkillParser()
        skill = parser.parse_file("/path/to/SKILL.md")
    """

    # Matches ``---\\n<frontmatter>\\n---\\n<body>`` at the start of the file.
    FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)

    def parse(self, content: str) -> SkillDefinition:
        """Parse a SKILL.md string into a SkillDefinition.

        Args:
            content: Full text content of a SKILL.md file.

        Returns:
            A populated ``SkillDefinition`` instance.

        Raises:
            ValueError: If the YAML frontmatter block is missing or not a
                YAML mapping.
            yaml.YAMLError: If the frontmatter contains invalid YAML syntax.
        """
        match = self.FRONTMATTER_RE.match(content)
        if not match:
            raise ValueError(
                "Invalid SKILL.md format: missing YAML frontmatter. "
                "The file must start with a '---' fence."
            )

        frontmatter_str, body = match.groups()

        frontmatter = yaml.safe_load(frontmatter_str)
        if not isinstance(frontmatter, dict):
            raise ValueError(
                "SKILL.md frontmatter must be a YAML mapping (key: value pairs). "
                f"Got {type(frontmatter).__name__!r} instead."
            )

        return SkillDefinition(
            name=frontmatter.get("name", ""),
            description=frontmatter.get("description", ""),
            version=frontmatter.get("version", "1.0.0"),
            model=frontmatter.get("model", "claude-sonnet-4-20250514"),
            max_tokens=frontmatter.get("max_tokens", 4096),
            temperature=frontmatter.get("temperature", 0.0),
            system_prompt=body.strip(),
            tools=frontmatter.get("tools", []),
            tags=frontmatter.get("tags", []),
            timeout_seconds=frontmatter.get("timeout_seconds", 300),
        )

    def parse_file(self, path: str) -> SkillDefinition:
        """Read and parse a SKILL.md file from disk.

        Args:
            path: Absolute or relative filesystem path to the SKILL.md file.

        Returns:
            A populated ``SkillDefinition`` instance.

        Raises:
            OSError: If the file cannot be read.
            ValueError: If the file content is not a valid SKILL.md.
            yaml.YAMLError: If the frontmatter contains invalid YAML syntax.
        """
        with open(path, encoding="utf-8") as fh:
            return self.parse(fh.read())

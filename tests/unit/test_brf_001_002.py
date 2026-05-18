# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Tests for FR-BRF-001 and FR-BRF-002.

Covers:
- EntityGroup.render_label: alias parenthetical when aliases present (FR-BRF-001 AC1)
- render_entity_grouped: groups tasks by node_id; multiple display names collapsed
- render_entity_grouped: unassigned tasks in sentinel group
- render_entity_grouped: empty task list returns safe output
- find_duplicate_resource_candidates: identical names → similarity 1.0 above threshold
- find_duplicate_resource_candidates: dissimilar names → below threshold, excluded
- find_duplicate_resource_candidates: same node_id skipped (not a duplicate)
- build_duplicate_suspicion_prompt: renders merge prompt lines (FR-BRF-002 AC1)
- _levenshtein: known-answer cases
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# FR-BRF-001 — EntityGroup.render_label
# ---------------------------------------------------------------------------


class TestEntityGroup:
    def test_render_label_no_aliases(self):
        """Single name returns canonical only — no parenthetical."""
        from graphclaw.agent.briefing_renderer import EntityGroup

        g = EntityGroup(node_id="RES-001", canonical_name="Bob")
        assert g.render_label() == "Bob"

    def test_render_label_single_alias(self):
        """One alias produces 'Bob (also: Mr. Smith)'."""
        from graphclaw.agent.briefing_renderer import EntityGroup

        g = EntityGroup(node_id="RES-001", canonical_name="Bob", aliases=["Mr. Smith"])
        assert g.render_label() == "Bob (also: Mr. Smith)"

    def test_render_label_multiple_aliases(self):
        """Multiple aliases joined by ', '."""
        from graphclaw.agent.briefing_renderer import EntityGroup

        g = EntityGroup(node_id="RES-001", canonical_name="Bob", aliases=["Mr. Smith", "B. Jones"])
        assert "also: Mr. Smith, B. Jones" in g.render_label()

    def test_add_alias_no_duplicate(self):
        """add_alias does not record the same alias twice."""
        from graphclaw.agent.briefing_renderer import EntityGroup

        g = EntityGroup(node_id="RES-001", canonical_name="Bob")
        g.add_alias("Bobby")
        g.add_alias("Bobby")
        assert g.aliases.count("Bobby") == 1

    def test_add_alias_canonical_not_recorded(self):
        """add_alias ignores the canonical name itself."""
        from graphclaw.agent.briefing_renderer import EntityGroup

        g = EntityGroup(node_id="RES-001", canonical_name="Bob")
        g.add_alias("Bob")
        assert g.aliases == []

    def test_render_line_format(self):
        """render_line returns '<label> — TSK-A, TSK-B'."""
        from graphclaw.agent.briefing_renderer import EntityGroup

        g = EntityGroup(node_id="RES-001", canonical_name="Alice", task_ids=["TSK-A", "TSK-B"])
        line = g.render_line()
        assert line == "Alice — TSK-A, TSK-B"

    def test_render_line_with_alias(self):
        """FR-BRF-001 AC1: Bob (also: Mr. Smith) — TSK-Y, TSK-Z."""
        from graphclaw.agent.briefing_renderer import EntityGroup

        g = EntityGroup(
            node_id="RES-001",
            canonical_name="Bob",
            aliases=["Mr. Smith"],
            task_ids=["TSK-Y", "TSK-Z"],
        )
        line = g.render_line()
        assert line == "Bob (also: Mr. Smith) — TSK-Y, TSK-Z"


# ---------------------------------------------------------------------------
# FR-BRF-001 — render_entity_grouped
# ---------------------------------------------------------------------------


class TestRenderEntityGrouped:
    def test_empty_tasks(self):
        """Empty task list returns safe fallback output."""
        from graphclaw.agent.briefing_renderer import render_entity_grouped

        out = render_entity_grouped([])
        assert "(no tasks)" in out

    def test_groups_by_node_id(self):
        """Tasks with same node_id appear in one line."""
        from graphclaw.agent.briefing_renderer import render_entity_grouped

        tasks = [
            {"id": "TSK-1", "assigned_to": "RES-001", "assignee_display_name": "Alice"},
            {"id": "TSK-2", "assigned_to": "RES-001", "assignee_display_name": "Alice"},
        ]
        out = render_entity_grouped(tasks)
        assert "TSK-1, TSK-2" in out
        assert out.count("Alice") == 1  # only one line for RES-001

    def test_aliases_collapsed(self):
        """FR-BRF-001 AC1: two display names for same node → parenthetical alias."""
        from graphclaw.agent.briefing_renderer import render_entity_grouped

        tasks = [
            {"id": "TSK-Y", "assigned_to": "RES-001", "assignee_display_name": "Bob"},
            {"id": "TSK-Z", "assigned_to": "RES-001", "assignee_display_name": "Mr. Smith"},
        ]
        out = render_entity_grouped(tasks)
        assert "Bob (also: Mr. Smith)" in out
        assert "TSK-Y" in out
        assert "TSK-Z" in out

    def test_unassigned_tasks_grouped(self):
        """Tasks with no assignee node_id appear under '(unassigned)'."""
        from graphclaw.agent.briefing_renderer import render_entity_grouped

        tasks = [
            {"id": "TSK-U", "assigned_to": None, "assignee_display_name": ""},
        ]
        out = render_entity_grouped(tasks)
        assert "(unassigned)" in out
        assert "TSK-U" in out

    def test_multiple_distinct_assignees(self):
        """Tasks from different assignees produce separate lines."""
        from graphclaw.agent.briefing_renderer import render_entity_grouped

        tasks = [
            {"id": "TSK-A", "assigned_to": "RES-001", "assignee_display_name": "Alice"},
            {"id": "TSK-B", "assigned_to": "RES-002", "assignee_display_name": "Bob"},
        ]
        out = render_entity_grouped(tasks)
        assert "Alice" in out
        assert "Bob" in out
        assert "TSK-A" in out
        assert "TSK-B" in out

    def test_header_present(self):
        """Output starts with the expected section header."""
        from graphclaw.agent.briefing_renderer import render_entity_grouped

        out = render_entity_grouped(
            [{"id": "TSK-1", "assigned_to": "RES-001", "assignee_display_name": "X"}]
        )
        assert "## Tasks by Assignee" in out


# ---------------------------------------------------------------------------
# FR-BRF-002 — find_duplicate_resource_candidates
# ---------------------------------------------------------------------------


class TestFindDuplicateResourceCandidates:
    def test_identical_names_above_threshold(self):
        """Identical names → similarity 1.0, surfaced in results."""
        from graphclaw.agent.briefing import find_duplicate_resource_candidates

        resources = [
            {"id": "RES-001", "display_name": "Alice"},
            {"id": "RES-002", "display_name": "Alice"},
        ]
        pairs = find_duplicate_resource_candidates(resources, threshold=0.75)
        assert len(pairs) == 1
        assert pairs[0][4] == 1.0

    def test_dissimilar_names_excluded(self):
        """Very different names excluded at default threshold."""
        from graphclaw.agent.briefing import find_duplicate_resource_candidates

        resources = [
            {"id": "RES-001", "display_name": "Alice"},
            {"id": "RES-002", "display_name": "Zephyr"},
        ]
        pairs = find_duplicate_resource_candidates(resources, threshold=0.75)
        assert pairs == []

    def test_similar_names_surfaced(self):
        """'Bob' and 'Bob Smith' are similar enough at a low threshold."""
        from graphclaw.agent.briefing import find_duplicate_resource_candidates

        resources = [
            {"id": "RES-001", "display_name": "Robert"},
            {"id": "RES-002", "display_name": "Roberto"},
        ]
        pairs = find_duplicate_resource_candidates(resources, threshold=0.5)
        assert len(pairs) == 1
        assert pairs[0][4] >= 0.5

    def test_same_node_id_skipped(self):
        """Same node_id is not treated as a duplicate of itself."""
        from graphclaw.agent.briefing import find_duplicate_resource_candidates

        resources = [
            {"id": "RES-001", "display_name": "Alice"},
            {"id": "RES-001", "display_name": "Alice Johnson"},
        ]
        pairs = find_duplicate_resource_candidates(resources, threshold=0.5)
        assert pairs == []

    def test_empty_list_returns_empty(self):
        """No resources → no pairs."""
        from graphclaw.agent.briefing import find_duplicate_resource_candidates

        assert find_duplicate_resource_candidates([]) == []

    def test_single_resource_returns_empty(self):
        """Single resource cannot form a pair."""
        from graphclaw.agent.briefing import find_duplicate_resource_candidates

        assert find_duplicate_resource_candidates([{"id": "R1", "display_name": "Bob"}]) == []


# ---------------------------------------------------------------------------
# FR-BRF-002 — build_duplicate_suspicion_prompt
# ---------------------------------------------------------------------------


class TestBuildDuplicateSuspicionPrompt:
    def test_empty_pairs_returns_empty_string(self):
        """No pairs → empty string (no section added to briefing)."""
        from graphclaw.agent.briefing_renderer import build_duplicate_suspicion_prompt

        assert build_duplicate_suspicion_prompt([]) == ""

    def test_prompt_contains_merge_suggestion(self):
        """FR-BRF-002 AC1: two similar resources → merge prompt rendered."""
        from graphclaw.agent.briefing_renderer import build_duplicate_suspicion_prompt

        pairs = [("RES-001", "Bob", "RES-002", "Robert", 0.88)]
        out = build_duplicate_suspicion_prompt(pairs)
        assert "Bob" in out
        assert "Robert" in out
        assert "0.88" in out
        assert "merging" in out.lower()

    def test_prompt_header_present(self):
        """Section header is always included when pairs exist."""
        from graphclaw.agent.briefing_renderer import build_duplicate_suspicion_prompt

        pairs = [("R1", "A", "R2", "B", 0.80)]
        out = build_duplicate_suspicion_prompt(pairs)
        assert "Possible Duplicate" in out


# ---------------------------------------------------------------------------
# _levenshtein known-answer cases
# ---------------------------------------------------------------------------


class TestLevenshtein:
    def test_identical_strings_zero(self):
        from graphclaw.agent.briefing import _levenshtein

        assert _levenshtein("abc", "abc") == 0

    def test_single_insert(self):
        from graphclaw.agent.briefing import _levenshtein

        assert _levenshtein("abc", "abcd") == 1

    def test_single_delete(self):
        from graphclaw.agent.briefing import _levenshtein

        assert _levenshtein("abcd", "abc") == 1

    def test_single_substitution(self):
        from graphclaw.agent.briefing import _levenshtein

        assert _levenshtein("abc", "axc") == 1

    def test_kitten_sitting(self):
        """Classic 'kitten' → 'sitting' = 3."""
        from graphclaw.agent.briefing import _levenshtein

        assert _levenshtein("kitten", "sitting") == 3

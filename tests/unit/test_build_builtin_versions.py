#!/usr/bin/env python3
"""Unit tests for version-indexed builtin database builder."""

import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from build_builtin_versions import (  # noqa: E402
    NodeState,
    _entry_to_intervals,
    _needs_full_rebuild,
    _serialize_builtins,
    _update_states_for_version,
    parse_semver_tag,
    sort_semver_tags,
)


class TestSemverHelpers(unittest.TestCase):
    """Test semver parsing and sorting helpers."""

    def test_parse_semver_tag_valid(self):
        self.assertEqual(parse_semver_tag("v0.3.0"), (0, 3, 0))
        self.assertEqual(parse_semver_tag("v12.34.56"), (12, 34, 56))

    def test_parse_semver_tag_invalid(self):
        self.assertIsNone(parse_semver_tag("0.3.0"))
        self.assertIsNone(parse_semver_tag("v0.3.0-rc1"))
        self.assertIsNone(parse_semver_tag("latest"))

    def test_sort_semver_tags(self):
        tags = ["v0.3.10", "v0.3.2", "v0.3.2", "v0.10.0", "v0.3.1", "foo"]
        self.assertEqual(
            sort_semver_tags(tags),
            ["v0.3.1", "v0.3.2", "v0.3.10", "v0.10.0"],
        )


class TestIntervalConversion(unittest.TestCase):
    """Test conversions between schema fields and presence intervals."""

    def setUp(self):
        self.versions = ["v0.3.0", "v0.3.1", "v0.3.2", "v0.3.3", "v0.3.4"]
        self.version_to_index = {v: i for i, v in enumerate(self.versions)}

    def test_entry_to_intervals_monotonic_removed(self):
        entry = {"introduced_in": "v0.3.1", "removed_in": "v0.3.4", "category": "extras"}
        self.assertEqual(
            _entry_to_intervals("SomeNode", entry, self.version_to_index),
            [(1, 3)],
        )

    def test_entry_to_intervals_present_in_ranges(self):
        entry = {
            "introduced_in": "v0.3.0",
            "present_in": ["v0.3.0-v0.3.1", "v0.3.3+"],
            "category": "api",
        }
        self.assertEqual(
            _entry_to_intervals("SomeNode", entry, self.version_to_index),
            [(0, 1), (3, None)],
        )

    def test_serialize_removed_node(self):
        node_states = {
            "RemovedNode": NodeState(category="extras", intervals=[(1, 3)]),
        }
        builtins = _serialize_builtins(self.versions, node_states)
        self.assertEqual(builtins["RemovedNode"]["introduced_in"], "v0.3.1")
        self.assertEqual(builtins["RemovedNode"]["removed_in"], "v0.3.4")
        self.assertNotIn("present_in", builtins["RemovedNode"])

    def test_serialize_non_monotonic_node(self):
        node_states = {
            "FlakyNode": NodeState(category="api", intervals=[(0, 1), (3, None)]),
        }
        builtins = _serialize_builtins(self.versions, node_states)
        self.assertEqual(builtins["FlakyNode"]["introduced_in"], "v0.3.0")
        self.assertEqual(builtins["FlakyNode"]["present_in"], ["v0.3.0-v0.3.1", "v0.3.3+"])
        self.assertNotIn("removed_in", builtins["FlakyNode"])


class TestIncrementalUpdates(unittest.TestCase):
    """Test incremental state transitions and rebuild detection."""

    def test_update_states_closes_and_reopens_intervals(self):
        node_states = {
            "A": NodeState(category="core", intervals=[(0, None)]),
            "B": NodeState(category="extras", intervals=[(0, 0)]),
        }

        # Version index 1: A disappears, B reappears, C introduced
        _update_states_for_version(
            node_states=node_states,
            node_set={"B", "C"},
            category_map={"B": "extras", "C": "api"},
            current_index=1,
        )

        self.assertEqual(node_states["A"].intervals, [(0, 0)])
        self.assertEqual(node_states["B"].intervals, [(0, 0), (1, None)])
        self.assertEqual(node_states["C"].intervals, [(1, None)])
        self.assertEqual(node_states["C"].category, "api")

    def test_needs_full_rebuild_when_older_missing_tag_appears(self):
        remote_tags = ["v0.3.0", "v0.3.1", "v0.3.2", "v0.3.3"]
        existing_versions = ["v0.3.0", "v0.3.2", "v0.3.3"]
        self.assertTrue(_needs_full_rebuild(remote_tags, existing_versions))

    def test_no_full_rebuild_for_append_only_updates(self):
        remote_tags = ["v0.3.0", "v0.3.1", "v0.3.2"]
        existing_versions = ["v0.3.0", "v0.3.1"]
        self.assertFalse(_needs_full_rebuild(remote_tags, existing_versions))


if __name__ == "__main__":
    unittest.main()

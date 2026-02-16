"""Integration tests for augmentation edge cases and ranking behavior."""

import json
import sys
import tempfile
from pathlib import Path

import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from build_global_mappings import GlobalMappingsBuilder
from augment_mappings import MappingsAugmenter


class TestAugmentationRanking:
    """Test that augmentation properly maintains ranking."""

    def test_manager_node_added_ranks_below_registry_with_higher_stats(
        self,
        temp_cache_file,
        temp_mappings_file,
        temp_manager_file,
        sample_packages,
        sample_node,
        write_cache_helper,
        write_manager_helper
    ):
        """Test Manager package ranks below registry package with better stats."""
        # Registry package with good stats
        nodes = [
            sample_packages(
                "popular-registry",
                "Popular Registry Package",
                downloads=10000,
                github_stars=500,
                versions=[{
                    "version": "1.0.0",
                    "comfy_nodes": [sample_node("SharedNode")]
                }]
            ),
        ]

        write_cache_helper(temp_cache_file, nodes)

        # Build initial mappings
        builder = GlobalMappingsBuilder()
        mappings_data = builder.build_mappings(temp_cache_file)

        with open(temp_mappings_file, 'w') as f:
            json.dump(mappings_data, f, indent=2)

        # Manager extension at same URL (will augment existing package)
        manager_extensions = {
            "https://github.com/author/popular-registry": [
                ["SharedNode", "ExtraNode"],
                {"title_aux": "Popular Registry Package"}
            ],
        }

        write_manager_helper(temp_manager_file, manager_extensions)

        # Augment
        augmenter = MappingsAugmenter(temp_mappings_file, temp_manager_file)
        augmenter.load_data()
        augmenter.augment_mappings()
        augmenter.save_augmented_mappings(temp_mappings_file)

        # Verify
        with open(temp_mappings_file, 'r') as f:
            final_data = json.load(f)

        # SharedNode should still only have 1 entry (registry)
        shared_entries = final_data["mappings"]["SharedNode::_"]
        assert len(shared_entries) == 1
        assert shared_entries[0]["package_id"] == "popular-registry"

        # ExtraNode should be added from Manager
        extra_entries = final_data["mappings"]["ExtraNode::_"]
        assert len(extra_entries) == 1
        assert extra_entries[0]["package_id"] == "popular-registry"
        assert extra_entries[0]["source"] == "manager"

    def test_multiple_manager_packages_for_same_node_rank_correctly(
        self,
        temp_cache_file,
        temp_mappings_file,
        temp_manager_file,
        write_cache_helper,
        write_manager_helper
    ):
        """Test multiple synthetic packages for same node rank by score (all 0)."""
        # Empty registry
        write_cache_helper(temp_cache_file, [])

        builder = GlobalMappingsBuilder()
        mappings_data = builder.build_mappings(temp_cache_file)

        with open(temp_mappings_file, 'w') as f:
            json.dump(mappings_data, f, indent=2)

        # Multiple Manager extensions providing same node
        manager_extensions = {
            "https://github.com/author-a/custom-nodes-a": [
                ["CustomNode"],
                {"title_aux": "Custom Nodes A"}
            ],
            "https://github.com/author-b/custom-nodes-b": [
                ["CustomNode"],
                {"title_aux": "Custom Nodes B"}
            ],
            "https://github.com/author-c/custom-nodes-c": [
                ["CustomNode"],
                {"title_aux": "Custom Nodes C"}
            ],
        }

        write_manager_helper(temp_manager_file, manager_extensions)

        # Augment
        augmenter = MappingsAugmenter(temp_mappings_file, temp_manager_file)
        augmenter.load_data()
        augmenter.augment_mappings()
        augmenter.save_augmented_mappings(temp_mappings_file)

        # Verify
        with open(temp_mappings_file, 'r') as f:
            final_data = json.load(f)

        entries = final_data["mappings"]["CustomNode::_"]
        assert len(entries) == 3

        # All should have ranks 1, 2, 3
        ranks = sorted([e["rank"] for e in entries])
        assert ranks == [1, 2, 3]

        # Schema: score should NOT be in output
        for e in entries:
            assert "score" not in e
            # Schema: mappings should NOT have synthetic field
            assert "synthetic" not in e
            # Schema: Manager mappings SHOULD have source field
            assert e["source"] == "manager"

    def test_registry_and_multiple_manager_packages_mixed_ranking(
        self,
        temp_cache_file,
        temp_mappings_file,
        temp_manager_file,
        sample_packages,
        sample_node,
        write_cache_helper,
        write_manager_helper
    ):
        """Test mixed ranking when registry and manager packages provide same node."""
        # Registry package with moderate stats
        nodes = [
            sample_packages(
                "registry-pkg",
                "Registry Package",
                downloads=1000,
                github_stars=50,
                versions=[{
                    "version": "1.0.0",
                    "comfy_nodes": [sample_node("MixedNode")]
                }]
            ),
        ]

        write_cache_helper(temp_cache_file, nodes)

        builder = GlobalMappingsBuilder()
        mappings_data = builder.build_mappings(temp_cache_file)

        with open(temp_mappings_file, 'w') as f:
            json.dump(mappings_data, f, indent=2)

        # Multiple Manager-only extensions
        manager_extensions = {
            "https://github.com/community/node-pack-1": [
                ["MixedNode"],
                {"title_aux": "Community Pack 1"}
            ],
            "https://github.com/community/node-pack-2": [
                ["MixedNode"],
                {"title_aux": "Community Pack 2"}
            ],
        }

        write_manager_helper(temp_manager_file, manager_extensions)

        # Augment
        augmenter = MappingsAugmenter(temp_mappings_file, temp_manager_file)
        augmenter.load_data()
        augmenter.augment_mappings()
        augmenter.save_augmented_mappings(temp_mappings_file)

        # Verify
        with open(temp_mappings_file, 'r') as f:
            final_data = json.load(f)

        entries = final_data["mappings"]["MixedNode::_"]
        assert len(entries) == 3

        # Registry package should rank first (has stats)
        assert entries[0]["package_id"] == "registry-pkg"
        assert entries[0]["rank"] == 1
        # Schema: Registry mappings should NOT have source field
        assert "source" not in entries[0]

        # Manager packages rank after (no stats)
        assert entries[1]["rank"] == 2
        assert entries[2]["rank"] == 3

        # Schema: score should NOT be in output
        for entry in entries:
            assert "score" not in entry
            # Schema: mappings should NOT have synthetic field
            assert "synthetic" not in entry

        # Manager entries should have source field
        assert entries[1]["source"] == "manager"
        assert entries[2]["source"] == "manager"


class TestAugmentationEdgeCases:
    """Test edge cases in augmentation logic."""

    def test_manager_data_with_no_matching_registry_urls(
        self,
        temp_cache_file,
        temp_mappings_file,
        temp_manager_file,
        sample_packages,
        sample_node,
        write_cache_helper,
        write_manager_helper
    ):
        """Test Manager data with completely different URLs from registry."""
        # Registry packages
        nodes = [
            sample_packages(
                "registry-a",
                "Registry A",
                downloads=1000,
                github_stars=50,
                versions=[{
                    "version": "1.0.0",
                    "comfy_nodes": [sample_node("NodeA")]
                }]
            ),
        ]

        write_cache_helper(temp_cache_file, nodes)

        builder = GlobalMappingsBuilder()
        mappings_data = builder.build_mappings(temp_cache_file)

        with open(temp_mappings_file, 'w') as f:
            json.dump(mappings_data, f, indent=2)

        # Manager data with completely different URLs
        manager_extensions = {
            "https://github.com/different/package-b": [
                ["NodeB"],
                {"title_aux": "Package B"}
            ],
            "https://github.com/different/package-c": [
                ["NodeC"],
                {"title_aux": "Package C"}
            ],
        }

        write_manager_helper(temp_manager_file, manager_extensions)

        # Augment
        augmenter = MappingsAugmenter(temp_mappings_file, temp_manager_file)
        augmenter.load_data()
        augmenter.augment_mappings()
        augmenter.save_augmented_mappings(temp_mappings_file)

        # Verify
        with open(temp_mappings_file, 'r') as f:
            final_data = json.load(f)

        # All 3 nodes should exist
        assert "NodeA::_" in final_data["mappings"]
        assert "NodeB::_" in final_data["mappings"]
        assert "NodeC::_" in final_data["mappings"]

        # NodeA from registry
        assert len(final_data["mappings"]["NodeA::_"]) == 1
        assert final_data["mappings"]["NodeA::_"][0]["package_id"] == "registry-a"

        # NodeB and NodeC from synthetic packages
        assert len(final_data["mappings"]["NodeB::_"]) == 1
        assert len(final_data["mappings"]["NodeC::_"]) == 1

        # Verify synthetic packages created with manager_ prefix
        assert "manager_different_package_b" in final_data["packages"]
        assert "manager_different_package_c" in final_data["packages"]

    def test_manager_empty_node_list(
        self,
        temp_cache_file,
        temp_mappings_file,
        temp_manager_file,
        write_cache_helper,
        write_manager_helper
    ):
        """Test Manager extension with empty node list."""
        write_cache_helper(temp_cache_file, [])

        builder = GlobalMappingsBuilder()
        mappings_data = builder.build_mappings(temp_cache_file)

        with open(temp_mappings_file, 'w') as f:
            json.dump(mappings_data, f, indent=2)

        # Manager with empty node lists
        manager_extensions = {
            "https://github.com/empty/extension-1": [
                [],  # Empty node list
                {"title_aux": "Empty Extension"}
            ],
            "https://github.com/valid/extension-2": [
                ["ValidNode"],
                {"title_aux": "Valid Extension"}
            ],
        }

        write_manager_helper(temp_manager_file, manager_extensions)

        # Augment
        augmenter = MappingsAugmenter(temp_mappings_file, temp_manager_file)
        augmenter.load_data()
        augmenter.augment_mappings()
        augmenter.save_augmented_mappings(temp_mappings_file)

        # Verify
        with open(temp_mappings_file, 'r') as f:
            final_data = json.load(f)

        # Only valid node should exist
        assert "ValidNode::_" in final_data["mappings"]
        assert len(final_data["mappings"]) == 1

        # Both synthetic packages created (even empty one) with manager_ prefix
        # This is acceptable - package exists but provides no nodes
        assert "manager_valid_extension_2" in final_data["packages"]
        assert "manager_empty_extension_1" in final_data["packages"]

        # But empty package has no mappings
        empty_pkg_nodes = [
            k for k, entries in final_data["mappings"].items()
            if any(e["package_id"] == "manager_empty_extension_1" for e in entries)
        ]
        assert len(empty_pkg_nodes) == 0

    def test_manager_same_url_as_registry_augments_existing_package(
        self,
        temp_cache_file,
        temp_mappings_file,
        temp_manager_file,
        sample_packages,
        sample_node,
        write_cache_helper,
        write_manager_helper
    ):
        """Test that Manager data with same URL augments existing registry package."""
        # Registry package
        nodes = [
            sample_packages(
                "existing-pkg",
                "Existing Package",
                downloads=5000,
                github_stars=200,
                versions=[{
                    "version": "1.0.0",
                    "comfy_nodes": [sample_node("NodeA")]
                }]
            ),
        ]

        write_cache_helper(temp_cache_file, nodes)

        builder = GlobalMappingsBuilder()
        mappings_data = builder.build_mappings(temp_cache_file)

        with open(temp_mappings_file, 'w') as f:
            json.dump(mappings_data, f, indent=2)

        # Manager with same URL
        manager_extensions = {
            "https://github.com/author/existing-pkg": [
                ["NodeA", "NodeB", "NodeC"],  # NodeA exists, B and C are new
                {"title_aux": "Existing Package"}
            ],
        }

        write_manager_helper(temp_manager_file, manager_extensions)

        # Augment
        augmenter = MappingsAugmenter(temp_mappings_file, temp_manager_file)
        augmenter.load_data()
        initial_package_count = len(augmenter.mappings_data['packages'])

        augmenter.augment_mappings()
        augmenter.save_augmented_mappings(temp_mappings_file)

        # Verify
        with open(temp_mappings_file, 'r') as f:
            final_data = json.load(f)

        # No new packages created (same URL)
        assert len(final_data["packages"]) == initial_package_count

        # NodeA should still have 1 entry (not duplicated)
        assert len(final_data["mappings"]["NodeA::_"]) == 1
        assert final_data["mappings"]["NodeA::_"][0]["package_id"] == "existing-pkg"

        # NodeB and NodeC should be added to existing package
        assert len(final_data["mappings"]["NodeB::_"]) == 1
        assert final_data["mappings"]["NodeB::_"][0]["package_id"] == "existing-pkg"
        assert final_data["mappings"]["NodeB::_"][0]["source"] == "manager"

        assert len(final_data["mappings"]["NodeC::_"]) == 1
        assert final_data["mappings"]["NodeC::_"][0]["package_id"] == "existing-pkg"
        assert final_data["mappings"]["NodeC::_"][0]["source"] == "manager"

    def test_stats_tracking_accurate(
        self,
        temp_cache_file,
        temp_mappings_file,
        temp_manager_file,
        sample_packages,
        sample_node,
        write_cache_helper,
        write_manager_helper
    ):
        """Test that augmentation stats are tracked accurately."""
        # Registry with 2 packages
        nodes = [
            sample_packages(
                "pkg-a",
                "Package A",
                downloads=1000,
                github_stars=50,
                versions=[{
                    "version": "1.0.0",
                    "comfy_nodes": [sample_node("Node1")]
                }]
            ),
            sample_packages(
                "pkg-b",
                "Package B",
                downloads=2000,
                github_stars=100,
                versions=[{
                    "version": "1.0.0",
                    "comfy_nodes": [sample_node("Node2")]
                }]
            ),
        ]

        write_cache_helper(temp_cache_file, nodes)

        builder = GlobalMappingsBuilder()
        mappings_data = builder.build_mappings(temp_cache_file)

        with open(temp_mappings_file, 'w') as f:
            json.dump(mappings_data, f, indent=2)

        # Manager data: augment pkg-a, create 2 synthetic
        manager_extensions = {
            "https://github.com/author/pkg-a": [
                ["Node1", "Node3"],  # Node1 exists, Node3 new
                {"title_aux": "Package A"}
            ],
            "https://github.com/new/synthetic-1": [
                ["Node4", "Node5"],
                {"title_aux": "Synthetic 1"}
            ],
            "https://github.com/new/synthetic-2": [
                ["Node6"],
                {"title_aux": "Synthetic 2"}
            ],
        }

        write_manager_helper(temp_manager_file, manager_extensions)

        # Augment
        augmenter = MappingsAugmenter(temp_mappings_file, temp_manager_file)
        augmenter.load_data()
        augmenter.augment_mappings()
        augmenter.save_augmented_mappings(temp_mappings_file)

        # Verify stats
        assert augmenter.stats['nodes_added'] == 4  # Node3, Node4, Node5, Node6
        assert augmenter.stats['nodes_skipped_exists'] == 1  # Node1
        assert len(augmenter.stats['packages_augmented']) == 1  # pkg-a
        assert len(augmenter.stats['synthetic_packages_created']) == 2  # synthetic-1, synthetic-2
        assert augmenter.stats['total_manager_nodes'] == 5  # All nodes from Manager


class TestAliasAndOverrideBehavior:
    """Test alias canonicalization and explicit mapping overrides."""

    @staticmethod
    def _write_alias_file(aliases):
        temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='_aliases.json', delete=False)
        temp_path = Path(temp_file.name)
        temp_file.close()

        with open(temp_path, 'w') as f:
            json.dump({"schema_version": 1, "aliases": aliases}, f, indent=2)

        return temp_path

    @staticmethod
    def _write_override_file(overrides):
        temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='_overrides.json', delete=False)
        temp_path = Path(temp_file.name)
        temp_file.close()

        with open(temp_path, 'w') as f:
            json.dump({"schema_version": 1, "overrides": overrides}, f, indent=2)

        return temp_path

    def test_aliases_canonicalize_packages_and_mapping_entries(
        self,
        temp_cache_file,
        temp_mappings_file,
        temp_manager_file,
        sample_packages,
        sample_node,
        write_cache_helper,
        write_manager_helper,
    ):
        """Legacy package IDs are rewritten to canonical IDs with merged entries."""
        nodes = [
            sample_packages(
                "comfyui_ryanonyheinside",
                "Ryan Canonical",
                downloads=100,
                github_stars=10,
                versions=[{"version": "2.2.0", "comfy_nodes": [sample_node("AudioInfo")]}],
            ),
            sample_packages(
                "comfyui_ryanontheinside",
                "Ryan Legacy",
                downloads=50,
                github_stars=5,
                versions=[{"version": "2.0.2", "comfy_nodes": [sample_node("AudioInfo")]}],
            ),
        ]

        write_cache_helper(temp_cache_file, nodes)
        builder = GlobalMappingsBuilder()
        mappings_data = builder.build_mappings(temp_cache_file)
        with open(temp_mappings_file, 'w') as f:
            json.dump(mappings_data, f, indent=2)

        write_manager_helper(temp_manager_file, {})
        alias_file = self._write_alias_file(
            {"comfyui_ryanontheinside": "comfyui_ryanonyheinside"}
        )

        try:
            augmenter = MappingsAugmenter(
                temp_mappings_file,
                temp_manager_file,
                alias_file=alias_file,
            )
            augmenter.load_data()
            augmenter.augment_mappings()
            augmenter.save_augmented_mappings(temp_mappings_file)
        finally:
            alias_file.unlink(missing_ok=True)

        with open(temp_mappings_file, 'r') as f:
            final_data = json.load(f)

        assert final_data["package_aliases"] == {
            "comfyui_ryanontheinside": "comfyui_ryanonyheinside"
        }
        assert "comfyui_ryanontheinside" not in final_data["packages"]
        assert "comfyui_ryanonyheinside" in final_data["packages"]

        entries = final_data["mappings"]["AudioInfo::_"]
        assert len(entries) == 1
        assert entries[0]["package_id"] == "comfyui_ryanonyheinside"
        assert "2.2.0" in entries[0]["versions"]
        assert "2.0.2" in entries[0]["versions"]

    def test_override_forces_preferred_package_to_rank_one(
        self,
        temp_cache_file,
        temp_mappings_file,
        temp_manager_file,
        sample_packages,
        sample_node,
        write_cache_helper,
        write_manager_helper,
    ):
        """Overrides place configured package first even when ranking would differ."""
        nodes = [
            sample_packages(
                "audio-general-ComfyUI",
                "Audio General",
                downloads=1000,
                github_stars=100,
                versions=[{"version": "1.0.0", "comfy_nodes": [sample_node("AudioInfo")]}],
            ),
            sample_packages(
                "comfyui_ryanonyheinside",
                "Ryan Canonical",
                downloads=100,
                github_stars=10,
                versions=[{"version": "2.2.0", "comfy_nodes": [sample_node("AudioInfo")]}],
            ),
        ]

        write_cache_helper(temp_cache_file, nodes)
        builder = GlobalMappingsBuilder()
        mappings_data = builder.build_mappings(temp_cache_file)
        with open(temp_mappings_file, 'w') as f:
            json.dump(mappings_data, f, indent=2)

        write_manager_helper(temp_manager_file, {})
        override_file = self._write_override_file(
            [
                {
                    "node_type": "AudioInfo",
                    "input_signature": "_",
                    "package_id": "comfyui_ryanonyheinside",
                }
            ]
        )

        try:
            augmenter = MappingsAugmenter(
                temp_mappings_file,
                temp_manager_file,
                override_file=override_file,
            )
            augmenter.load_data()
            augmenter.augment_mappings()
            augmenter.save_augmented_mappings(temp_mappings_file)
        finally:
            override_file.unlink(missing_ok=True)

        with open(temp_mappings_file, 'r') as f:
            final_data = json.load(f)

        entries = final_data["mappings"]["AudioInfo::_"]
        assert len(entries) == 2
        assert entries[0]["package_id"] == "comfyui_ryanonyheinside"
        assert entries[0]["rank"] == 1
        assert entries[0]["source"] == "override"
        assert entries[1]["package_id"] == "audio-general-ComfyUI"
        assert entries[1]["rank"] == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

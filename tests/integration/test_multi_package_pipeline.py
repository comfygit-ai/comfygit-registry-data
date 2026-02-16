"""Integration tests for multi-package mapping pipeline.

Tests the full flow from cache building to augmentation with Manager data.
"""

import json
import sys
from pathlib import Path

import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from build_global_mappings import GlobalMappingsBuilder
from augment_mappings import MappingsAugmenter


class TestMultiPackageRanking:
    """Test multi-package ranking and scoring."""

    def test_three_packages_same_node_ranked_correctly(
        self, temp_cache_file, sample_packages, sample_node, write_cache_helper
    ):
        """Test that multiple packages for same node are ranked by popularity."""
        # Create packages with different popularity levels
        nodes = [
            sample_packages(
                "popular-math",
                "Popular Math Package",
                downloads=10000,
                github_stars=500,
                versions=[{
                    "version": "2.0.0",
                    "comfy_nodes": [sample_node("IntToFloat")]
                }]
            ),
            sample_packages(
                "community-math",
                "Community Math",
                downloads=500,
                github_stars=50,
                versions=[{
                    "version": "1.5.0",
                    "comfy_nodes": [sample_node("IntToFloat")]
                }]
            ),
            sample_packages(
                "experimental-nodes",
                "Experimental Nodes",
                downloads=10,
                github_stars=2,
                versions=[{
                    "version": "0.1.0",
                    "comfy_nodes": [sample_node("IntToFloat")]
                }]
            ),
        ]

        write_cache_helper(temp_cache_file, nodes)

        # Build mappings
        builder = GlobalMappingsBuilder()
        result = builder.build_mappings(temp_cache_file)

        # Verify structure
        assert "IntToFloat::_" in result["mappings"]
        entries = result["mappings"]["IntToFloat::_"]
        assert isinstance(entries, list)
        assert len(entries) == 3

        # Verify ranking order (most popular first)
        assert entries[0]["package_id"] == "popular-math"
        assert entries[0]["rank"] == 1
        assert entries[1]["package_id"] == "community-math"
        assert entries[1]["rank"] == 2
        assert entries[2]["package_id"] == "experimental-nodes"
        assert entries[2]["rank"] == 3

        # Schema: score should NOT be in output
        for entry in entries:
            assert "score" not in entry
            # Schema: Registry mappings should NOT have source field
            assert "source" not in entry

    def test_zero_stats_package_gets_minimum_score(
        self, temp_cache_file, sample_packages, sample_node, write_cache_helper
    ):
        """Test that packages with 0 downloads and 0 stars still get ranked."""
        nodes = [
            sample_packages(
                "active-package",
                "Active Package",
                downloads=100,
                github_stars=10,
                versions=[{
                    "version": "1.0.0",
                    "comfy_nodes": [sample_node("TestNode")]
                }]
            ),
            sample_packages(
                "zero-stats-package",
                "Zero Stats Package",
                downloads=0,
                github_stars=0,
                versions=[{
                    "version": "1.0.0",
                    "comfy_nodes": [sample_node("TestNode")]
                }]
            ),
        ]

        write_cache_helper(temp_cache_file, nodes)

        builder = GlobalMappingsBuilder()
        result = builder.build_mappings(temp_cache_file)

        entries = result["mappings"]["TestNode::_"]
        assert len(entries) == 2

        # Active package should rank first
        assert entries[0]["package_id"] == "active-package"
        assert entries[0]["rank"] == 1

        # Zero stats package should rank second
        assert entries[1]["package_id"] == "zero-stats-package"
        assert entries[1]["rank"] == 2

        # Schema: score should NOT be in output
        for entry in entries:
            assert "score" not in entry
            assert "source" not in entry

    def test_tied_scores_stable_ordering(
        self, temp_cache_file, sample_packages, sample_node, write_cache_helper
    ):
        """Test that packages with identical scores maintain stable ordering."""
        nodes = [
            sample_packages(
                "package-a",
                "Package A",
                downloads=100,
                github_stars=10,
                versions=[{
                    "version": "1.0.0",
                    "comfy_nodes": [sample_node("TiedNode")]
                }]
            ),
            sample_packages(
                "package-b",
                "Package B",
                downloads=100,
                github_stars=10,
                versions=[{
                    "version": "1.0.0",
                    "comfy_nodes": [sample_node("TiedNode")]
                }]
            ),
            sample_packages(
                "package-c",
                "Package C",
                downloads=100,
                github_stars=10,
                versions=[{
                    "version": "1.0.0",
                    "comfy_nodes": [sample_node("TiedNode")]
                }]
            ),
        ]

        write_cache_helper(temp_cache_file, nodes)

        builder = GlobalMappingsBuilder()
        result = builder.build_mappings(temp_cache_file)

        entries = result["mappings"]["TiedNode::_"]
        assert len(entries) == 3

        # All should still have ranks (even with tied scores internally)
        assert entries[0]["rank"] == 1
        assert entries[1]["rank"] == 2
        assert entries[2]["rank"] == 3

        # Schema: score should NOT be in output
        for entry in entries:
            assert "score" not in entry
            assert "source" not in entry


class TestDifferentSignatures:
    """Test handling of different input signatures."""

    def test_same_node_name_different_signatures_separate_lists(
        self, temp_cache_file, sample_packages, sample_node, write_cache_helper
    ):
        """Test that same node with different signatures creates separate entries."""
        nodes = [
            sample_packages(
                "basic-loader",
                "Basic Loader",
                downloads=5000,
                github_stars=100,
                versions=[{
                    "version": "1.0.0",
                    "comfy_nodes": [
                        sample_node("LoadImage", json.dumps({"required": {"image": ["IMAGE"]}}))
                    ]
                }]
            ),
            sample_packages(
                "advanced-loader",
                "Advanced Loader",
                downloads=3000,
                github_stars=80,
                versions=[{
                    "version": "1.0.0",
                    "comfy_nodes": [
                        sample_node("LoadImage", json.dumps({"required": {"path": ["STRING"], "image": ["IMAGE"]}}))
                    ]
                }]
            ),
        ]

        write_cache_helper(temp_cache_file, nodes)

        builder = GlobalMappingsBuilder()
        result = builder.build_mappings(temp_cache_file)

        # Should create two different keys
        load_image_keys = [k for k in result["mappings"].keys() if k.startswith("LoadImage::")]
        assert len(load_image_keys) == 2

        # Each should have one package
        for key in load_image_keys:
            entries = result["mappings"][key]
            assert isinstance(entries, list)
            assert len(entries) == 1

    def test_multiple_packages_per_different_signatures(
        self, temp_cache_file, sample_packages, sample_node, write_cache_helper
    ):
        """Test multiple packages can provide same signature while others provide different."""
        nodes = [
            # Two packages with signature A
            sample_packages(
                "pkg1-sig-a",
                "Package 1 Sig A",
                downloads=1000,
                github_stars=50,
                versions=[{
                    "version": "1.0.0",
                    "comfy_nodes": [sample_node("TestNode", json.dumps({"required": {"x": ["INT"]}}))]
                }]
            ),
            sample_packages(
                "pkg2-sig-a",
                "Package 2 Sig A",
                downloads=500,
                github_stars=25,
                versions=[{
                    "version": "1.0.0",
                    "comfy_nodes": [sample_node("TestNode", json.dumps({"required": {"x": ["INT"]}}))]
                }]
            ),
            # One package with signature B
            sample_packages(
                "pkg1-sig-b",
                "Package 1 Sig B",
                downloads=2000,
                github_stars=100,
                versions=[{
                    "version": "1.0.0",
                    "comfy_nodes": [sample_node("TestNode", json.dumps({"required": {"x": ["FLOAT"]}}))]
                }]
            ),
        ]

        write_cache_helper(temp_cache_file, nodes)

        builder = GlobalMappingsBuilder()
        result = builder.build_mappings(temp_cache_file)

        # Should have 2 different signatures
        test_node_keys = [k for k in result["mappings"].keys() if k.startswith("TestNode::")]
        assert len(test_node_keys) == 2

        # Find which key has 2 packages (signature A)
        sig_a_key = None
        sig_b_key = None
        for key in test_node_keys:
            if len(result["mappings"][key]) == 2:
                sig_a_key = key
            elif len(result["mappings"][key]) == 1:
                sig_b_key = key

        assert sig_a_key is not None
        assert sig_b_key is not None

        # Verify signature A has correct packages ranked
        sig_a_entries = result["mappings"][sig_a_key]
        assert sig_a_entries[0]["package_id"] == "pkg1-sig-a"
        assert sig_a_entries[1]["package_id"] == "pkg2-sig-a"


class TestVersionAggregation:
    """Test version handling across packages."""

    def test_package_multiple_versions_same_node(
        self, temp_cache_file, sample_packages, sample_node, write_cache_helper
    ):
        """Test that multiple versions of same package aggregate correctly."""
        nodes = [
            sample_packages(
                "evolving-package",
                "Evolving Package",
                downloads=1000,
                github_stars=50,
                versions=[
                    {
                        "version": "3.0.0",
                        "comfy_nodes": [sample_node("MyNode")]
                    },
                    {
                        "version": "2.5.0",
                        "comfy_nodes": [sample_node("MyNode")]
                    },
                    {
                        "version": "2.0.0",
                        "comfy_nodes": [sample_node("MyNode")]
                    },
                ]
            ),
        ]

        write_cache_helper(temp_cache_file, nodes)

        builder = GlobalMappingsBuilder()
        result = builder.build_mappings(temp_cache_file)

        entries = result["mappings"]["MyNode::_"]
        assert len(entries) == 1
        assert entries[0]["package_id"] == "evolving-package"
        assert set(entries[0]["versions"]) == {"3.0.0", "2.5.0", "2.0.0"}


class TestLargeScale:
    """Test with realistic scale."""

    def test_many_packages_same_node(
        self, temp_cache_file, sample_packages, sample_node, write_cache_helper
    ):
        """Test handling of many packages providing the same node."""
        # Create 20 packages all providing IntToFloat
        nodes = []
        for i in range(20):
            nodes.append(
                sample_packages(
                    f"math-pkg-{i}",
                    f"Math Package {i}",
                    downloads=1000 * (20 - i),  # Decreasing popularity
                    github_stars=50 * (20 - i),
                    versions=[{
                        "version": "1.0.0",
                        "comfy_nodes": [sample_node("IntToFloat")]
                    }]
                )
            )

        write_cache_helper(temp_cache_file, nodes)

        builder = GlobalMappingsBuilder()
        result = builder.build_mappings(temp_cache_file)

        entries = result["mappings"]["IntToFloat::_"]
        assert len(entries) == 20

        # Verify ranking is correct (descending by popularity)
        for i in range(20):
            assert entries[i]["rank"] == i + 1

        # Schema: score should NOT be in output
        for entry in entries:
            assert "score" not in entry
            assert "source" not in entry


class TestFullPipelineWithAugmentation:
    """Test full pipeline including Manager augmentation."""

    def test_registry_plus_manager_augmentation(
        self,
        temp_cache_file,
        temp_mappings_file,
        temp_manager_file,
        sample_packages,
        sample_node,
        write_cache_helper,
        write_manager_helper
    ):
        """Test full pipeline: build mappings, then augment with Manager data."""
        # Step 1: Create registry cache with 2 packages
        nodes = [
            sample_packages(
                "registry-math",
                "Registry Math",
                downloads=5000,
                github_stars=200,
                versions=[{
                    "version": "1.0.0",
                    "comfy_nodes": [sample_node("Add")]
                }]
            ),
            sample_packages(
                "registry-utils",
                "Registry Utils",
                downloads=3000,
                github_stars=100,
                versions=[{
                    "version": "1.0.0",
                    "comfy_nodes": [sample_node("Subtract")]
                }]
            ),
        ]

        write_cache_helper(temp_cache_file, nodes)

        # Step 2: Build initial mappings
        builder = GlobalMappingsBuilder()
        mappings_data = builder.build_mappings(temp_cache_file)

        with open(temp_mappings_file, 'w') as f:
            json.dump(mappings_data, f, indent=2)

        # Step 3: Create Manager data with additional packages
        manager_extensions = {
            "https://github.com/community/math-extended": [
                ["Add", "Multiply"],  # Node list
                {"title": "Community Math Extended", "author": "community"}  # Metadata
            ],
            "https://github.com/author/new-nodes": [
                ["Divide"],
                {"title": "New Nodes", "author": "author"}
            ],
        }

        write_manager_helper(temp_manager_file, manager_extensions)

        # Step 4: Augment mappings
        augmenter = MappingsAugmenter(temp_mappings_file, temp_manager_file)
        augmenter.load_data()
        augmenter.augment_mappings()
        augmenter.save_augmented_mappings(temp_mappings_file)

        # Step 5: Verify augmented results
        with open(temp_mappings_file, 'r') as f:
            final_data = json.load(f)

        # Alias metadata should always be present for downstream canonicalization.
        assert "package_aliases" in final_data
        assert final_data["package_aliases"] == {}

        # Verify Add now has 2 packages (1 from registry, 1 from manager)
        add_entries = final_data["mappings"]["Add::_"]
        assert len(add_entries) == 2

        # Registry package should rank higher (has downloads/stars)
        assert add_entries[0]["package_id"] == "registry-math"
        assert add_entries[0]["rank"] == 1

        # Manager synthetic package should rank lower (no stats)
        # Schema: Manager package IDs should be manager_{user}_{repo}
        assert add_entries[1]["package_id"] == "manager_community_math_extended"
        assert add_entries[1]["rank"] == 2
        # Schema: Manager mappings SHOULD have source field
        assert "source" in add_entries[1]
        assert add_entries[1]["source"] == "manager"
        # Schema: mappings should NOT have synthetic field
        assert "synthetic" not in add_entries[1]
        # Schema: score should NOT be in output
        assert "score" not in add_entries[1]

        # Verify new nodes from Manager
        assert "Multiply::_" in final_data["mappings"]
        assert "Divide::_" in final_data["mappings"]

        # Verify synthetic packages were created with manager_ prefix
        assert "manager_community_math_extended" in final_data["packages"]
        assert "manager_author_new_nodes" in final_data["packages"]

        synthetic_pkg = final_data["packages"]["manager_community_math_extended"]
        # Schema: source field on package (optional, but we use it)
        assert synthetic_pkg["source"] == "manager"
        # Schema: DONT include synthetic field in output (redundant)
        assert "synthetic" not in synthetic_pkg
        # Schema: Manager uses title_aux not title
        assert synthetic_pkg["display_name"] == "Community Math Extended"

    def test_manager_only_nodes_create_synthetic_packages(
        self,
        temp_cache_file,
        temp_mappings_file,
        temp_manager_file,
        write_cache_helper,
        write_manager_helper
    ):
        """Test that Manager-only nodes create synthetic packages correctly."""
        # Empty registry cache
        write_cache_helper(temp_cache_file, [])

        # Build empty mappings
        builder = GlobalMappingsBuilder()
        mappings_data = builder.build_mappings(temp_cache_file)

        with open(temp_mappings_file, 'w') as f:
            json.dump(mappings_data, f, indent=2)

        # Manager data with nodes not in registry
        manager_extensions = {
            "https://github.com/awesome/custom-nodes": [
                ["CustomNode1", "CustomNode2", "CustomNode3"],
                {"title_aux": "Awesome Custom Nodes", "author": "awesome", "description": "Cool nodes"}
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

        assert "package_aliases" in final_data

        # All 3 nodes should exist
        assert "CustomNode1::_" in final_data["mappings"]
        assert "CustomNode2::_" in final_data["mappings"]
        assert "CustomNode3::_" in final_data["mappings"]

        # Synthetic package should exist with manager_ prefix
        assert "manager_awesome_custom_nodes" in final_data["packages"]
        pkg = final_data["packages"]["manager_awesome_custom_nodes"]
        # Schema: DONT include synthetic in output
        assert "synthetic" not in pkg
        assert pkg["source"] == "manager"
        assert pkg["display_name"] == "Awesome Custom Nodes"
        assert pkg["description"] == "Cool nodes"


class TestEdgeCases:
    """Test edge cases and error conditions."""

    def test_empty_cache_produces_empty_mappings(
        self, temp_cache_file, write_cache_helper
    ):
        """Test that empty cache produces empty but valid mappings."""
        write_cache_helper(temp_cache_file, [])

        builder = GlobalMappingsBuilder()
        result = builder.build_mappings(temp_cache_file)

        assert result["mappings"] == {}
        assert result["packages"] == {}
        assert result["stats"]["packages"] == 0
        assert result["stats"]["signatures"] == 0
        assert result["stats"]["total_nodes"] == 0

    def test_package_without_nodes_metadata(
        self, temp_cache_file, sample_packages, write_cache_helper
    ):
        """Test package with versions but no comfy_nodes."""
        nodes = [
            sample_packages(
                "no-metadata-pkg",
                "No Metadata Package",
                downloads=1000,
                github_stars=50,
                versions=[{
                    "version": "1.0.0",
                    # No comfy_nodes
                }]
            ),
        ]

        write_cache_helper(temp_cache_file, nodes)

        builder = GlobalMappingsBuilder()
        result = builder.build_mappings(temp_cache_file)

        # Should create package entry but no mappings
        assert result["mappings"] == {}
        assert "no-metadata-pkg" in result["packages"]

    def test_deprecated_versions_excluded_from_mappings(
        self, temp_cache_file, sample_packages, sample_node, write_cache_helper
    ):
        """Test that deprecated versions don't create mappings."""
        nodes = [
            sample_packages(
                "evolving-pkg",
                "Evolving Package",
                downloads=1000,
                github_stars=50,
                versions=[
                    {
                        "version": "2.0.0",
                        "deprecated": False,
                        "comfy_nodes": [sample_node("NewNode")]
                    },
                    {
                        "version": "1.0.0",
                        "deprecated": True,
                        "comfy_nodes": [sample_node("OldNode")]
                    },
                ]
            ),
        ]

        write_cache_helper(temp_cache_file, nodes)

        builder = GlobalMappingsBuilder()
        result = builder.build_mappings(temp_cache_file)

        # NewNode should exist (not deprecated)
        assert "NewNode::_" in result["mappings"]

        # OldNode should NOT exist (deprecated)
        assert "OldNode::_" not in result["mappings"]

        # Version 1.0.0 should still be in package metadata
        assert "1.0.0" in result["packages"]["evolving-pkg"]["versions"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

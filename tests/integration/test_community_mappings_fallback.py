"""Integration tests for curated community mappings fallback behavior."""

import json
import sys
import tempfile
from pathlib import Path

import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from build_global_mappings import GlobalMappingsBuilder
from augment_mappings import MappingsAugmenter


class TestCommunityMappingsFallback:
    """Test curated community mappings fallback precedence and validation."""

    @staticmethod
    def _repo_community_file() -> Path:
        return Path(__file__).parent.parent.parent / "config" / "community_mappings.json"

    @staticmethod
    def _write_community_file(entries):
        temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='_community.json', delete=False)
        temp_path = Path(temp_file.name)
        temp_file.close()

        with open(temp_path, 'w') as f:
            json.dump({"schema_version": 1, "mappings": entries}, f, indent=2)

        return temp_path

    @staticmethod
    def _write_alias_file(aliases):
        temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='_aliases.json', delete=False)
        temp_path = Path(temp_file.name)
        temp_file.close()

        with open(temp_path, 'w') as f:
            json.dump({"schema_version": 1, "aliases": aliases}, f, indent=2)

        return temp_path

    def test_seeded_set_get_inserted_when_missing(
        self,
        temp_cache_file,
        temp_mappings_file,
        temp_manager_file,
        sample_packages,
        write_cache_helper,
        write_manager_helper,
    ):
        """Seeded SetNode/GetNode are inserted as fallback when unresolved."""
        nodes = [
            sample_packages(
                "comfyui-kjnodes",
                "KJ Nodes",
                versions=[{"version": "1.0.0", "comfy_nodes": []}],
            ),
            sample_packages(
                "rgthree-comfy",
                "rgthree Comfy",
                versions=[{"version": "1.0.0", "comfy_nodes": []}],
            ),
        ]

        write_cache_helper(temp_cache_file, nodes)

        builder = GlobalMappingsBuilder()
        mappings_data = builder.build_mappings(temp_cache_file)
        with open(temp_mappings_file, 'w') as f:
            json.dump(mappings_data, f, indent=2)

        write_manager_helper(temp_manager_file, {})

        augmenter = MappingsAugmenter(
            temp_mappings_file,
            temp_manager_file,
            self._repo_community_file(),
        )
        augmenter.load_data()
        augmenter.augment_mappings()
        augmenter.save_augmented_mappings(temp_mappings_file)

        with open(temp_mappings_file, 'r') as f:
            final_data = json.load(f)

        assert "SetNode::_" in final_data["mappings"]
        assert "GetNode::_" in final_data["mappings"]

        set_entry = final_data["mappings"]["SetNode::_"][0]
        get_entry = final_data["mappings"]["GetNode::_"][0]

        assert set_entry["package_id"] == "comfyui-kjnodes"
        assert set_entry["source"] == "community"
        assert set_entry["rank"] == 1

        assert get_entry["package_id"] == "comfyui-kjnodes"
        assert get_entry["source"] == "community"
        assert get_entry["rank"] == 1

    def test_registry_mapping_not_overwritten_by_community(
        self,
        temp_cache_file,
        temp_mappings_file,
        temp_manager_file,
        sample_packages,
        sample_node,
        write_cache_helper,
        write_manager_helper,
    ):
        """Community fallback never overwrites existing registry-derived mapping keys."""
        nodes = [
            sample_packages(
                "comfyui-kjnodes",
                "KJ Nodes",
                versions=[{"version": "1.0.0", "comfy_nodes": []}],
            ),
            sample_packages(
                "rgthree-comfy",
                "rgthree Comfy",
                versions=[{"version": "1.0.0", "comfy_nodes": []}],
            ),
            sample_packages(
                "registry-setnode",
                "Registry SetNode",
                versions=[{"version": "1.0.0", "comfy_nodes": [sample_node("SetNode")]}],
            ),
        ]

        write_cache_helper(temp_cache_file, nodes)

        builder = GlobalMappingsBuilder()
        mappings_data = builder.build_mappings(temp_cache_file)
        with open(temp_mappings_file, 'w') as f:
            json.dump(mappings_data, f, indent=2)

        write_manager_helper(temp_manager_file, {})

        augmenter = MappingsAugmenter(
            temp_mappings_file,
            temp_manager_file,
            self._repo_community_file(),
        )
        augmenter.load_data()
        augmenter.augment_mappings()
        augmenter.save_augmented_mappings(temp_mappings_file)

        with open(temp_mappings_file, 'r') as f:
            final_data = json.load(f)

        set_entries = final_data["mappings"]["SetNode::_"]
        assert len(set_entries) == 1
        assert set_entries[0]["package_id"] == "registry-setnode"
        assert "source" not in set_entries[0]

        assert final_data["mappings"]["GetNode::_"][0]["source"] == "community"

    def test_manager_mapping_not_overwritten_by_community(
        self,
        temp_cache_file,
        temp_mappings_file,
        temp_manager_file,
        sample_packages,
        write_cache_helper,
        write_manager_helper,
    ):
        """Community fallback never overwrites existing manager-derived mapping keys."""
        nodes = [
            sample_packages(
                "comfyui-kjnodes",
                "KJ Nodes",
                versions=[{"version": "1.0.0", "comfy_nodes": []}],
            ),
            sample_packages(
                "rgthree-comfy",
                "rgthree Comfy",
                versions=[{"version": "1.0.0", "comfy_nodes": []}],
            ),
        ]

        write_cache_helper(temp_cache_file, nodes)

        builder = GlobalMappingsBuilder()
        mappings_data = builder.build_mappings(temp_cache_file)
        with open(temp_mappings_file, 'w') as f:
            json.dump(mappings_data, f, indent=2)

        write_manager_helper(
            temp_manager_file,
            {
                "https://github.com/author/comfyui-kjnodes": [
                    ["SetNode"],
                    {"title_aux": "KJ Nodes"},
                ]
            },
        )

        augmenter = MappingsAugmenter(
            temp_mappings_file,
            temp_manager_file,
            self._repo_community_file(),
        )
        augmenter.load_data()
        augmenter.augment_mappings()
        augmenter.save_augmented_mappings(temp_mappings_file)

        with open(temp_mappings_file, 'r') as f:
            final_data = json.load(f)

        set_entries = final_data["mappings"]["SetNode::_"]
        assert len(set_entries) == 1
        assert set_entries[0]["package_id"] == "comfyui-kjnodes"
        assert set_entries[0]["source"] == "manager"

        get_entries = final_data["mappings"]["GetNode::_"]
        assert len(get_entries) == 1
        assert get_entries[0]["package_id"] == "comfyui-kjnodes"
        assert get_entries[0]["source"] == "community"

    def test_invalid_community_package_reference_raises(
        self,
        temp_cache_file,
        temp_mappings_file,
        temp_manager_file,
        sample_packages,
        write_cache_helper,
        write_manager_helper,
    ):
        """Missing package references in community mappings fail with clear errors."""
        nodes = [
            sample_packages(
                "existing-package",
                "Existing Package",
                versions=[{"version": "1.0.0", "comfy_nodes": []}],
            )
        ]

        write_cache_helper(temp_cache_file, nodes)

        builder = GlobalMappingsBuilder()
        mappings_data = builder.build_mappings(temp_cache_file)
        with open(temp_mappings_file, 'w') as f:
            json.dump(mappings_data, f, indent=2)

        write_manager_helper(temp_manager_file, {})

        community_file = self._write_community_file(
            [
                {
                    "node_type": "SetNode",
                    "input_signature": "_",
                    "package_id": "missing-package",
                    "reason": "test",
                    "source_url": "https://example.com",
                    "added_at": "2026-02-14",
                }
            ]
        )

        try:
            augmenter = MappingsAugmenter(temp_mappings_file, temp_manager_file, community_file)
            augmenter.load_data()
            with pytest.raises(ValueError, match="missing-package"):
                augmenter.augment_mappings()
        finally:
            community_file.unlink(missing_ok=True)

    def test_community_fallback_resolves_aliased_package_id(
        self,
        temp_cache_file,
        temp_mappings_file,
        temp_manager_file,
        sample_packages,
        write_cache_helper,
        write_manager_helper,
    ):
        """Fallback community package references can use legacy IDs when alias exists."""
        nodes = [
            sample_packages(
                "comfyui_ryanonyheinside",
                "Ryan Canonical",
                versions=[{"version": "2.2.0", "comfy_nodes": []}],
            )
        ]

        write_cache_helper(temp_cache_file, nodes)
        builder = GlobalMappingsBuilder()
        mappings_data = builder.build_mappings(temp_cache_file)
        with open(temp_mappings_file, 'w') as f:
            json.dump(mappings_data, f, indent=2)

        write_manager_helper(temp_manager_file, {})
        community_file = self._write_community_file(
            [
                {
                    "node_type": "AudioInfo",
                    "input_signature": "_",
                    "package_id": "comfyui_ryanontheinside",
                    "reason": "Legacy ID test",
                    "source_url": "https://example.com",
                    "added_at": "2026-02-15",
                }
            ]
        )
        alias_file = self._write_alias_file(
            {"comfyui_ryanontheinside": "comfyui_ryanonyheinside"}
        )

        try:
            augmenter = MappingsAugmenter(
                temp_mappings_file,
                temp_manager_file,
                community_file,
                alias_file=alias_file,
            )
            augmenter.load_data()
            augmenter.augment_mappings()
            augmenter.save_augmented_mappings(temp_mappings_file)
        finally:
            community_file.unlink(missing_ok=True)
            alias_file.unlink(missing_ok=True)

        with open(temp_mappings_file, 'r') as f:
            final_data = json.load(f)

        entry = final_data["mappings"]["AudioInfo::_"][0]
        assert entry["package_id"] == "comfyui_ryanonyheinside"
        assert entry["source"] == "community"

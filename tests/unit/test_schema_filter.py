#!/usr/bin/env python3
"""Tests for schema filter functionality."""

import json
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from schema_filter import SchemaFilter


class TestSchemaFilter(unittest.TestCase):
    """Test schema filtering functionality."""

    def setUp(self):
        """Create test data and config."""
        # Minimal schema config (matching what will be in config/output_schema.toml)
        self.minimal_config = """
[packages]
display_name = true
description = true
repository = true
github_stars = true
versions = true
source = true

author = false
downloads = false
rating = false
license = false
category = false
icon = false
tags = false
status = false
created_at = false

[versions]
version = true
download_url = true
deprecated = true
dependencies = true

changelog = false
release_date = false
status = false
supported_accelerators = false
supported_comfyui_version = false
supported_os = false

[mappings]
package_id = true
versions = true
rank = true
source = true
"""

        # Create temp config file
        self.config_file = tempfile.NamedTemporaryFile(mode='w', suffix='.toml', delete=False)
        self.config_file.write(self.minimal_config)
        self.config_file.close()

        # Test data - full package with all fields
        self.full_package = {
            "display_name": "Test Package",
            "author": "Test Author",
            "description": "Test description",
            "repository": "https://github.com/test/repo",
            "downloads": 1000,
            "github_stars": 50,
            "rating": 5,
            "license": "MIT",
            "category": "test",
            "icon": "icon.png",
            "tags": ["tag1", "tag2"],
            "status": "active",
            "created_at": "2025-01-01",
            "source": "registry",
            "versions": {
                "1.0.0": {
                    "version": "1.0.0",
                    "changelog": "Initial release",
                    "release_date": "2025-01-01",
                    "dependencies": ["dep1"],
                    "deprecated": False,
                    "download_url": "https://cdn.example.com/package.zip",
                    "status": "active",
                    "supported_accelerators": ["cuda"],
                    "supported_comfyui_version": "1.0",
                    "supported_os": ["linux"]
                }
            }
        }

        # Full mapping entry
        self.full_mapping = {
            "package_id": "test-package",
            "versions": ["1.0.0"],
            "rank": 1,
            "source": "registry"
        }

    def tearDown(self):
        """Clean up temp files."""
        Path(self.config_file.name).unlink(missing_ok=True)

    def test_filter_package_removes_unused_fields(self):
        """Test that unused package fields are removed."""
        filter = SchemaFilter(Path(self.config_file.name))
        filtered = filter.filter_package(self.full_package)

        # Should keep these fields
        self.assertIn("display_name", filtered)
        self.assertIn("description", filtered)
        self.assertIn("repository", filtered)
        self.assertIn("github_stars", filtered)
        self.assertIn("source", filtered)
        self.assertIn("versions", filtered)

        # Should remove these fields
        self.assertNotIn("author", filtered)
        self.assertNotIn("downloads", filtered)
        self.assertNotIn("rating", filtered)
        self.assertNotIn("license", filtered)
        self.assertNotIn("category", filtered)
        self.assertNotIn("icon", filtered)
        self.assertNotIn("tags", filtered)
        self.assertNotIn("status", filtered)
        self.assertNotIn("created_at", filtered)

    def test_filter_version_removes_unused_fields(self):
        """Test that unused version fields are removed."""
        filter = SchemaFilter(Path(self.config_file.name))
        version = self.full_package["versions"]["1.0.0"]
        filtered = filter.filter_version(version)

        # Should keep these fields
        self.assertIn("version", filtered)
        self.assertIn("download_url", filtered)
        self.assertIn("deprecated", filtered)
        self.assertIn("dependencies", filtered)

        # Should remove these fields
        self.assertNotIn("changelog", filtered)
        self.assertNotIn("release_date", filtered)
        self.assertNotIn("status", filtered)
        self.assertNotIn("supported_accelerators", filtered)
        self.assertNotIn("supported_comfyui_version", filtered)
        self.assertNotIn("supported_os", filtered)

    def test_filter_mapping_keeps_all_fields(self):
        """Test that all mapping fields are kept (all required)."""
        filter = SchemaFilter(Path(self.config_file.name))
        filtered = filter.filter_mapping(self.full_mapping)

        # All fields should be kept
        self.assertEqual(filtered, self.full_mapping)

    def test_filter_package_with_versions_dict(self):
        """Test that nested versions are filtered correctly."""
        filter = SchemaFilter(Path(self.config_file.name))
        filtered = filter.filter_package(self.full_package)

        # Versions dict should still exist
        self.assertIn("versions", filtered)
        self.assertIn("1.0.0", filtered["versions"])

        # Version should be filtered
        version = filtered["versions"]["1.0.0"]
        self.assertIn("version", version)
        self.assertIn("download_url", version)
        self.assertNotIn("changelog", version)

    def test_filter_complete_output(self):
        """Test filtering complete mappings output structure."""
        filter = SchemaFilter(Path(self.config_file.name))

        full_output = {
            "version": "2025.01.01",
            "generated_at": "2025-01-01T00:00:00",
            "stats": {
                "packages": 1,
                "signatures": 1
            },
            "package_aliases": {
                "legacy-package": "canonical-package"
            },
            "mappings": {
                "TestNode::_": [self.full_mapping]
            },
            "packages": {
                "test-package": self.full_package
            }
        }

        filtered = filter.filter_mappings_output(full_output)

        # Top-level structure should be preserved
        self.assertIn("version", filtered)
        self.assertIn("generated_at", filtered)
        self.assertIn("stats", filtered)
        self.assertIn("package_aliases", filtered)
        self.assertIn("mappings", filtered)
        self.assertIn("packages", filtered)
        self.assertEqual(
            filtered["package_aliases"]["legacy-package"],
            "canonical-package"
        )

        # Package should be filtered
        pkg = filtered["packages"]["test-package"]
        self.assertIn("display_name", pkg)
        self.assertNotIn("author", pkg)

        # Version should be filtered
        version = pkg["versions"]["1.0.0"]
        self.assertIn("version", version)
        self.assertNotIn("changelog", version)

    def test_filter_empty_versions_dict(self):
        """Test filtering package with empty versions (synthetic packages)."""
        filter = SchemaFilter(Path(self.config_file.name))

        synthetic_package = {
            "display_name": "Synthetic Package",
            "author": "Author",
            "description": "",
            "repository": "https://github.com/test/synthetic",
            "downloads": 0,
            "github_stars": 0,
            "source": "manager",
            "versions": {}
        }

        filtered = filter.filter_package(synthetic_package)

        # Should keep essential fields
        self.assertIn("display_name", filtered)
        self.assertIn("description", filtered)
        self.assertIn("repository", filtered)
        self.assertIn("source", filtered)
        self.assertIn("versions", filtered)

        # Empty versions should remain empty
        self.assertEqual(filtered["versions"], {})

        # Should remove unused fields
        self.assertNotIn("author", filtered)
        self.assertNotIn("downloads", filtered)

    def test_filter_missing_optional_fields(self):
        """Test filtering when some optional fields are missing."""
        filter = SchemaFilter(Path(self.config_file.name))

        minimal_package = {
            "display_name": "Minimal Package",
            "repository": "https://github.com/test/minimal",
            "github_stars": 10,
            "versions": {}
        }

        filtered = filter.filter_package(minimal_package)

        # Should not crash and should preserve existing fields
        self.assertEqual(filtered["display_name"], "Minimal Package")
        self.assertEqual(filtered["repository"], "https://github.com/test/minimal")
        self.assertEqual(filtered["github_stars"], 10)

    def test_missing_config_returns_unfiltered(self):
        """Test that missing config file returns data unfiltered."""
        filter = SchemaFilter(Path("/nonexistent/config.toml"))

        # Should return data unchanged when config missing
        filtered = filter.filter_package(self.full_package)
        self.assertEqual(filtered, self.full_package)

    def test_multiple_packages_filtered(self):
        """Test filtering multiple packages in packages section."""
        filter = SchemaFilter(Path(self.config_file.name))

        packages = {
            "pkg1": self.full_package.copy(),
            "pkg2": self.full_package.copy()
        }

        filtered = filter.filter_packages_section(packages)

        # Both packages should be filtered
        self.assertEqual(len(filtered), 2)
        for pkg_id, pkg in filtered.items():
            self.assertIn("display_name", pkg)
            self.assertNotIn("author", pkg)

    def test_mapping_with_list_of_entries(self):
        """Test filtering mapping with multiple package entries."""
        filter = SchemaFilter(Path(self.config_file.name))

        mappings = {
            "TestNode::abc123": [
                {"package_id": "pkg1", "versions": ["1.0.0"], "rank": 1},
                {"package_id": "pkg2", "versions": ["2.0.0"], "rank": 2, "source": "manager"}
            ]
        }

        filtered = filter.filter_mappings_section(mappings)

        # Structure should be preserved
        self.assertEqual(len(filtered["TestNode::abc123"]), 2)

        # Each entry should keep all fields (all required for mappings)
        for entry in filtered["TestNode::abc123"]:
            self.assertIn("package_id", entry)
            self.assertIn("versions", entry)
            self.assertIn("rank", entry)


class TestSchemaFilterFileSize(unittest.TestCase):
    """Test that filtering actually reduces file size."""

    def test_filtered_output_is_smaller(self):
        """Test that filtered JSON is significantly smaller than unfiltered."""
        # Create minimal config
        config_content = """
[packages]
display_name = true
description = true
repository = true
github_stars = true
versions = true
source = true

author = false
downloads = false
rating = false
license = false
category = false
icon = false
tags = false
status = false
created_at = false

[versions]
version = true
download_url = true
deprecated = true
dependencies = true

changelog = false
release_date = false
status = false
supported_accelerators = false
supported_comfyui_version = false
supported_os = false

[mappings]
package_id = true
versions = true
rank = true
source = true
"""

        config_file = tempfile.NamedTemporaryFile(mode='w', suffix='.toml', delete=False)
        config_file.write(config_content)
        config_file.close()

        try:
            # Create sample data with many packages
            full_output = {
                "version": "2025.01.01",
                "generated_at": "2025-01-01T00:00:00",
                "stats": {"packages": 100, "signatures": 100},
                "mappings": {},
                "packages": {}
            }

            # Generate 100 test packages with all fields
            for i in range(100):
                pkg_id = f"test-package-{i}"
                full_output["packages"][pkg_id] = {
                    "display_name": f"Test Package {i}",
                    "author": f"Author {i}",
                    "description": f"Description {i}" * 10,  # Make it larger
                    "repository": f"https://github.com/test/repo{i}",
                    "downloads": 1000 * i,
                    "github_stars": 50 * i,
                    "rating": 5,
                    "license": '{"file": "LICENSE"}',
                    "category": "test",
                    "icon": f"icon{i}.png",
                    "tags": ["tag1", "tag2", "tag3"],
                    "status": "NodeStatusActive",
                    "created_at": "2025-01-01T00:00:00Z",
                    "source": "registry",
                    "versions": {
                        "1.0.0": {
                            "version": "1.0.0",
                            "changelog": f"Changelog {i}" * 20,  # Make it larger
                            "release_date": "2025-01-01T00:00:00Z",
                            "dependencies": ["dep1", "dep2"],
                            "deprecated": False,
                            "download_url": f"https://cdn.example.com/pkg{i}.zip",
                            "status": "NodeVersionStatusActive",
                            "supported_accelerators": ["cuda", "rocm"],
                            "supported_comfyui_version": "1.0",
                            "supported_os": ["linux", "windows"]
                        }
                    }
                }
                full_output["mappings"][f"TestNode{i}::_"] = [{
                    "package_id": pkg_id,
                    "versions": ["1.0.0"],
                    "rank": 1
                }]

            # Measure unfiltered size
            unfiltered_json = json.dumps(full_output, indent=2)
            unfiltered_size = len(unfiltered_json)

            # Apply filter
            filter = SchemaFilter(Path(config_file.name))
            filtered_output = filter.filter_mappings_output(full_output)

            # Measure filtered size
            filtered_json = json.dumps(filtered_output, indent=2)
            filtered_size = len(filtered_json)

            # Calculate reduction
            reduction_percent = ((unfiltered_size - filtered_size) / unfiltered_size) * 100

            print(f"\nFile size comparison:")
            print(f"  Unfiltered: {unfiltered_size:,} bytes")
            print(f"  Filtered:   {filtered_size:,} bytes")
            print(f"  Reduction:  {reduction_percent:.1f}%")

            # Assert significant reduction (should be 40-60%)
            self.assertGreater(reduction_percent, 30,
                             "Filtered output should be at least 30% smaller")
            self.assertLess(filtered_size, unfiltered_size,
                           "Filtered output should be smaller than unfiltered")

        finally:
            Path(config_file.name).unlink(missing_ok=True)


if __name__ == '__main__':
    unittest.main()

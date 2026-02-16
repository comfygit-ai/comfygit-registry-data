#!/usr/bin/env python3
"""Augment node mappings with ComfyUI Manager's extension-node-map data.

This script enhances the ComfyUI registry node mappings by filling in missing node data
from ComfyUI Manager's extension-node-map.json file. It both augments existing registry
packages and creates synthetic packages for Manager-only extensions.

BEHAVIOR:
- Augments packages that exist in the registry (matched by GitHub URL)
- Creates synthetic packages for Manager-only extensions (prefixed with "github_")
- Adds node mappings with unknown input signatures (marked with "_")
- Supports multiple packages per node signature
- Preserves existing mappings with real input signatures over Manager's name-only data

HOW IT WORKS:
1. Loads existing node_mappings.json and extension-node-map.json
2. First pass: Augments registry packages with Manager node data
3. Second pass: Creates synthetic packages for Manager-only extensions
4. Re-ranks all mappings based on package scores
"""

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from comfygit_core.utils.input_signature import create_node_key
from build_global_mappings import calculate_package_score
from url_utils import (
    normalize_repository_url,
    is_supported_repo_url,
    generate_manager_package_id
)
from logging import getLogger

logger = getLogger(__name__)


class MappingsAugmenter:
    """Augments node mappings with ComfyUI Manager data."""

    def __init__(
        self,
        mappings_file: Path,
        manager_file: Path,
        community_file: Optional[Path] = None,
        alias_file: Optional[Path] = None,
        override_file: Optional[Path] = None,
    ):
        self.mappings_file = mappings_file
        self.manager_file = manager_file
        self.community_file = community_file
        self.alias_file = alias_file
        self.override_file = override_file
        self.mappings_data = None
        self.manager_data = None
        self.community_data = []
        self.package_aliases = {}
        self.mapping_overrides = []
        self.stats = {
            'nodes_added': 0,
            'nodes_skipped_exists': 0,
            'packages_augmented': set(),
            'packages_not_found': set(),
            'synthetic_packages_created': set(),
            'total_manager_nodes': 0,
            'community_nodes_added': 0,
            'community_nodes_skipped_exists': 0,
            'aliases_applied': 0,
            'mapping_overrides_applied': 0
        }

    def load_data(self):
        """Load both data files."""
        with open(self.mappings_file, 'r') as f:
            self.mappings_data = json.load(f)
        logger.info(f"Loaded {len(self.mappings_data['mappings'])} mappings, {len(self.mappings_data['packages'])} packages")

        with open(self.manager_file, 'r') as f:
            manager_raw = json.load(f)

        if isinstance(manager_raw, dict) and "extensions" in manager_raw:
            self.manager_data = manager_raw["extensions"]
            fetched_at = manager_raw.get("fetched_at", "unknown")
            logger.info(f"Loaded {len(self.manager_data)} extensions from Manager (fetched: {fetched_at})")
        else:
            self.manager_data = manager_raw
            logger.info(f"Loaded {len(self.manager_data)} extensions from Manager (raw format)")

        if self.community_file and self.community_file.exists():
            with open(self.community_file, 'r') as f:
                community_raw = json.load(f)
            self.community_data = community_raw.get("mappings", [])
            if not isinstance(self.community_data, list):
                raise ValueError(f"Invalid community mappings format in {self.community_file}: 'mappings' must be a list")
            logger.info(f"Loaded {len(self.community_data)} curated community mappings from {self.community_file}")
        elif self.community_file:
            logger.warning(f"Community mappings file not found: {self.community_file}")

        if self.alias_file and self.alias_file.exists():
            with open(self.alias_file, 'r') as f:
                alias_raw = json.load(f)

            if isinstance(alias_raw, dict):
                aliases = alias_raw.get("aliases", alias_raw)
            else:
                raise ValueError(f"Invalid alias format in {self.alias_file}: expected object")

            self.package_aliases = self._normalize_aliases(aliases)
            logger.info(f"Loaded {len(self.package_aliases)} package aliases from {self.alias_file}")
        elif self.alias_file:
            logger.warning(f"Package alias file not found: {self.alias_file}")

        if self.override_file and self.override_file.exists():
            with open(self.override_file, 'r') as f:
                override_raw = json.load(f)

            if isinstance(override_raw, dict):
                self.mapping_overrides = override_raw.get("overrides", [])
            elif isinstance(override_raw, list):
                self.mapping_overrides = override_raw
            else:
                raise ValueError(f"Invalid mapping override format in {self.override_file}: expected list or object")

            if not isinstance(self.mapping_overrides, list):
                raise ValueError(f"Invalid mapping override format in {self.override_file}: 'overrides' must be a list")
            logger.info(f"Loaded {len(self.mapping_overrides)} node mapping overrides from {self.override_file}")
        elif self.override_file:
            logger.warning(f"Node mapping override file not found: {self.override_file}")

    @staticmethod
    def _normalize_aliases(raw_aliases: Dict[str, Any]) -> Dict[str, str]:
        """Normalize raw alias map to direct legacy->canonical IDs."""
        if not isinstance(raw_aliases, dict):
            raise ValueError("Invalid alias format: 'aliases' must be an object")

        aliases = {}
        for legacy_id, canonical_id in raw_aliases.items():
            if not isinstance(legacy_id, str) or not isinstance(canonical_id, str):
                continue
            if legacy_id == canonical_id:
                continue
            aliases[legacy_id] = canonical_id

        normalized = {}
        for legacy_id in aliases:
            seen = set()
            current = legacy_id
            while current in aliases:
                if current in seen:
                    logger.warning(f"Skipping cyclic alias mapping for {legacy_id}")
                    current = legacy_id
                    break
                seen.add(current)
                next_id = aliases[current]
                if next_id == current:
                    break
                current = next_id

            if current != legacy_id:
                normalized[legacy_id] = current

        return normalized

    def _canonicalize_package_id(self, package_id: Optional[str]) -> Optional[str]:
        """Return canonical package ID using loaded aliases."""
        if not package_id:
            return package_id
        return self.package_aliases.get(package_id, package_id)

    @staticmethod
    def _merge_package_data(target: Dict[str, Any], source: Dict[str, Any]) -> None:
        """Merge package metadata for alias-collapsed package IDs."""
        target_versions = target.get("versions", {})
        source_versions = source.get("versions", {})
        if isinstance(target_versions, dict) and isinstance(source_versions, dict):
            for version, version_data in source_versions.items():
                target_versions.setdefault(version, version_data)
            target["versions"] = target_versions

        for field, value in source.items():
            if field == "versions":
                continue
            if field in ("downloads", "github_stars", "rating") and isinstance(value, (int, float)):
                target[field] = max(target.get(field, 0), value)
                continue
            if field not in target or target[field] in (None, "", [], {}):
                target[field] = value

    @staticmethod
    def _merge_mapping_entry(target: Dict[str, Any], source: Dict[str, Any]) -> None:
        """Merge duplicate mapping rows after alias canonicalization."""
        target_versions = target.get("versions", []) or []
        source_versions = source.get("versions", []) or []
        target["versions"] = list(dict.fromkeys([*target_versions, *source_versions]))

        if source.get("_temp_score", 0) > target.get("_temp_score", 0):
            target["_temp_score"] = source["_temp_score"]

        # If any merged row is registry-backed (no source), keep it as registry.
        source_tag = source.get("source")
        if not source_tag:
            target.pop("source", None)
        elif "source" not in target:
            target["source"] = source_tag

    def _apply_package_aliases(self):
        """Canonicalize package IDs in packages and mappings using alias map."""
        self.mappings_data["package_aliases"] = dict(sorted(self.package_aliases.items()))
        if not self.package_aliases:
            return

        packages = self.mappings_data["packages"]
        for legacy_id, canonical_id in self.package_aliases.items():
            if legacy_id == canonical_id:
                continue

            legacy_package = packages.pop(legacy_id, None)
            if not legacy_package:
                continue

            if canonical_id not in packages:
                packages[canonical_id] = legacy_package
            else:
                self._merge_package_data(packages[canonical_id], legacy_package)
            self.stats["aliases_applied"] += 1

        for node_key, entries in self.mappings_data["mappings"].items():
            deduped_entries = {}
            for entry in entries:
                canonical_package_id = self._canonicalize_package_id(entry.get("package_id"))
                if not canonical_package_id:
                    continue
                candidate = dict(entry)
                candidate["package_id"] = canonical_package_id

                existing = deduped_entries.get(canonical_package_id)
                if existing is None:
                    deduped_entries[canonical_package_id] = candidate
                    continue
                self._merge_mapping_entry(existing, candidate)

            self.mappings_data["mappings"][node_key] = list(deduped_entries.values())

    def build_url_to_package_map(self) -> Dict[str, str]:
        """Build mapping from repository URLs to package IDs."""
        url_map = {}

        for package_id, package_info in self.mappings_data['packages'].items():
            repo_url = package_info.get('repository', '')
            if repo_url:
                # Repository URLs should already be normalized from build phase
                normalized_url = normalize_repository_url(repo_url)
                url_map[normalized_url] = self._canonicalize_package_id(package_id)

        logger.info(f"Built URL map with {len(url_map)} repositories")
        return url_map

    def create_synthetic_package(self, repo_url: str, extension_data: list):
        """Create a synthetic package entry for a Manager-only extension."""
        normalized_url = normalize_repository_url(repo_url)
        package_id = generate_manager_package_id(normalized_url)

        if package_id in self.mappings_data['packages']:
            return package_id

        metadata = extension_data[1] if len(extension_data) > 1 and isinstance(extension_data[1], dict) else {}

        # Extract display name from metadata or URL
        parsed = urlparse(normalized_url)
        path_parts = parsed.path.strip('/').split('/')
        default_name = path_parts[-1] if path_parts else parsed.netloc

        # Try title_aux first (correct field), fall back to title for compatibility
        display_name = metadata.get('title_aux') or metadata.get('title') or default_name

        self.mappings_data['packages'][package_id] = {
            'display_name': display_name,
            'author': metadata.get('author', path_parts[0] if path_parts else ''),
            'description': metadata.get('description', ''),
            'repository': normalized_url,
            'downloads': 0,
            'github_stars': 0,
            'rating': 0,
            'license': '{}',
            'category': '',
            'icon': '',
            'tags': [],
            'status': 'NodeStatusActive',
            'created_at': datetime.now().isoformat(),
            'source': 'manager',
            'versions': {}
        }

        self.stats['synthetic_packages_created'].add(package_id)
        logger.info(f"Created synthetic package: {package_id}")
        return package_id

    def augment_mappings(self):
        """Augment mappings with Manager data."""
        url_to_package = self.build_url_to_package_map()
        packages_not_found = {}

        # First pass: Process extensions that exist in registry
        for repo_url, extension_data in self.manager_data.items():
            # Skip unsupported repository types
            if not is_supported_repo_url(repo_url):
                continue

            normalized_url = normalize_repository_url(repo_url)
            package_id = url_to_package.get(normalized_url)

            if not package_id:
                packages_not_found[repo_url] = extension_data
                continue

            if not isinstance(extension_data, list) or len(extension_data) < 1:
                continue

            node_list = extension_data[0]
            if not isinstance(node_list, list):
                continue

            self.stats['total_manager_nodes'] += len(node_list)

            nodes_added_for_package = 0
            for node_type in node_list:
                if not isinstance(node_type, str):
                    continue

                node_key = create_node_key(node_type, "_")

                # Check if this package already has an entry for this node
                if node_key in self.mappings_data['mappings']:
                    entries = self.mappings_data['mappings'][node_key]
                    existing_entry = None
                    for entry in entries:
                        if entry['package_id'] == package_id:
                            existing_entry = entry
                            break

                    if existing_entry:
                        self.stats['nodes_skipped_exists'] += 1
                        continue

                # Add new entry for this package
                if node_key not in self.mappings_data['mappings']:
                    self.mappings_data['mappings'][node_key] = []

                package_info = self.mappings_data['packages'][package_id]
                score = calculate_package_score(
                    package_info.get('downloads', 0),
                    package_info.get('github_stars', 0)
                )

                self.mappings_data['mappings'][node_key].append({
                    'package_id': package_id,
                    'versions': [],
                    '_temp_score': score,
                    'rank': 0,  # Will be re-ranked later
                    'source': 'manager'
                })

                self.stats['nodes_added'] += 1
                nodes_added_for_package += 1
                logger.debug(f"Added {node_type} -> {package_id}")

            if nodes_added_for_package > 0:
                self.stats['packages_augmented'].add(package_id)
                logger.info(f"Augmented {package_id} with {nodes_added_for_package} nodes")

        # Second pass: Create synthetic packages for Manager-only extensions
        logger.info(f"Creating synthetic packages for {len(packages_not_found)} Manager-only extensions...")
        for repo_url, extension_data in packages_not_found.items():
            if not isinstance(extension_data, list) or len(extension_data) < 1:
                continue

            node_list = extension_data[0]
            if not isinstance(node_list, list):
                continue

            package_id = self.create_synthetic_package(repo_url, extension_data)
            if not package_id:
                self.stats['packages_not_found'].add(repo_url)
                continue

            self.stats['total_manager_nodes'] += len(node_list)

            nodes_added_for_package = 0
            for node_type in node_list:
                if not isinstance(node_type, str):
                    continue

                node_key = create_node_key(node_type, "_")

                # Check if this package already has an entry
                if node_key in self.mappings_data['mappings']:
                    entries = self.mappings_data['mappings'][node_key]
                    existing_entry = None
                    for entry in entries:
                        if entry['package_id'] == package_id:
                            existing_entry = entry
                            break

                    if existing_entry:
                        self.stats['nodes_skipped_exists'] += 1
                        continue

                # Add entry for synthetic package
                if node_key not in self.mappings_data['mappings']:
                    self.mappings_data['mappings'][node_key] = []

                package_info = self.mappings_data['packages'][package_id]
                score = calculate_package_score(
                    package_info.get('downloads', 0),
                    package_info.get('github_stars', 0)
                )

                self.mappings_data['mappings'][node_key].append({
                    'package_id': package_id,
                    'versions': [],
                    '_temp_score': score,
                    'rank': 0,
                    'source': 'manager'
                })

                self.stats['nodes_added'] += 1
                nodes_added_for_package += 1
                logger.debug(f"Added {node_type} -> synthetic {package_id}")

            if nodes_added_for_package > 0:
                logger.info(f"Synthetic package {package_id} mapped {nodes_added_for_package} nodes")

        # Third pass: Canonicalize legacy package IDs before fallback/overrides/ranking
        self._apply_package_aliases()

        # Fourth pass: Curated community fallback mappings (only fill unresolved keys)
        self._apply_community_fallback()

        # Fifth pass: Apply explicit per-node mapping overrides
        self._apply_mapping_overrides()

        # Re-rank all mappings
        self._rerank_all_mappings()

    def _apply_community_fallback(self):
        """Apply curated community mappings for unresolved node keys only."""
        if not self.community_data:
            return

        for mapping_entry in self.community_data:
            if not isinstance(mapping_entry, dict):
                continue

            node_type = mapping_entry.get("node_type")
            package_id = self._canonicalize_package_id(mapping_entry.get("package_id"))
            input_signature = mapping_entry.get("input_signature") or "_"

            if not node_type or not package_id:
                continue

            if package_id not in self.mappings_data["packages"]:
                raise ValueError(
                    f"Invalid community mapping: package_id '{package_id}' not found for node '{node_type}'"
                )

            node_key = create_node_key(node_type, input_signature)
            if node_key in self.mappings_data["mappings"]:
                self.stats['community_nodes_skipped_exists'] += 1
                continue

            package_info = self.mappings_data["packages"][package_id]
            score = calculate_package_score(
                package_info.get("downloads", 0),
                package_info.get("github_stars", 0)
            )

            self.mappings_data["mappings"][node_key] = [{
                "package_id": package_id,
                "versions": [],
                "_temp_score": score,
                "rank": 0,
                "source": "community"
            }]
            self.stats['community_nodes_added'] += 1
            logger.info(f"Community fallback mapped {node_key} -> {package_id}")

    def _apply_mapping_overrides(self):
        """Apply explicit per-node package override rules."""
        if not self.mapping_overrides:
            return

        for override in self.mapping_overrides:
            if not isinstance(override, dict):
                continue

            node_type = override.get("node_type")
            package_id = self._canonicalize_package_id(override.get("package_id"))
            input_signature = override.get("input_signature") or "_"

            if not node_type or not package_id:
                continue

            if package_id not in self.mappings_data["packages"]:
                raise ValueError(
                    f"Invalid override mapping: package_id '{package_id}' not found for node '{node_type}'"
                )

            node_key = create_node_key(node_type, input_signature)
            entries = self.mappings_data["mappings"].setdefault(node_key, [])

            override_entry = None
            remaining_entries = []
            for entry in entries:
                if entry.get("package_id") == package_id and override_entry is None:
                    override_entry = dict(entry)
                else:
                    remaining_entries.append(entry)

            if override_entry is None:
                override_entry = {
                    "package_id": package_id,
                    "versions": [],
                    "source": "override",
                }
            else:
                override_entry["source"] = "override"

            self.mappings_data["mappings"][node_key] = [override_entry, *remaining_entries]
            self.stats["mapping_overrides_applied"] += 1
            logger.info(f"Override mapped {node_key} -> {package_id}")

    def _rerank_all_mappings(self):
        """Re-rank all package entries based on scores."""
        for node_key, entries in self.mappings_data['mappings'].items():
            # Calculate scores for entries that don't have them yet (Registry entries)
            for entry in entries:
                if '_temp_score' not in entry:
                    pkg = self.mappings_data['packages'][entry['package_id']]
                    score = calculate_package_score(
                        pkg.get('downloads', 0),
                        pkg.get('github_stars', 0)
                    )
                    entry['_temp_score'] = score

            # Sort and rank (explicit overrides always stay at the top).
            entries.sort(
                key=lambda x: (x.get("source") == "override", x['_temp_score']),
                reverse=True,
            )
            for rank, entry in enumerate(entries, 1):
                entry['rank'] = rank
                del entry['_temp_score']  # Remove score from output

    def save_augmented_mappings(self, output_file: Path, schema_config: Path = None):
        """Save the augmented mappings.

        Args:
            output_file: Path to save augmented mappings
            schema_config: Optional path to schema configuration file for filtering
        """
        self.mappings_data['stats']['augmented'] = True
        self.mappings_data['stats']['augmentation_date'] = datetime.now().isoformat()
        self.mappings_data['stats']['nodes_from_manager'] = self.stats['nodes_added']
        self.mappings_data['stats']['nodes_from_community'] = self.stats['community_nodes_added']
        self.mappings_data['stats']['signatures'] = len(self.mappings_data['mappings'])
        self.mappings_data['stats']['packages'] = len(self.mappings_data['packages'])
        self.mappings_data['stats']['synthetic_packages'] = len(self.stats['synthetic_packages_created'])

        # Count total node entries (sum of all list lengths)
        total_nodes = sum(len(entries) for entries in self.mappings_data['mappings'].values())
        self.mappings_data['stats']['total_nodes'] = total_nodes

        # Sort mappings for deterministic output
        self.mappings_data['mappings'] = dict(sorted(self.mappings_data['mappings'].items()))

        # Apply schema filter if provided
        if schema_config and schema_config.exists():
            from schema_filter import SchemaFilter
            filter = SchemaFilter(schema_config)
            self.mappings_data = filter.filter_mappings_output(self.mappings_data)
            logger.info(f"Applied schema filter from {schema_config}")

        # Atomic write
        temp_file = Path(str(output_file) + '.tmp')
        try:
            with open(temp_file, 'w') as f:
                json.dump(self.mappings_data, f, indent=2)
            temp_file.replace(output_file)
            logger.info(f"Saved augmented mappings to {output_file}")
        finally:
            if temp_file.exists():
                temp_file.unlink()

    def print_summary(self):
        """Print augmentation summary."""
        print("\n" + "=" * 60)
        print("📊 AUGMENTATION SUMMARY")
        print("=" * 60)
        print(f"Total Manager nodes processed: {self.stats['total_manager_nodes']}")
        print(f"Nodes added: {self.stats['nodes_added']}")
        print(f"Nodes skipped (already exists): {self.stats['nodes_skipped_exists']}")
        print(f"Registry packages augmented: {len(self.stats['packages_augmented'])}")
        print(f"Synthetic packages created: {len(self.stats['synthetic_packages_created'])}")
        print(f"Packages failed to process: {len(self.stats['packages_not_found'])}")
        print(f"Package aliases applied: {self.stats['aliases_applied']}")
        print(f"Mapping overrides applied: {self.stats['mapping_overrides_applied']}")
        print(f"Community fallback nodes added: {self.stats['community_nodes_added']}")
        print(f"Community fallback nodes skipped: {self.stats['community_nodes_skipped_exists']}")
        print("=" * 60)

        if self.stats['synthetic_packages_created']:
            print(f"\n✨ Created {len(self.stats['synthetic_packages_created'])} synthetic packages from Manager-only extensions")

        if self.stats['packages_not_found'] and logger.isEnabledFor(10):
            print("\nPackages that couldn't be processed (first 10):")
            for url in list(self.stats['packages_not_found'])[:10]:
                print(f"  - {url}")


def main():
    parser = argparse.ArgumentParser(description="Augment node mappings with ComfyUI Manager data")
    parser.add_argument(
        '--mappings',
        type=Path,
        default=Path('src/comfygit_core/data/node_mappings.json'),
        help='Path to existing node_mappings.json'
    )
    parser.add_argument(
        '--manager',
        type=Path,
        default=Path('src/comfygit_core/data/extension-node-map.json'),
        help='Path to ComfyUI Manager extension-node-map.json'
    )
    parser.add_argument(
        '--output',
        type=Path,
        help='Output file (default: overwrite input mappings file)'
    )
    parser.add_argument(
        '--schema-config',
        type=Path,
        default=Path('config/output_schema.toml'),
        help='Schema configuration file (default: config/output_schema.toml)'
    )
    parser.add_argument(
        '--community',
        type=Path,
        default=Path('config/community_mappings.json'),
        help='Path to curated community mappings JSON (default: config/community_mappings.json)'
    )
    parser.add_argument(
        '--aliases',
        type=Path,
        default=Path('config/package_aliases.json'),
        help='Path to package aliases JSON (default: config/package_aliases.json)'
    )
    parser.add_argument(
        '--overrides',
        type=Path,
        default=Path('config/node_mapping_overrides.json'),
        help='Path to node mapping overrides JSON (default: config/node_mapping_overrides.json)'
    )
    parser.add_argument(
        '--log-level',
        default='INFO',
        help='Logging level'
    )

    args = parser.parse_args()

    if not args.output:
        args.output = args.mappings

    if not args.mappings.exists():
        parser.error(f"Mappings file not found: {args.mappings}")
    if not args.manager.exists():
        parser.error(f"Manager file not found: {args.manager}")

    augmenter = MappingsAugmenter(
        args.mappings,
        args.manager,
        args.community,
        args.aliases,
        args.overrides,
    )
    augmenter.load_data()
    augmenter.augment_mappings()
    augmenter.save_augmented_mappings(args.output, schema_config=args.schema_config)
    augmenter.print_summary()


if __name__ == '__main__':
    main()

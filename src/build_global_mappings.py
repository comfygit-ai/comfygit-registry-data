#!/usr/bin/env python3
"""Build global node mappings from cached registry data."""

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from comfygit_core.utils.input_signature import (
    create_node_key,
    normalize_registry_inputs,
)
from url_utils import normalize_repository_url

from logging import getLogger

logger = getLogger(__name__)


def calculate_package_score(downloads: int, github_stars: int) -> float:
    """Calculate popularity score for package ranking.

    Args:
        downloads: Number of package downloads
        github_stars: Number of GitHub stars

    Returns:
        Popularity score (higher is better)
    """
    # Weight downloads and stars equally
    # Normalize to prevent one metric from dominating
    # Add 1 to avoid division by zero
    score = (downloads / 10.0) + (github_stars * 2.0)
    return max(score, 0.1)  # Ensure minimum score


class GlobalMappingsBuilder:
    """Builds global node mappings from cached registry data."""

    def __init__(self):
        self.mappings = {}  # node_key -> [{"package_id", "versions", "score", "rank"}]
        self.packages = {}  # package_id -> package metadata
        self.total_nodes = 0
        self.total_signatures = 0

    def build_mappings(self, registry_cache: Path, existing_mappings: Path = None) -> Dict:
        """Build mappings from cached registry data with optional incremental support."""
        start_time = time.time()
        logger.info("Starting mappings build from cache")

        # Load existing mappings if provided (for incremental updates)
        if existing_mappings and existing_mappings.exists():
            logger.info(f"Loading existing mappings from {existing_mappings}")
            with open(existing_mappings, 'r') as f:
                existing_data = json.load(f)
                self.mappings = existing_data.get("mappings", {})
                self.packages = existing_data.get("packages", {})
                logger.info(f"Loaded {len(self.mappings)} existing mappings, {len(self.packages)} packages")

        # Load registry cache
        if not registry_cache.exists():
            logger.error(f"Registry cache not found: {registry_cache}")
            return {}

        with open(registry_cache, 'r') as f:
            cache_data = json.load(f)

        nodes = cache_data.get("nodes", [])
        cached_at = cache_data.get("cached_at", "")
        metadata_entries = cache_data.get("metadata_entries", 0)

        logger.info(f"Loaded cache: {len(nodes)} nodes, {metadata_entries} metadata entries (cached at: {cached_at})")

        # Process all nodes
        for i, node in enumerate(nodes, 1):
            if i % 100 == 0:
                logger.info(f"Processing node {i}/{len(nodes)}...")

            self._process_node(node)

        # Finalize: rank all packages for each signature
        self._rank_all_mappings()

        # Build stats
        elapsed = time.time() - start_time
        logger.info("=" * 60)
        logger.info("📊 BUILD SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Total packages processed: {len(self.packages)}")
        logger.info(f"Node signatures collected: {len(self.mappings)}")
        logger.info(f"Total nodes mapped: {self.total_nodes}")
        logger.info(f"Build time: {elapsed:.1f}s")
        logger.info("=" * 60)

        # Return complete data structure
        return {
            "version": datetime.now().strftime("%Y.%m.%d"),
            "generated_at": datetime.now().isoformat(),
            "stats": {
                "packages": len(self.packages),
                "signatures": len(self.mappings),
                "total_nodes": self.total_nodes
            },
            "mappings": self.mappings,
            "packages": self.packages
        }

    def _process_node(self, node: Dict):
        """Process a single node package from cache."""
        package_id = node["id"]

        # Store package metadata (only once per package)
        if package_id not in self.packages:
            self.packages[package_id] = {
                "display_name": node.get("name", package_id),
                "author": node.get("author", ""),
                "description": node.get("description", ""),
                "repository": normalize_repository_url(node.get("repository", "")),
                "downloads": node.get("downloads", 0),
                "github_stars": node.get("github_stars", 0),
                "rating": node.get("rating", 0),
                "license": node.get("license", ""),
                "category": node.get("category", ""),
                "icon": node.get("icon", ""),
                "tags": node.get("tags", []),
                "status": node.get("status", ""),
                "created_at": node.get("created_at", ""),
                "versions": {}  # version -> metadata
            }

        # Process versions
        versions_list = node.get("versions_list", [])
        if not versions_list:
            logger.debug(f"No versions for {package_id}")
            return

        # Process each version
        for version_info in versions_list:
            version = version_info["version"]

            # Skip deprecated versions for node mappings
            skip_for_mappings = version_info.get("deprecated", False)

            # Store version metadata (excluding comfy_nodes)
            version_metadata = {
                "version": version,
                "changelog": version_info.get("changelog", ""),
                "release_date": version_info.get("createdAt", ""),
                "dependencies": version_info.get("dependencies", []),
                "deprecated": version_info.get("deprecated", False),
                "download_url": version_info.get("download_url", version_info.get("downloadUrl", "")),
                "status": version_info.get("status", ""),
                "supported_accelerators": version_info.get("supported_accelerators"),
                "supported_comfyui_version": version_info.get("supported_comfyui_version", ""),
                "supported_os": version_info.get("supported_os")
            }

            # Add to package versions
            self.packages[package_id]["versions"][version] = version_metadata

            # Process comfy-nodes metadata for mappings (skip deprecated versions)
            if not skip_for_mappings:
                comfy_nodes = version_info.get("comfy_nodes", [])
                if comfy_nodes:
                    self._process_comfy_nodes(package_id, version, comfy_nodes)

        # Sort versions dictionary by version number (highest first)
        versions_dict = self.packages[package_id]["versions"]
        sorted_versions = sorted(
            versions_dict.items(),
            key=lambda x: self._parse_version(x[0]),
            reverse=True
        )
        self.packages[package_id]["versions"] = dict(sorted_versions)

    def _parse_version(self, version_str: str) -> tuple:
        """Parse version string for sorting.

        Returns tuple of integers for proper semantic version sorting.
        Examples: "1.2.3" -> (1, 2, 3), "2.0.0-beta1" -> (2, 0, 0)
        """
        # Remove any pre-release suffixes
        base_version = version_str.split('-')[0].split('+')[0]

        try:
            parts = []
            for part in base_version.split('.'):
                try:
                    parts.append(int(part))
                except ValueError:
                    parts.append(0)
            # Pad with zeros to ensure consistent length
            while len(parts) < 3:
                parts.append(0)
            return tuple(parts)
        except Exception:
            return (0, 0, 0)

    def _calculate_recency_multiplier(self, package_id: str) -> float:
        """Calculate recency multiplier based on package age (0.5 to 1.0).

        Uses latest version date to determine package freshness:
        - 0-90 days: 1.0 (no penalty)
        - 90-180 days: 0.95 (5% penalty)
        - 180-365 days: 0.85 (15% penalty)
        - 365-730 days: 0.70 (30% penalty)
        - 730+ days: 0.50 (50% penalty)

        Args:
            package_id: Package identifier

        Returns:
            Multiplier between 0.5 and 1.0
        """
        package_info = self.packages[package_id]
        versions = package_info.get("versions", {})

        if not versions:
            return 1.0  # No penalty if no version data

        # Find most recent version date
        latest_date = None
        for version_data in versions.values():
            release_date = version_data.get("release_date")
            if release_date:
                try:
                    dt = datetime.fromisoformat(release_date.replace('Z', '+00:00'))
                    if latest_date is None or dt > latest_date:
                        latest_date = dt
                except:
                    pass

        if latest_date is None:
            return 1.0  # No penalty if can't parse dates

        # Calculate age
        now = datetime.now(timezone.utc)
        days_old = (now - latest_date).days

        # Step function penalty
        if days_old < 90:
            return 1.0
        elif days_old < 180:
            return 0.95
        elif days_old < 365:
            return 0.85
        elif days_old < 730:
            return 0.70
        else:
            return 0.50

    def _process_comfy_nodes(self, package_id: str, version: str, comfy_nodes: List[Dict]):
        """Process comfy-nodes metadata and create mappings."""
        # Get package stats for scoring
        package_info = self.packages[package_id]
        downloads = package_info.get("downloads", 0)
        github_stars = package_info.get("github_stars", 0)
        base_score = calculate_package_score(downloads, github_stars)

        # Apply recency multiplier
        recency_multiplier = self._calculate_recency_multiplier(package_id)
        score = base_score * recency_multiplier

        for node_data in comfy_nodes:
            display_name = node_data.get("comfy_node_name", "")
            if not display_name:
                continue

            # Parse and normalize inputs
            input_types_str = node_data.get("input_types", "")
            normalized_inputs = ""

            if input_types_str:
                try:
                    # input_types might be string or already parsed dict
                    if isinstance(input_types_str, str):
                        normalized_inputs = normalize_registry_inputs(input_types_str)
                    elif isinstance(input_types_str, dict):
                        normalized_inputs = normalize_registry_inputs(json.dumps(input_types_str))
                except Exception as e:
                    logger.debug(f"Failed to normalize inputs for {display_name}: {e}")
                    normalized_inputs = ""

            # Create node key
            node_key = create_node_key(display_name, normalized_inputs)

            # Initialize mapping list if needed
            if node_key not in self.mappings:
                self.mappings[node_key] = []
                self.total_signatures += 1

            # Find existing entry for this package
            existing_entry = None
            for entry in self.mappings[node_key]:
                if entry["package_id"] == package_id:
                    existing_entry = entry
                    break

            if existing_entry:
                # Add version if not already present
                if version not in existing_entry["versions"]:
                    existing_entry["versions"].append(version)
            else:
                # Create new entry for this package (NO source field - Registry is default)
                self.mappings[node_key].append({
                    "package_id": package_id,
                    "versions": [version],
                    "_temp_score": score,  # Temporary, will be removed after ranking
                    "rank": 0  # Will be set in _rank_all_mappings
                })

            self.total_nodes += 1

    def _rank_all_mappings(self):
        """Assign ranks to all package entries based on scores."""
        for node_key, entries in self.mappings.items():
            # Sort by score (descending)
            entries.sort(key=lambda x: x["_temp_score"], reverse=True)

            # Assign ranks and remove temporary score
            for rank, entry in enumerate(entries, 1):
                entry["rank"] = rank
                del entry["_temp_score"]  # Remove score from output


def main():
    parser = argparse.ArgumentParser(
        description="Build global node mappings from registry cache",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  uv run scripts/build_global_mappings.py \\
    --cache registry_cache.json \\
    --output node_mappings.json
        """
    )

    parser.add_argument(
        "--cache",
        "-c",
        type=Path,
        required=True,
        help="Registry cache file (from build_registry_cache.py)"
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        required=True,
        help="Output mappings file"
    )
    parser.add_argument(
        "--existing",
        "-e",
        type=Path,
        help="Existing mappings file to merge with (for incremental updates)"
    )
    parser.add_argument(
        "--schema-config",
        type=Path,
        default=Path("config/output_schema.toml"),
        help="Schema configuration file (default: config/output_schema.toml)"
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level"
    )

    args = parser.parse_args()

    # Build mappings
    builder = GlobalMappingsBuilder()
    data = builder.build_mappings(registry_cache=args.cache, existing_mappings=args.existing)

    if not data:
        logger.error("Failed to build mappings")
        return 1

    # Apply schema filter if provided
    if args.schema_config and args.schema_config.exists():
        from schema_filter import SchemaFilter
        filter = SchemaFilter(args.schema_config)
        data = filter.filter_mappings_output(data)
        logger.info(f"Applied schema filter from {args.schema_config}")

    # Save results
    try:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, 'w') as f:
            json.dump(data, f, indent=2)

        file_size = args.output.stat().st_size / 1024 / 1024
        logger.info(f"✅ Mappings saved to {args.output} ({file_size:.1f} MB)")

    except Exception as e:
        logger.error(f"Failed to save mappings: {e}")
        return 1

    return 0


if __name__ == "__main__":
    exit(main() or 0)

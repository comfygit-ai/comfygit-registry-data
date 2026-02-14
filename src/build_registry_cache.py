#!/usr/bin/env python3
"""Build comprehensive registry cache with progressive enhancement support."""

import argparse
import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from registry_client import RegistryClient

from logging import getLogger

logger = getLogger(__name__)



class RegistryCacheBuilder:
    """Builds registry cache with three-phase progressive enhancement."""

    def __init__(
        self,
        concurrency: int = 10,
        checkpoint_interval: int = 20,
        node_timeout: int = 300,
        batch_timeout: int = 600,
        max_versions: int = 1,
        nodes_per_page: int = 100,
        rate_limit_delay: float = 0.1,
        max_retries: int = 3,
        force_metadata_refresh: bool = False
    ):
        self.concurrency = concurrency
        self.checkpoint_interval = checkpoint_interval
        self.node_timeout = node_timeout
        self.batch_timeout = batch_timeout
        self.max_versions = max_versions
        self.nodes_per_page = nodes_per_page
        self.rate_limit_delay = rate_limit_delay
        self.max_retries = max_retries
        self.force_metadata_refresh = force_metadata_refresh
        self.nodes_processed = 0
        self.versions_processed = 0
        self.metadata_fetched = 0
        self.failed_nodes = []
        self.nodes_data = {}  # Using dict for O(1) lookups
        self.last_checkpoint = 0

    async def build_cache(
        self,
        output_file: Path,
        input_cache: Optional[Path] = None,
        fetch_nodes: bool = True,
        fetch_versions: bool = True,
        fetch_metadata: bool = True,
        pages: Optional[int] = None
    ):
        """Build registry cache with progressive enhancement."""
        start_time = time.time()

        # Determine active phases
        phases = []
        if fetch_nodes:
            phases.append("Phase 1: Basic node info")
        if fetch_versions:
            phases.append("Phase 2: Versions & install data")
        if fetch_metadata:
            phases.append("Phase 3: Metadata")

        logger.info(f"Starting cache build - Active phases: {', '.join(phases)}")
        logger.info(f"Settings: concurrency={self.concurrency}, checkpoint_interval={self.checkpoint_interval}")

        if self.max_versions > 0 and fetch_metadata:
            logger.info(f"Will fetch metadata for up to {self.max_versions} most recent versions per node")

        # Load existing cache
        if input_cache and input_cache.exists():
            self._load_cache(input_cache)
            logger.info(f"Loaded {len(self.nodes_data)} nodes from existing cache")

        async with RegistryClient(concurrency=self.concurrency, max_retries=self.max_retries) as client:
            # Phase 1: Fetch basic node info
            if fetch_nodes:
                await self._phase1_fetch_nodes(client, output_file, pages)

            # Phase 2: Fetch versions and install info
            if fetch_versions:
                await self._phase2_fetch_versions(client, output_file)
                # Explicit checkpoint after version updates
                self._save_cache(output_file)
                logger.info("📦 Checkpoint: Version updates saved")

            # Phase 3: Fetch metadata
            if fetch_metadata:
                await self._phase3_fetch_metadata(client, output_file)

            # Final save
            self._save_cache(output_file)

        # Report results
        elapsed = time.time() - start_time
        self._print_summary(elapsed)

    async def _phase1_fetch_nodes(self, client: RegistryClient, output_file: Path, max_pages: Optional[int]):
        """Phase 1: Fetch basic node information."""
        logger.info("=" * 60)
        logger.info("PHASE 1: Fetching basic node information")
        logger.info("=" * 60)

        all_nodes = []
        page = 1
        total_pages = None

        max_retries = 5  # More aggressive retries
        while True:
            if max_pages and page > max_pages:
                logger.info(f"Reached max_pages limit ({max_pages})")
                break

            retries = 0
            page_success = False

            while retries < max_retries and not page_success:
                try:
                    logger.debug(f"Fetching nodes page {page} (limit={self.nodes_per_page})...")
                    url = f"{client.base_url}/nodes"
                    params = {"page": page, "limit": self.nodes_per_page}

                    # Longer timeout for problematic pages
                    timeout = asyncio.wait_for(
                        client.session.get(url, params=params),
                        timeout=30  # 30 second timeout
                    )

                    async with await timeout as response:
                        if response.status != 200:
                            logger.error(f"Failed to fetch nodes page {page}: {response.status}")
                            if response.status >= 400 and response.status < 500:
                                # Client error - don't retry
                                break
                            # Server error - will retry
                            retries += 1
                            if retries < max_retries:
                                await asyncio.sleep(3 ** retries)  # More aggressive backoff
                            continue

                        data = await response.json()
                        nodes = data.get("nodes", [])

                        if nodes:
                            # Process and cache immediately
                            for node in nodes:
                                node_id = node["id"]
                                api_latest = node.get("latest_version", {}).get("version") if isinstance(node.get("latest_version"), dict) else None

                                if node_id not in self.nodes_data:
                                    # New node - initialize with basic info and timestamps
                                    self.nodes_data[node_id] = node
                                    self.nodes_data[node_id]["basic_cached"] = True
                                    self.nodes_data[node_id]["versions_cached"] = False
                                    self.nodes_data[node_id]["metadata_count"] = 0
                                    self.nodes_data[node_id]["first_seen"] = datetime.now().isoformat()
                                    self.nodes_data[node_id]["last_checked"] = datetime.now().isoformat()
                                    self.nodes_data[node_id]["_needs_version_refresh"] = True
                                else:
                                    # Update existing node with latest basic info
                                    existing = self.nodes_data[node_id]
                                    cached_latest = existing.get("latest_version", {}).get("version") if isinstance(existing.get("latest_version"), dict) else None

                                    # Check if latest version changed
                                    if api_latest != cached_latest:
                                        existing["_needs_version_refresh"] = True
                                        logger.debug(f"{node_id}: latest version changed ({cached_latest} → {api_latest})")
                                    else:
                                        # Check if not checked in 24h (fallback for intermediate versions)
                                        last_checked = existing.get("last_checked")
                                        if last_checked:
                                            try:
                                                last_checked_dt = datetime.fromisoformat(last_checked)
                                                hours_since_check = (datetime.now() - last_checked_dt).total_seconds() / 3600
                                                if hours_since_check > 24:
                                                    existing["_needs_version_refresh"] = True
                                                    logger.debug(f"{node_id}: forcing check (last checked {hours_since_check:.1f}h ago)")
                                                else:
                                                    existing["_needs_version_refresh"] = False
                                            except Exception:
                                                existing["_needs_version_refresh"] = True
                                        else:
                                            existing["_needs_version_refresh"] = True

                                    existing.update({k: v for k, v in node.items()
                                                   if k not in ['versions_list', 'basic_cached',
                                                               'versions_cached', 'metadata_count', 'first_seen', 'last_checked']})
                                    existing["basic_cached"] = True
                                    # Note: last_checked is intentionally NOT updated here to allow Phase 2 to process existing nodes

                            all_nodes.extend(nodes)
                            logger.info(f"Page {page}: Cached {len(nodes)} nodes")

                        if total_pages is None:
                            total_pages = data.get("totalPages", 1)
                            logger.info(f"Total pages to fetch: {total_pages}")

                        page_success = True

                        if page >= total_pages or not nodes:
                            break

                except asyncio.TimeoutError:
                    retries += 1
                    logger.warning(f"Timeout on page {page} (attempt {retries}/{max_retries})")
                    if retries < max_retries:
                        await asyncio.sleep(3 ** retries)  # More aggressive backoff
                except Exception as e:
                    retries += 1
                    logger.warning(f"Error fetching page {page} (attempt {retries}/{max_retries}): {e}")
                    if retries < max_retries:
                        await asyncio.sleep(3 ** retries)  # More aggressive backoff
                    else:
                        logger.error(f"Failed to fetch page {page} after {max_retries} attempts")

            if not page_success:
                logger.error(f"Giving up on page {page}, continuing with next page")
                # Continue to next page instead of breaking entirely

            if page_success and total_pages and page >= total_pages:
                break

            page += 1
            if page_success:
                await asyncio.sleep(2)  # Longer delay between pages

        logger.info(f"Phase 1 complete: Fetched {len(all_nodes)} nodes")

    async def _phase2_fetch_versions(self, client: RegistryClient, output_file: Path):
        """Phase 2: Fetch versions and install info."""
        logger.info("=" * 60)
        logger.info("PHASE 2: Fetching versions and install data")
        logger.info("=" * 60)

        # Process nodes that need version refresh based on Phase 1 detection
        all_nodes = list(self.nodes_data.items())
        nodes_to_process = []
        skipped_unchanged = 0

        for node_id, node in all_nodes:
            # Check if version refresh is needed (set by Phase 1)
            if not node.get("_needs_version_refresh", True):
                skipped_unchanged += 1
                continue

            nodes_to_process.append((node_id, node))

        if skipped_unchanged > 0:
            logger.info(f"Optimization: Skipped {skipped_unchanged} nodes with unchanged latest version")

        if not nodes_to_process:
            logger.info("No nodes to process")
            return

        logger.info(f"Checking versions for {len(nodes_to_process)} nodes")

        # Process in batches for checkpointing
        for i in range(0, len(nodes_to_process), self.checkpoint_interval):
            batch = nodes_to_process[i:i+self.checkpoint_interval]
            batch_num = (i // self.checkpoint_interval) + 1
            total_batches = (len(nodes_to_process) + self.checkpoint_interval - 1) // self.checkpoint_interval

            logger.info(f"Processing batch {batch_num}/{total_batches} ({len(batch)} nodes)")

            # Process batch in groups of `concurrency` to respect rate limits
            for j in range(0, len(batch), self.concurrency):
                group = batch[j:j+self.concurrency]

                tasks = [
                    asyncio.create_task(self._fetch_node_versions_incremental(client, node_id))
                    for node_id, _ in group
                ]

                try:
                    await asyncio.wait_for(
                        asyncio.gather(*tasks, return_exceptions=True),
                        timeout=self.batch_timeout
                    )
                except asyncio.TimeoutError:
                    logger.error(f"Batch {batch_num} group {j//self.concurrency + 1} timed out")

            # Checkpoint after each batch
            self._save_cache(output_file)
            logger.info(f"Checkpoint saved after batch {batch_num}")

    async def _phase3_fetch_metadata(self, client: RegistryClient, output_file: Path):
        """Phase 3: Fetch metadata for versions."""
        logger.info("=" * 60)
        logger.info("PHASE 3: Fetching metadata")
        logger.info("=" * 60)

        if self.max_versions <= 0:
            logger.info("No metadata limit specified, fetching all")

        if self.force_metadata_refresh:
            logger.info("⚠️  METADATA OVERRIDE MODE: Will re-fetch all metadata even if cached")

        # Get nodes that need metadata for their LATEST versions
        nodes_needing_metadata = []
        for node_id, node in self.nodes_data.items():
            versions_list = node.get("versions_list", [])
            if not versions_list:
                continue

            # Get the top N versions (already sorted newest first)
            max_to_check = self.max_versions if self.max_versions > 0 else len(versions_list)
            top_versions = versions_list[:max_to_check]

            # Check if we need to process this node
            if self.force_metadata_refresh:
                # Force refresh mode - process ALL nodes with versions
                nodes_needing_metadata.append((node_id, node, len(top_versions)))
            else:
                # Normal mode - only process nodes with missing metadata
                missing_metadata_count = sum(1 for v in top_versions if not v.get("metadata_cached", False))
                if missing_metadata_count > 0:
                    nodes_needing_metadata.append((node_id, node, missing_metadata_count))

        if not nodes_needing_metadata:
            logger.info("All nodes have sufficient metadata cached")
            return

        logger.info(f"Processing metadata for {len(nodes_needing_metadata)} nodes")

        # Process in batches
        for i in range(0, len(nodes_needing_metadata), self.checkpoint_interval):
            batch = nodes_needing_metadata[i:i+self.checkpoint_interval]
            batch_num = (i // self.checkpoint_interval) + 1
            total_batches = (len(nodes_needing_metadata) + self.checkpoint_interval - 1) // self.checkpoint_interval

            logger.info(f"Processing batch {batch_num}/{total_batches} ({len(batch)} nodes)")

            # Process batch in groups of `concurrency` to respect rate limits
            for j in range(0, len(batch), self.concurrency):
                group = batch[j:j+self.concurrency]

                tasks = [
                    asyncio.create_task(self._fetch_node_metadata(client, node_id))
                    for node_id, node, _ in group
                ]

                try:
                    await asyncio.wait_for(
                        asyncio.gather(*tasks, return_exceptions=True),
                        timeout=self.batch_timeout
                    )
                except asyncio.TimeoutError:
                    logger.error(f"Batch {batch_num} group {j//self.concurrency + 1} timed out")

            # Checkpoint after each batch
            self._save_cache(output_file)
            logger.info(f"Checkpoint saved after batch {batch_num}")

    async def _fetch_node_versions_incremental(self, client: RegistryClient, node_id: str):
        """Fetch versions and install info incrementally for a node."""
        try:
            # Get current versions from API with rate limiting delay
            api_versions = await client.get_node_versions(node_id)
            await asyncio.sleep(self.rate_limit_delay)  # Rate limit delay between version requests

            if not api_versions:
                # No versions available
                if "versions_list" not in self.nodes_data[node_id]:
                    self.nodes_data[node_id]["versions_list"] = []
                    self.nodes_data[node_id]["versions_cached"] = True
                    logger.debug(f"No versions found for {node_id}")
                return

            # Sort API versions by date (most recent first)
            api_versions.sort(key=lambda v: v.get("createdAt", ""), reverse=True)

            # Get existing cached versions
            existing_versions = self.nodes_data[node_id].get("versions_list", [])
            existing_version_map = {v["version"]: v for v in existing_versions}

            # Update deprecated status for existing versions
            deprecated_updates = 0
            for api_version in api_versions:
                version_key = api_version["version"]
                if version_key in existing_version_map:
                    cached_version = existing_version_map[version_key]
                    api_deprecated = api_version.get("deprecated", False)
                    cached_deprecated = cached_version.get("deprecated", False)

                    if api_deprecated != cached_deprecated:
                        cached_version["deprecated"] = api_deprecated
                        deprecated_updates += 1

            # Find NEW versions that aren't cached yet
            new_versions = [v for v in api_versions if v["version"] not in existing_version_map]

            if not new_versions and deprecated_updates == 0:
                logger.debug(f"No updates for {node_id} ({len(existing_versions)} cached)")
                return

            if deprecated_updates > 0:
                logger.debug(f"Updated deprecated status for {deprecated_updates} versions of {node_id}")

            if not new_versions:
                logger.debug(f"No new versions for {node_id}, but updated {deprecated_updates} deprecated flags")
                return

            logger.debug(f"Found {len(new_versions)} new versions for {node_id} (had {len(existing_versions)})")

            # Process NEW versions - only call install if downloadUrl missing
            new_enriched_versions = []
            install_calls_made = 0

            for version_info in new_versions:
                version = version_info["version"]

                # Check if we already have downloadUrl from versions endpoint
                has_download_url = version_info.get("downloadUrl", "").strip() != ""

                if has_download_url:
                    # Use existing data from versions endpoint
                    version_info["download_url"] = version_info["downloadUrl"]
                    # dependencies already in version_info if present
                else:
                    # Missing downloadUrl - call install endpoint
                    install_info = await client.get_install_info(node_id, version)
                    if install_info:
                        version_info["download_url"] = install_info.get("downloadUrl", "")
                        version_info["dependencies"] = install_info.get("dependencies", [])
                        version_info["install_type"] = install_info.get("installType", "")
                    install_calls_made += 1
                    await asyncio.sleep(0.02)  # Rate limit only when making API calls

                version_info["metadata_cached"] = False
                version_info["first_seen"] = datetime.now().isoformat()
                new_enriched_versions.append(version_info)
                self.versions_processed += 1

            if install_calls_made > 0:
                logger.debug(f"Made {install_calls_made} install calls for {node_id} (others had downloadUrl)")
            else:
                logger.debug(f"No install calls needed for {node_id} - all versions had downloadUrl")

            # Merge: existing versions + new versions, maintain sort by date
            all_versions = list(existing_versions) + new_enriched_versions
            all_versions.sort(key=lambda v: v.get("createdAt", ""), reverse=True)

            self.nodes_data[node_id]["versions_list"] = all_versions
            self.nodes_data[node_id]["versions_cached"] = True
            self.nodes_data[node_id]["last_checked"] = datetime.now().isoformat()

            logger.debug(f"Added {len(new_enriched_versions)} new versions to {node_id} (total: {len(all_versions)})")

        except Exception as e:
            logger.error(f"Failed to fetch versions for {node_id}: {e}")
            self.failed_nodes.append(node_id)

    async def _fetch_node_metadata(self, client: RegistryClient, node_id: str):
        """Fetch metadata for the latest N versions of a node."""
        try:
            node = self.nodes_data[node_id]
            versions_list = node.get("versions_list", [])

            if not versions_list:
                return

            # Only process the top N versions (already sorted newest first)
            max_to_process = self.max_versions if self.max_versions > 0 else len(versions_list)
            top_versions = versions_list[:max_to_process]

            metadata_fetched = 0

            for version_info in top_versions:
                # Skip if already has metadata (unless forcing refresh)
                if not self.force_metadata_refresh:
                    if version_info.get("metadata_cached", False):
                        continue

                version = version_info["version"]

                # Get comfy-nodes metadata
                logger.debug(f"Fetching metadata for {node_id}@{version}")
                comfy_nodes = await client.get_comfy_nodes(node_id, version)

                if comfy_nodes is None:
                    # Failed to fetch (rate limited, timeout, error)
                    # Don't mark as cached so we can retry on next run
                    logger.debug(f"Failed to fetch metadata for {node_id}@{version}, will retry on next run")
                    continue
                elif comfy_nodes:
                    # Successfully fetched with data
                    version_info["comfy_nodes"] = comfy_nodes
                    version_info["metadata_cached"] = True
                    self.metadata_fetched += len(comfy_nodes)
                    metadata_fetched += 1
                else:
                    # Successfully fetched but empty (no metadata exists for this version)
                    version_info["comfy_nodes"] = []
                    version_info["metadata_cached"] = True
                    metadata_fetched += 1

                await asyncio.sleep(0.05)  # Rate limit

            # Update metadata count
            node["metadata_count"] = sum(
                1 for v in versions_list if v.get("metadata_cached", False)
            )

            logger.debug(f"Fetched metadata for {metadata_fetched} versions of {node_id}")

        except Exception as e:
            logger.error(f"Failed to fetch metadata for {node_id}: {e}")
            self.failed_nodes.append(node_id)

    def _load_cache(self, cache_file: Path):
        """Load existing cache data with timestamp preservation."""
        with open(cache_file, 'r') as f:
            cache_data = json.load(f)

        # Convert nodes list to dict for efficient lookups
        nodes = cache_data.get("nodes", [])
        for node in nodes:
            # Ensure timestamps exist for existing data
            if "first_seen" not in node:
                node["first_seen"] = cache_data.get("cached_at", datetime.now().isoformat())
            if "last_checked" not in node:
                node["last_checked"] = cache_data.get("cached_at", datetime.now().isoformat())

            # Ensure version timestamps exist
            for version in node.get("versions_list", []):
                if "first_seen" not in version:
                    version["first_seen"] = node.get("first_seen", datetime.now().isoformat())

            self.nodes_data[node["id"]] = node

    def _save_cache(self, output_file: Path):
        """Save cache to file atomically."""
        try:
            # Convert nodes dict to list for storage
            nodes_list = list(self.nodes_data.values())

            # Calculate stats
            total_versions = sum(
                len(n.get("versions_list", [])) for n in nodes_list
            )
            total_metadata = sum(
                sum(len(v.get("comfy_nodes", [])) for v in n.get("versions_list", []))
                for n in nodes_list
            )

            cache_data = {
                "cached_at": datetime.now().isoformat(),
                "node_count": len(nodes_list),
                "versions_processed": total_versions,
                "metadata_entries": total_metadata,
                "nodes": nodes_list
            }

            output_file.parent.mkdir(parents=True, exist_ok=True)

            # Atomic write
            temp_file = Path(str(output_file) + '.tmp')
            try:
                with open(temp_file, 'w') as f:
                    json.dump(cache_data, f, indent=2)
                temp_file.replace(output_file)

                file_size = output_file.stat().st_size / 1024 / 1024
                logger.debug(f"Cache saved: {len(nodes_list)} nodes, {file_size:.1f} MB")
            finally:
                if temp_file.exists():
                    temp_file.unlink()

        except Exception as e:
            logger.error(f"Failed to save cache: {e}")
            raise

    def _print_summary(self, elapsed: float):
        """Print build summary."""
        nodes_list = list(self.nodes_data.values())

        # Calculate phase completion stats
        phase1_complete = sum(1 for n in nodes_list if n.get("basic_cached", False))
        phase2_complete = sum(1 for n in nodes_list if n.get("versions_cached", False))
        phase3_nodes = sum(1 for n in nodes_list if n.get("metadata_count", 0) > 0)

        total_versions = sum(len(n.get("versions_list", [])) for n in nodes_list)
        total_metadata = sum(
            sum(len(v.get("comfy_nodes", [])) for v in n.get("versions_list", []))
            for n in nodes_list
        )

        logger.info("=" * 60)
        logger.info("📊 CACHE BUILD SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Phase 1 (basic info): {phase1_complete} nodes")
        logger.info(f"Phase 2 (versions): {phase2_complete} nodes, {total_versions} versions")
        logger.info(f"Phase 3 (metadata): {phase3_nodes} nodes, {total_metadata} entries")
        logger.info(f"Failed nodes: {len(self.failed_nodes)}")
        logger.info(f"Build time: {elapsed:.1f}s")
        logger.info("=" * 60)

        if self.failed_nodes:
            logger.warning(f"Failed nodes: {', '.join(self.failed_nodes[:10])}"
                         + (f" ... and {len(self.failed_nodes) - 10} more"
                            if len(self.failed_nodes) > 10 else ""))


def main():
    parser = argparse.ArgumentParser(
        description="Build registry cache with progressive enhancement",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Progressive Enhancement Examples:

  # Phase 1: Fetch basic node info only
  uv run scripts/build_registry_cache.py \\
    --output cache.json --no-versions --no-metadata

  # Phase 2: Add versions and install data
  uv run scripts/build_registry_cache.py \\
    --input cache.json --output cache.json \\
    --no-nodes --no-metadata

  # Phase 3: Add metadata for top 10 versions
  uv run scripts/build_registry_cache.py \\
    --input cache.json --output cache.json \\
    --no-nodes --no-versions --max-versions 10
        """
    )

    # File arguments
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=Path("src/comfygit_core/data/registry_cache.json"),
        help="Output cache file"
    )
    parser.add_argument(
        "--input", "-i",
        type=Path,
        help="Existing cache for incremental update"
    )

    # Phase control
    parser.add_argument(
        "--no-nodes",
        action="store_true",
        help="Skip fetching basic node info (Phase 1)"
    )
    parser.add_argument(
        "--no-versions",
        action="store_true",
        help="Skip fetching versions and install data (Phase 2)"
    )
    parser.add_argument(
        "--no-metadata",
        action="store_true",
        help="Skip fetching metadata (Phase 3)"
    )

    # Performance settings
    parser.add_argument(
        "--concurrency", "-c",
        type=int,
        default=10,
        help="Number of concurrent requests"
    )
    parser.add_argument(
        "--checkpoint-interval",
        type=int,
        default=20,
        help="Save checkpoint every N nodes"
    )
    parser.add_argument(
        "--nodes-per-page",
        type=int,
        default=100,
        help="Nodes per page when fetching from registry"
    )

    # Limits
    parser.add_argument(
        "--max-versions",
        type=int,
        default=-1,
        help="Max versions per node to fetch metadata for (-1 for all)"
    )
    parser.add_argument(
        "--pages",
        type=int,
        help="Max pages of nodes to fetch (for testing)"
    )

    # Timeouts
    parser.add_argument(
        "--node-timeout",
        type=int,
        default=300,
        help="Timeout per node in seconds"
    )
    parser.add_argument(
        "--batch-timeout",
        type=int,
        default=600,
        help="Timeout per batch in seconds"
    )

    # Logging
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level"
    )

    args = parser.parse_args()
    # setup_logging(level=args.log_level, simple_format=True)

    builder = RegistryCacheBuilder(
        concurrency=args.concurrency,
        checkpoint_interval=args.checkpoint_interval,
        node_timeout=args.node_timeout,
        batch_timeout=args.batch_timeout,
        max_versions=args.max_versions,
        nodes_per_page=args.nodes_per_page
    )

    asyncio.run(
        builder.build_cache(
            output_file=args.output,
            input_cache=args.input,
            fetch_nodes=not args.no_nodes,
            fetch_versions=not args.no_versions,
            fetch_metadata=not args.no_metadata,
            pages=args.pages
        )
    )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Registry update orchestrator - coordinates incremental data pipeline.
Single entry point for all registry data updates.
"""

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

from build_registry_cache import RegistryCacheBuilder
from build_global_mappings import GlobalMappingsBuilder
from augment_mappings import MappingsAugmenter
from fetch_manager_data import ManagerDataFetcher

import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class RegistryOrchestrator:
    """Orchestrates incremental registry data updates."""

    def __init__(self, data_dir: Path, schema_config: Path = None, concurrency: int = 8, checkpoint_interval: int = 25, max_versions: int = 1, rate_limit_delay: float = 0.1, max_retries: int = 3, force_metadata_refresh: bool = False):
        self.data_dir = data_dir
        self.cache_file = data_dir / "full_registry_cache.json"
        self.mappings_file = data_dir / "node_mappings.json"
        self.manager_file = data_dir / ".temp_extension-node-map.json"  # Temporary file
        self.community_file = Path("config/community_mappings.json")
        self.state_file = data_dir / ".update_state.json"
        self.stats: Dict[str, Any] = {"started_at": datetime.now().isoformat()}

        # Cache builder configuration
        self.concurrency = concurrency
        self.checkpoint_interval = checkpoint_interval
        self.max_versions = max_versions
        self.rate_limit_delay = rate_limit_delay
        self.max_retries = max_retries
        self.force_metadata_refresh = force_metadata_refresh
        self.schema_config = schema_config

        # Ensure data directory exists
        data_dir.mkdir(parents=True, exist_ok=True)

    async def run_update(self, incremental: bool = True) -> Dict[str, Any]:
        """Run complete registry update pipeline."""
        logger.info("🚀 Starting registry update pipeline")
        logger.info(f"Data directory: {self.data_dir}")
        logger.info(f"Incremental mode: {incremental}")

        try:
            # Step 1: Update registry cache
            await self._update_registry_cache(incremental)

            # Step 2: Fetch Manager data (GPL-3, temporary)
            await self._fetch_manager_data()

            # Step 3: Update mappings
            await self._update_mappings(incremental)

            # Step 4: Augment with Manager data
            await self._augment_mappings()

            # Step 5: Save state
            self._save_state()

            logger.info("✅ Registry update pipeline completed successfully")
            return self.stats

        except Exception as e:
            logger.error(f"❌ Pipeline failed: {e}")
            self.stats["error"] = str(e)
            raise
        finally:
            # Always clean up GPL-3 licensed Manager data
            self._cleanup_manager_data()

    async def _update_registry_cache(self, incremental: bool):
        """Update registry cache incrementally."""
        logger.info("📦 Updating registry cache")

        # Determine input file for incremental update
        input_cache = self.cache_file if incremental and self.cache_file.exists() else None

        # Build cache with all phases
        builder = RegistryCacheBuilder(
            concurrency=self.concurrency,
            checkpoint_interval=self.checkpoint_interval,
            max_versions=self.max_versions,
            rate_limit_delay=self.rate_limit_delay,
            max_retries=self.max_retries,
            force_metadata_refresh=self.force_metadata_refresh,
            nodes_per_page=200
        )

        cache_start = datetime.now()
        await builder.build_cache(
            output_file=self.cache_file,
            input_cache=input_cache,
            fetch_nodes=True,
            fetch_versions=True,
            fetch_metadata=True
        )

        # Update stats
        self.stats["cache_updated_at"] = datetime.now().isoformat()
        self.stats["cache_duration_seconds"] = (datetime.now() - cache_start).total_seconds()

        if self.cache_file.exists():
            cache_size_mb = self.cache_file.stat().st_size / 1024 / 1024
            self.stats["cache_size_mb"] = round(cache_size_mb, 2)

            # Get cache stats
            with open(self.cache_file) as f:
                cache_data = json.load(f)
            self.stats["total_packages"] = cache_data.get("node_count", 0)
            self.stats["total_versions"] = cache_data.get("versions_processed", 0)
            self.stats["total_metadata"] = cache_data.get("metadata_entries", 0)

    async def _fetch_manager_data(self):
        """Fetch latest ComfyUI Manager extension map (GPL-3, temporary)."""
        logger.info("🔗 Fetching Manager extension data (GPL-3, temporary use only)")

        fetcher = ManagerDataFetcher()
        success = await fetcher.fetch(self.manager_file, force=True)

        if success:
            # Read back the data for stats
            try:
                with open(self.manager_file) as f:
                    wrapped_data = json.load(f)
                extension_count = wrapped_data.get("extension_count", 0)
                self.stats["manager_extensions"] = extension_count
                self.stats["manager_fetched_at"] = datetime.now().isoformat()
                logger.info(f"✅ Fetched {extension_count} Manager extensions")
            except Exception as e:
                logger.warning(f"Could not read Manager data stats: {e}")
        else:
            logger.warning("❌ Failed to fetch Manager data")
            self.stats["manager_fetch_failed"] = True

    async def _update_mappings(self, incremental: bool):
        """Generate mappings from cache data."""
        logger.info("🗺️  Generating node mappings")

        # Note: incremental parameter reserved for future optimization
        # Currently using full rebuild for MVP simplicity and reliability

        if not self.cache_file.exists():
            raise FileNotFoundError(f"Cache file not found: {self.cache_file}")

        mappings_start = datetime.now()

        # Build mappings (always from scratch for now - simple approach)
        builder = GlobalMappingsBuilder()
        mappings_data = builder.build_mappings(self.cache_file)

        if mappings_data:
            # Atomic write
            temp_file = self.mappings_file.with_suffix('.tmp')
            with open(temp_file, 'w') as f:
                json.dump(mappings_data, f, indent=2)
            temp_file.replace(self.mappings_file)

            # Update stats
            self.stats["mappings_updated_at"] = datetime.now().isoformat()
            self.stats["mappings_duration_seconds"] = (datetime.now() - mappings_start).total_seconds()
            self.stats["total_signatures"] = mappings_data["stats"]["signatures"]

            mappings_size_mb = self.mappings_file.stat().st_size / 1024 / 1024
            self.stats["mappings_size_mb"] = round(mappings_size_mb, 2)
        else:
            raise RuntimeError("Failed to generate mappings")

    async def _augment_mappings(self):
        """Augment mappings with Manager data."""
        logger.info("🔄 Augmenting mappings with Manager and community fallback data")

        if not self.mappings_file.exists():
            logger.warning("Mappings file not found, skipping augmentation")
            return

        if not self.manager_file.exists():
            logger.warning("Manager data not found, skipping augmentation")
            return

        augment_start = datetime.now()

        # Run augmentation
        augmenter = MappingsAugmenter(self.mappings_file, self.manager_file, self.community_file)
        augmenter.load_data()
        augmenter.augment_mappings()
        augmenter.save_augmented_mappings(self.mappings_file, schema_config=self.schema_config)

        # Update stats
        self.stats["augmentation_completed_at"] = datetime.now().isoformat()
        self.stats["augmentation_duration_seconds"] = (datetime.now() - augment_start).total_seconds()
        self.stats["nodes_added_from_manager"] = augmenter.stats["nodes_added"]
        self.stats["nodes_added_from_community"] = augmenter.stats["community_nodes_added"]
        self.stats["synthetic_packages_created"] = len(augmenter.stats["synthetic_packages_created"])

        # Update final mappings size after augmentation
        if self.mappings_file.exists():
            mappings_size_mb = self.mappings_file.stat().st_size / 1024 / 1024
            self.stats["final_mappings_size_mb"] = round(mappings_size_mb, 2)

    def _save_state(self):
        """Save update state for tracking."""
        self.stats["completed_at"] = datetime.now().isoformat()

        state_data = {
            "last_update": self.stats,
            "files": {
                "cache": str(self.cache_file),
                "mappings": str(self.mappings_file),
                "manager": str(self.manager_file)
            }
        }

        with open(self.state_file, 'w') as f:
            json.dump(state_data, f, indent=2)

        logger.info(f"State saved to {self.state_file}")

    def _cleanup_manager_data(self):
        """Clean up GPL-3 licensed Manager data to prevent contamination."""
        if self.manager_file.exists():
            try:
                self.manager_file.unlink()
                logger.info("🧹 Cleaned up temporary GPL-3 Manager data")
            except Exception as e:
                logger.warning(f"Failed to cleanup Manager data: {e}")

        # Also clean up any .tmp files
        for tmp_file in self.data_dir.glob("*.tmp"):
            try:
                tmp_file.unlink()
            except Exception:
                pass


async def main():
    parser = argparse.ArgumentParser(description="Registry data update orchestrator")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="Data directory (default: data/)"
    )
    parser.add_argument(
        "--incremental",
        action="store_true",
        default=True,
        help="Perform incremental update (default: True)"
    )
    parser.add_argument(
        "--force-full",
        action="store_true",
        help="Force full rebuild (overrides --incremental)"
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="DEBUG",
        help="Logging level"
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="Number of concurrent requests (default: 5)"
    )
    parser.add_argument(
        "--checkpoint-interval",
        type=int,
        default=3000,
        help="Save checkpoint every N items (default: 3000)"
    )
    parser.add_argument(
        "--max-versions",
        type=int,
        default=1,
        help="Max versions to fetch metadata for (default: 1)"
    )
    parser.add_argument(
        "--metadata-override",
        action="store_true",
        help="Force refresh metadata even if already cached (recovery mode)"
    )
    parser.add_argument(
        "--rate-limit-delay",
        type=float,
        default=0.1,
        help="Delay in seconds between version requests to avoid rate limiting (default: 0.1)"
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Max retry attempts for rate-limited requests with exponential backoff (default: 3)"
    )
    parser.add_argument(
        "--schema-config",
        type=Path,
        default=Path("config/output_schema.toml"),
        help="Schema configuration file (default: config/output_schema.toml)"
    )

    args = parser.parse_args()

    # Set logging level
    logging.getLogger().setLevel(getattr(logging, args.log_level))

    # Determine incremental mode
    incremental = args.incremental and not args.force_full

    # Run orchestrator
    orchestrator = RegistryOrchestrator(
        data_dir=args.data_dir,
        schema_config=args.schema_config,
        concurrency=args.concurrency,
        checkpoint_interval=args.checkpoint_interval,
        max_versions=args.max_versions,
        rate_limit_delay=args.rate_limit_delay,
        max_retries=args.max_retries,
        force_metadata_refresh=args.metadata_override
    )
    stats = await orchestrator.run_update(incremental=incremental)

    # Print summary
    print("\n" + "=" * 60)
    print("📊 UPDATE SUMMARY")
    print("=" * 60)
    print(f"Duration: {(datetime.fromisoformat(stats['completed_at']) - datetime.fromisoformat(stats['started_at'])).total_seconds():.1f}s")
    print(f"Packages: {stats.get('total_packages', 0)}")
    print(f"Versions: {stats.get('total_versions', 0)}")
    print(f"Signatures: {stats.get('total_signatures', 0)}")
    print(f"Manager extensions: {stats.get('manager_extensions', 0)}")
    print(f"Synthetic packages: {stats.get('synthetic_packages_created', 0)}")
    print(f"Cache size: {stats.get('cache_size_mb', 0):.1f} MB")
    print(f"Mappings size (before): {stats.get('mappings_size_mb', 0):.1f} MB")
    print(f"Mappings size (final): {stats.get('final_mappings_size_mb', 0):.1f} MB")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/env python3
"""Schema-based filtering for node mappings output.

Filters node_mappings.json output based on TOML configuration to reduce file size
by excluding unused fields. Uses Python 3.11+ stdlib tomllib for TOML parsing.
"""

import tomllib
from pathlib import Path
from typing import Dict, Any
from logging import getLogger

logger = getLogger(__name__)


class SchemaFilter:
    """Filters node mappings output based on schema configuration."""

    def __init__(self, config_path: Path):
        """Initialize filter with schema configuration.

        Args:
            config_path: Path to schema TOML file
        """
        self.config_path = config_path
        self.config = None

        if not config_path.exists():
            logger.warning(f"Schema config not found: {config_path}, will not filter output")
            return

        try:
            with open(config_path, 'rb') as f:
                self.config = tomllib.load(f)
            logger.info(f"Loaded schema config from {config_path}")
        except Exception as e:
            logger.error(f"Failed to load schema config: {e}, will not filter output")
            self.config = None

    def filter_mappings_output(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Filter complete mappings output structure.

        Args:
            data: Full mappings output dict

        Returns:
            Filtered mappings output (or unfiltered if config missing)
        """
        if not self.config:
            return data

        # Preserve top-level structure, filter nested sections
        filtered = {
            "version": data.get("version"),
            "generated_at": data.get("generated_at"),
            "stats": data.get("stats"),
            "package_aliases": data.get("package_aliases", {}),
            "mappings": self.filter_mappings_section(data.get("mappings", {})),
            "packages": self.filter_packages_section(data.get("packages", {}))
        }

        return filtered

    def filter_packages_section(self, packages: Dict[str, Any]) -> Dict[str, Any]:
        """Filter packages dictionary.

        Args:
            packages: Dict of package_id -> package data

        Returns:
            Filtered packages dict
        """
        if not self.config:
            return packages

        return {
            pkg_id: self.filter_package(pkg_data)
            for pkg_id, pkg_data in packages.items()
        }

    def filter_package(self, package: Dict[str, Any]) -> Dict[str, Any]:
        """Filter single package based on schema config.

        Args:
            package: Package data dict

        Returns:
            Filtered package dict
        """
        if not self.config:
            return package

        enabled_fields = self.config.get('packages', {})
        filtered = {}

        for field, value in package.items():
            # Special handling for nested versions dict
            if field == 'versions':
                if enabled_fields.get('versions', True):
                    filtered['versions'] = self.filter_versions_dict(value)
            elif enabled_fields.get(field, True):  # Default to include
                filtered[field] = value

        return filtered

    def filter_versions_dict(self, versions: Dict[str, Any]) -> Dict[str, Any]:
        """Filter versions dictionary.

        Args:
            versions: Dict of version -> version data

        Returns:
            Filtered versions dict
        """
        if not self.config:
            return versions

        return {
            version_key: self.filter_version(version_data)
            for version_key, version_data in versions.items()
        }

    def filter_version(self, version: Dict[str, Any]) -> Dict[str, Any]:
        """Filter single version based on schema config.

        Args:
            version: Version data dict

        Returns:
            Filtered version dict
        """
        if not self.config:
            return version

        enabled_fields = self.config.get('versions', {})

        return {
            field: value
            for field, value in version.items()
            if enabled_fields.get(field, True)  # Default to include
        }

    def filter_mappings_section(self, mappings: Dict[str, Any]) -> Dict[str, Any]:
        """Filter mappings dictionary.

        Args:
            mappings: Dict of node_key -> list of mapping entries

        Returns:
            Filtered mappings dict
        """
        if not self.config:
            return mappings

        return {
            node_key: [self.filter_mapping(entry) for entry in entries]
            for node_key, entries in mappings.items()
        }

    def filter_mapping(self, mapping: Dict[str, Any]) -> Dict[str, Any]:
        """Filter single mapping entry based on schema config.

        Args:
            mapping: Mapping entry dict

        Returns:
            Filtered mapping dict
        """
        if not self.config:
            return mapping

        enabled_fields = self.config.get('mappings', {})

        return {
            field: value
            for field, value in mapping.items()
            if enabled_fields.get(field, True)  # Default to include
        }

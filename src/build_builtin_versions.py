#!/usr/bin/env python3
"""Build a version-indexed ComfyUI builtin node database."""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from comfygit_core.utils.builtin_extractor import _extract_nodes_from_comfyui

logger = logging.getLogger(__name__)

SEMVER_TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
CATEGORY_PRECEDENCE = {
    "core": 0,
    "extras": 1,
    "api": 2,
    "custom": 3,
    "frontend": 4,
}


@dataclass
class NodeState:
    """Presence state for a builtin node across ComfyUI versions."""

    category: str
    intervals: list[tuple[int, int | None]]


def parse_semver_tag(tag: str) -> tuple[int, int, int] | None:
    """Parse `vX.Y.Z` tags into sortable tuples."""
    match = SEMVER_TAG_RE.match(tag.strip())
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def sort_semver_tags(tags: Iterable[str]) -> list[str]:
    """Return unique semver tags sorted oldest -> newest."""
    parsed: list[tuple[tuple[int, int, int], str]] = []
    for tag in set(tags):
        version = parse_semver_tag(tag)
        if version is not None:
            parsed.append((version, tag))
    parsed.sort(key=lambda x: x[0])
    return [tag for _, tag in parsed]


def _git_command(args: list[str], cwd: Path | None = None) -> str:
    """Run a git command and return stdout."""
    result = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise RuntimeError(f"Command failed: {' '.join(args)}\n{stderr}")
    return result.stdout


def list_remote_semver_tags(repo_url: str, min_version: str) -> list[str]:
    """List remote ComfyUI semver tags >= min_version."""
    min_tuple = parse_semver_tag(min_version)
    if min_tuple is None:
        raise ValueError(f"Invalid min version tag: {min_version}")

    output = _git_command(["git", "ls-remote", "--tags", "--refs", repo_url])
    remote_tags = []
    for line in output.splitlines():
        parts = line.strip().split("\t")
        if len(parts) != 2:
            continue
        ref = parts[1]
        if not ref.startswith("refs/tags/"):
            continue
        tag = ref.replace("refs/tags/", "", 1)
        parsed = parse_semver_tag(tag)
        if parsed and parsed >= min_tuple:
            remote_tags.append(tag)

    tags = sort_semver_tags(remote_tags)
    if not tags:
        raise RuntimeError("No matching ComfyUI tags found on remote")
    return tags


def _parse_present_range(
    range_spec: str,
    version_to_index: dict[str, int],
) -> tuple[int, int | None]:
    """Parse `present_in` range syntax into interval indices."""
    if range_spec.endswith("+"):
        start_version = range_spec[:-1]
        if start_version not in version_to_index:
            raise ValueError(f"Unknown present_in version: {start_version}")
        return (version_to_index[start_version], None)

    parts = range_spec.split("-", 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid present_in range: {range_spec}")
    start_version, end_version = parts
    if start_version not in version_to_index or end_version not in version_to_index:
        raise ValueError(f"Unknown present_in range: {range_spec}")
    start_idx = version_to_index[start_version]
    end_idx = version_to_index[end_version]
    if start_idx > end_idx:
        raise ValueError(f"Invalid present_in range order: {range_spec}")
    return (start_idx, end_idx)


def _entry_to_intervals(
    node_name: str,
    entry: dict,
    version_to_index: dict[str, int],
) -> list[tuple[int, int | None]]:
    """Convert persisted builtin entry fields into interval list."""
    if "present_in" in entry:
        return [_parse_present_range(spec, version_to_index) for spec in entry["present_in"]]

    introduced_in = entry.get("introduced_in")
    if introduced_in not in version_to_index:
        raise ValueError(f"{node_name}: unknown introduced_in {introduced_in}")
    start_idx = version_to_index[introduced_in]

    removed_in = entry.get("removed_in")
    if removed_in:
        if removed_in not in version_to_index:
            raise ValueError(f"{node_name}: unknown removed_in {removed_in}")
        removed_idx = version_to_index[removed_in]
        end_idx = removed_idx - 1
        if end_idx < start_idx:
            raise ValueError(f"{node_name}: removed_in precedes introduced_in")
        return [(start_idx, end_idx)]

    return [(start_idx, None)]


def load_existing_output(
    output_path: Path,
) -> tuple[list[str], dict[str, NodeState]]:
    """Load existing JSON output into mutable interval states."""
    if not output_path.exists():
        return [], {}

    with open(output_path, encoding="utf-8") as f:
        data = json.load(f)

    versions = data.get("comfyui_versions_processed", [])
    builtins = data.get("builtins", {})
    version_to_index = {version: idx for idx, version in enumerate(versions)}

    node_states: dict[str, NodeState] = {}
    for node_name, entry in builtins.items():
        category = entry.get("category", "frontend")
        intervals = _entry_to_intervals(node_name, entry, version_to_index)
        node_states[node_name] = NodeState(category=category, intervals=intervals)

    return versions, node_states


def _select_category(categories: set[str]) -> str:
    """Choose a deterministic category when node appears in multiple categories."""
    if not categories:
        return "frontend"
    return min(categories, key=lambda c: CATEGORY_PRECEDENCE.get(c, 999))


def _collect_nodes_from_extraction(extracted: dict) -> tuple[set[str], dict[str, str]]:
    """Convert extractor category output to node set and node->category map."""
    node_categories: dict[str, set[str]] = {}
    for category_name, category_data in extracted.items():
        nodes = category_data.get("nodes", [])
        for node_name in nodes:
            node_categories.setdefault(node_name, set()).add(category_name)

    node_set = set(node_categories.keys())
    category_map = {
        node_name: _select_category(categories)
        for node_name, categories in node_categories.items()
    }
    return node_set, category_map


def _node_present_at(state: NodeState, version_index: int) -> bool:
    """Check whether a node is present at a version index."""
    for start_idx, end_idx in state.intervals:
        if version_index < start_idx:
            continue
        if end_idx is None or version_index <= end_idx:
            return True
    return False


def _update_states_for_version(
    node_states: dict[str, NodeState],
    node_set: set[str],
    category_map: dict[str, str],
    current_index: int,
) -> None:
    """Update node intervals for the next sequential ComfyUI version."""
    existing_names = list(node_states.keys())
    for node_name in existing_names:
        state = node_states[node_name]
        start_idx, end_idx = state.intervals[-1]
        is_present = node_name in node_set

        if end_idx is None and not is_present:
            state.intervals[-1] = (start_idx, current_index - 1)
        elif end_idx is not None and is_present:
            state.intervals.append((current_index, None))

    for node_name in node_set:
        if node_name not in node_states:
            node_states[node_name] = NodeState(
                category=category_map.get(node_name, "frontend"),
                intervals=[(current_index, None)],
            )


def _needs_full_rebuild(remote_tags: list[str], existing_versions: list[str]) -> bool:
    """Return True when non-append updates require full recomputation."""
    if not existing_versions:
        return False

    remote_index = {tag: idx for idx, tag in enumerate(remote_tags)}
    last_idx = -1
    for tag in existing_versions:
        idx = remote_index.get(tag)
        if idx is None or idx <= last_idx:
            return True
        last_idx = idx

    existing_set = set(existing_versions)
    missing = [tag for tag in remote_tags if tag not in existing_set]
    if not missing:
        return False

    newest_existing = parse_semver_tag(existing_versions[-1])
    if newest_existing is None:
        return True

    for tag in missing:
        parsed = parse_semver_tag(tag)
        if parsed and parsed <= newest_existing:
            return True

    return False


def _extract_for_tag(repo_path: Path, tag: str) -> tuple[set[str], dict[str, str]]:
    """Check out a tag and extract builtin nodes."""
    _git_command(["git", "checkout", "--force", "--quiet", tag], cwd=repo_path)
    extracted, errors = _extract_nodes_from_comfyui(repo_path)
    if errors:
        logger.warning("Tag %s extraction had %s parser errors", tag, len(errors))
    return _collect_nodes_from_extraction(extracted)


def _count_nodes_added_in_latest(
    versions: list[str],
    node_states: dict[str, NodeState],
) -> int:
    """Count nodes that appear in latest version but not previous version."""
    if not versions:
        return 0

    latest_idx = len(versions) - 1
    prev_idx = latest_idx - 1
    added_count = 0
    for state in node_states.values():
        if not _node_present_at(state, latest_idx):
            continue
        if prev_idx < 0 or not _node_present_at(state, prev_idx):
            added_count += 1
    return added_count


def _serialize_builtins(
    versions: list[str],
    node_states: dict[str, NodeState],
) -> dict[str, dict]:
    """Serialize node interval states back to output schema."""
    builtins: dict[str, dict] = {}
    for node_name in sorted(node_states):
        state = node_states[node_name]
        intervals = state.intervals

        entry: dict[str, object] = {
            "introduced_in": versions[intervals[0][0]],
            "category": state.category,
        }

        if len(intervals) == 1:
            _, end_idx = intervals[0]
            if end_idx is not None:
                removed_idx = end_idx + 1
                if removed_idx < len(versions):
                    entry["removed_in"] = versions[removed_idx]
        else:
            present_ranges = []
            for start_idx, end_idx in intervals:
                if end_idx is None:
                    present_ranges.append(f"{versions[start_idx]}+")
                else:
                    present_ranges.append(f"{versions[start_idx]}-{versions[end_idx]}")
            entry["present_in"] = present_ranges

        builtins[node_name] = entry

    return builtins


def _write_output(output_path: Path, payload: dict) -> None:
    """Write output JSON atomically."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(f"{output_path.suffix}.tmp")
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    temp_path.replace(output_path)


@contextmanager
def prepared_comfyui_repo(repo_url: str, repo_path: Path | None):
    """Provide a local ComfyUI git checkout for extraction."""
    if repo_path:
        if not repo_path.exists():
            raise FileNotFoundError(f"ComfyUI repo path does not exist: {repo_path}")
        _git_command(["git", "fetch", "--tags", "--quiet"], cwd=repo_path)
        yield repo_path
        return

    with tempfile.TemporaryDirectory(prefix="comfyui-tags-") as temp_dir:
        local_path = Path(temp_dir) / "ComfyUI"
        _git_command(["git", "clone", "--quiet", "--no-checkout", repo_url, str(local_path)])
        yield local_path


def build_builtin_versions_database(
    output_path: Path,
    repo_url: str,
    min_version: str,
    repo_path: Path | None = None,
    force_full: bool = False,
) -> bool:
    """Build or incrementally update the builtin version database."""
    remote_tags = list_remote_semver_tags(repo_url, min_version)
    existing_versions, node_states = load_existing_output(output_path)

    full_rebuild = force_full or _needs_full_rebuild(remote_tags, existing_versions)
    if full_rebuild and existing_versions:
        logger.info("Detected non-append tag changes; rebuilding builtins database from scratch")
        existing_versions = []
        node_states = {}

    existing_set = set(existing_versions)
    tags_to_process = remote_tags if full_rebuild else [tag for tag in remote_tags if tag not in existing_set]

    if not tags_to_process and output_path.exists():
        logger.info("No new ComfyUI tags to process")
        return False

    logger.info("Processing %s ComfyUI version(s)", len(tags_to_process))

    with prepared_comfyui_repo(repo_url=repo_url, repo_path=repo_path) as local_repo:
        for tag in tags_to_process:
            logger.info("Extracting builtins for %s", tag)
            node_set, category_map = _extract_for_tag(local_repo, tag)
            existing_versions.append(tag)
            current_index = len(existing_versions) - 1
            _update_states_for_version(
                node_states=node_states,
                node_set=node_set,
                category_map=category_map,
                current_index=current_index,
            )

    builtins = _serialize_builtins(existing_versions, node_states)
    payload = {
        "version": datetime.now(timezone.utc).strftime("%Y.%m.%d"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "comfyui_versions_processed": existing_versions,
        "stats": {
            "total_builtins": len(builtins),
            "versions_processed": len(existing_versions),
            "newest_version": existing_versions[-1] if existing_versions else None,
            "nodes_added_in_latest": _count_nodes_added_in_latest(existing_versions, node_states),
        },
        "builtins": builtins,
    }
    _write_output(output_path, payload)
    logger.info(
        "Wrote %s builtins across %s ComfyUI versions to %s",
        payload["stats"]["total_builtins"],
        payload["stats"]["versions_processed"],
        output_path,
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build version-indexed ComfyUI builtins JSON",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("config/comfyui_builtins_by_version.json"),
        help="Output JSON file path",
    )
    parser.add_argument(
        "--repo-url",
        default="https://github.com/comfyanonymous/ComfyUI.git",
        help="ComfyUI git repository URL",
    )
    parser.add_argument(
        "--repo-path",
        type=Path,
        help="Optional existing local ComfyUI checkout to reuse",
    )
    parser.add_argument(
        "--min-version",
        default="v0.3.0",
        help="Minimum ComfyUI tag to include (format: vX.Y.Z)",
    )
    parser.add_argument(
        "--force-full",
        action="store_true",
        help="Force a full rebuild instead of incremental processing",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    changed = build_builtin_versions_database(
        output_path=args.output,
        repo_url=args.repo_url,
        min_version=args.min_version,
        repo_path=args.repo_path,
        force_full=args.force_full,
    )

    if changed:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

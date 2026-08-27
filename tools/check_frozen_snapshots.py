#!/usr/bin/env python3
"""Discover and validate every frozen AHP snapshot."""

from __future__ import annotations

import argparse
import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable

import check_conformance

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_ROOTS = ("spec", "schema", "fixtures", "conformance")
DATE_VERSION_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


class FrozenSnapshotFailure(Exception):
    """Raised when frozen snapshot discovery cannot be trusted."""


@dataclass(frozen=True)
class GitTreeEntry:
    mode: str
    object_id: str


def is_date_version(value: str) -> bool:
    if DATE_VERSION_RE.fullmatch(value) is None:
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def run_git(root: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", os.fspath(root), *args],
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise FrozenSnapshotFailure(f"cannot run Git: {exc}") from exc
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise FrozenSnapshotFailure(
            f"Git command failed ({' '.join(args)}): {detail or 'unknown error'}"
        )
    return result.stdout


def resolve_revision(root: Path, revision: str) -> str:
    if not revision:
        raise FrozenSnapshotFailure("Git revision must not be empty")
    output = run_git(root, "rev-parse", "--verify", f"{revision}^{{commit}}")
    return output.decode("ascii").strip()


def released_snapshot_entries(
    root: Path,
    revision: str,
) -> tuple[str, dict[str, GitTreeEntry], set[str], set[tuple[str, str]]]:
    commit = resolve_revision(root, revision)
    output = run_git(
        root,
        "ls-tree",
        "-r",
        "--full-tree",
        "-z",
        commit,
        "--",
        *SNAPSHOT_ROOTS,
    )
    entries: dict[str, GitTreeEntry] = {}
    versions: set[str] = set()
    version_roots: set[tuple[str, str]] = set()
    for record in output.split(b"\0"):
        if not record:
            continue
        metadata, separator, raw_path = record.partition(b"\t")
        if not separator:
            raise FrozenSnapshotFailure("cannot parse Git tree entry")
        try:
            mode, object_type, object_id = metadata.decode("ascii").split()
            path = raw_path.decode("utf-8", errors="surrogateescape")
        except ValueError as exc:
            raise FrozenSnapshotFailure("cannot parse Git tree entry") from exc
        parts = path.split("/")
        if (
            len(parts) < 3
            or parts[0] not in SNAPSHOT_ROOTS
            or not is_date_version(parts[1])
        ):
            continue
        if object_type != "blob":
            raise FrozenSnapshotFailure(
                f"unsupported Git tree entry in frozen snapshot: {path} ({object_type})"
            )
        entries[path] = GitTreeEntry(mode=mode, object_id=object_id)
        versions.add(parts[1])
        version_roots.add((parts[0], parts[1]))
    return commit, entries, versions, version_roots


def reachable_release_tags(root: Path, revision: str = "HEAD") -> tuple[str, ...]:
    output = run_git(root, "tag", "--merged", revision, "--list")
    tags: list[str] = []
    for raw_tag in output.decode("utf-8").splitlines():
        tag = raw_tag.strip()
        if not tag:
            continue
        if DATE_VERSION_RE.fullmatch(tag) is not None:
            if not is_date_version(tag):
                raise FrozenSnapshotFailure(
                    f"reachable release tag {tag!r} is not a valid calendar date"
                )
            tags.append(tag)
    return tuple(sorted(tags))


def newest_reachable_release_tag(
    root: Path,
    revision: str = "HEAD",
) -> str | None:
    tags = reachable_release_tags(root, revision)
    return tags[-1] if tags else None


def current_tree_entry(root: Path, path: Path) -> GitTreeEntry:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode):
        mode = "120000"
        content = os.fsencode(os.readlink(path))
    elif stat.S_ISREG(metadata.st_mode):
        mode = "100755" if metadata.st_mode & stat.S_IXUSR else "100644"
        content = path.read_bytes()
    else:
        raise FrozenSnapshotFailure(
            f"unsupported file type in frozen snapshot: {path.relative_to(root)}"
        )
    object_id = run_git(root, "hash-object", "--stdin", input_bytes=content)
    return GitTreeEntry(mode=mode, object_id=object_id.decode("ascii").strip())


def current_snapshot_entries(
    root: Path,
    versions: set[str],
) -> dict[str, GitTreeEntry]:
    entries: dict[str, GitTreeEntry] = {}
    for root_name in SNAPSHOT_ROOTS:
        for version in versions:
            snapshot_root = root / root_name / version
            if not snapshot_root.is_dir():
                continue
            for path in snapshot_root.rglob("*"):
                if not (path.is_file() or path.is_symlink()):
                    continue
                relative = path.relative_to(root).as_posix()
                entries[relative] = current_tree_entry(root, path)
    return entries


def check_immutable_release(root: Path, release_tag: str) -> list[str]:
    commit, baseline, versions, version_roots = released_snapshot_entries(
        root, release_tag
    )
    missing_release_roots = [
        f"{root_name}/{release_tag}"
        for root_name in SNAPSHOT_ROOTS
        if (root_name, release_tag) not in version_roots
    ]
    if missing_release_roots:
        raise FrozenSnapshotFailure(
            f"release tag {release_tag!r} at {commit} does not contain its complete "
            "date snapshot: " + ", ".join(missing_release_roots)
        )
    current = current_snapshot_entries(root, versions)
    errors: list[str] = []

    baseline_paths = set(baseline)
    current_paths = set(current)
    for path in sorted(baseline_paths - current_paths):
        errors.append(
            f"{path}: deleted from frozen snapshot released at {release_tag} ({commit})"
        )
    for path in sorted(current_paths - baseline_paths):
        errors.append(
            f"{path}: added to frozen snapshot released at {release_tag} ({commit})"
        )
    for path in sorted(baseline_paths & current_paths):
        if baseline[path] != current[path]:
            errors.append(
                f"{path}: modified from frozen snapshot released at "
                f"{release_tag} ({commit})"
            )
    return errors


def discover_frozen_snapshots(root: Path = ROOT) -> tuple[str, ...]:
    snapshots: set[str] = set()
    for root_name in SNAPSHOT_ROOTS:
        snapshot_root = root / root_name
        try:
            directories = sorted(
                path for path in snapshot_root.iterdir() if path.is_dir()
            )
        except OSError as exc:
            raise FrozenSnapshotFailure(
                f"cannot inspect snapshot root {snapshot_root}: {exc}"
            ) from exc

        for path in directories:
            snapshot = path.name
            if snapshot == "draft":
                continue
            if not is_date_version(snapshot):
                try:
                    label = path.relative_to(root).as_posix()
                except ValueError:
                    label = path.as_posix()
                raise FrozenSnapshotFailure(
                    f"::error title=Invalid snapshot directory::{label}/ "
                    "must use a valid YYYY-MM-DD calendar date"
                )
            snapshots.add(snapshot)

    return tuple(sorted(snapshots))


def run(
    root: Path = ROOT,
    *,
    revision: str = "HEAD",
    check_snapshot: Callable[[Path, str], check_conformance.CheckResult] = (
        check_conformance.run_checks
    ),
    stdout: Any = None,
    stderr: Any = None,
) -> int:
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    try:
        snapshots = discover_frozen_snapshots(root)
        release_tag = newest_reachable_release_tag(root, revision)
    except FrozenSnapshotFailure as exc:
        print(exc, file=stderr)
        return 1

    if release_tag is not None:
        try:
            immutable_errors = check_immutable_release(root, release_tag)
        except FrozenSnapshotFailure as exc:
            print(exc, file=stderr)
            return 1
        if immutable_errors:
            print(
                f"frozen snapshot immutability checks failed ({len(immutable_errors)}):",
                file=stderr,
            )
            for error in immutable_errors:
                print(f"- {error}", file=stderr)
            return 1
    else:
        print(
            "no reachable YYYY-MM-DD release tag; "
            "validating first-release snapshot set",
            file=stdout,
        )

    for snapshot in snapshots:
        result = check_snapshot(root, snapshot)
        if check_conformance.report_result(
            result,
            stdout=stdout,
            stderr=stderr,
        ):
            return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--revision",
        default="HEAD",
        help="revision whose newest reachable YYYY-MM-DD release tag is authoritative",
    )
    args = parser.parse_args()
    return run(revision=args.revision)


if __name__ == "__main__":
    raise SystemExit(main())

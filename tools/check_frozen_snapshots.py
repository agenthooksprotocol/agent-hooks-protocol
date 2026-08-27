#!/usr/bin/env python3
"""Discover and validate every frozen AHP snapshot."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Callable

import check_conformance

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_ROOTS = ("spec", "schema", "fixtures", "conformance")
EXACT_SEMVER_RE = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
)


class FrozenSnapshotFailure(Exception):
    """Raised when frozen snapshot discovery cannot be trusted."""


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
            if EXACT_SEMVER_RE.fullmatch(snapshot) is None:
                try:
                    label = path.relative_to(root).as_posix()
                except ValueError:
                    label = path.as_posix()
                raise FrozenSnapshotFailure(
                    f"::error title=Invalid snapshot directory::{label}/ "
                    "must use exact MAJOR.MINOR.PATCH"
                )
            snapshots.add(snapshot)

    return tuple(sorted(snapshots))


def run(
    root: Path = ROOT,
    *,
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
    except FrozenSnapshotFailure as exc:
        print(exc, file=stderr)
        return 1

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
    return run()


if __name__ == "__main__":
    raise SystemExit(main())

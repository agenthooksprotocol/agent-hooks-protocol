#!/usr/bin/env python3
"""Create a validated date-stamped AHP snapshot from the mutable draft."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

import check_conformance

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_ROOTS = ("spec", "schema", "fixtures", "conformance")


class FreezeFailure(Exception):
    """Raised when a draft cannot be frozen safely."""


def load_json(path: Path) -> Any:
    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=check_conformance.object_without_duplicates,
    )


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def retarget_path(value: object, root_name: str, version: str) -> str:
    prefix = f"{root_name}/draft/"
    if not isinstance(value, str) or not value.startswith(prefix):
        raise FreezeFailure(
            f"unexpected {root_name} draft path while freezing: {value!r}"
        )
    return f"{root_name}/{version}/{value.removeprefix(prefix)}"


def replace_required(text: str, old: str, new: str, path: Path) -> str:
    if old not in text:
        raise FreezeFailure(f"{path}: missing release marker {old!r}")
    return text.replace(old, new)


def retarget_spec(staging: Path, version: str) -> None:
    spec_dir = staging / "spec" / version
    requirements_path = spec_dir / "requirements.json"
    requirements = load_json(requirements_path)
    requirements["status"] = "Published"
    requirements["snapshotVersion"] = version
    requirements["protocolVersion"] = version
    for requirement in requirements["requirements"]:
        requirement["document"] = retarget_path(
            requirement.get("document"), "spec", version
        )
    write_json(requirements_path, requirements)

    for path in spec_dir.rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            '"protocolVersion": "draft"', f'"protocolVersion": "{version}"'
        )
        text = text.replace('"supported": ["draft"]', f'"supported": ["{version}"]')
        text = text.replace(
            "protocol version `draft`", f"protocol version `{version}`"
        )
        if path == spec_dir / "index.md":
            text = replace_required(
                text,
                "**Status:** Working Draft (`draft`)",
                f"**Status:** Published Protocol (`{version}`)",
                path,
            )
            text = replace_required(
                text,
                "**Canonical draft:** This document",
                "**Canonical version:** This document",
                path,
            )
            text = replace_required(
                text,
                "This is the canonical, language-neutral Working Draft specification. "
                "It is not a final standard and must not be represented as stable.",
                "This is the canonical, language-neutral published protocol "
                f"specification for version `{version}`.",
                path,
            )
            text = replace_required(
                text,
                "**Protocol version:** `draft`",
                f"**Protocol version:** `{version}`",
                path,
            )
            text = replace_required(
                text,
                "This document is a Working Draft specification, not a final standard.",
                "This document is the published AHP protocol specification for "
                f"version `{version}`.",
                path,
            )
        if path == spec_dir / "changelog.md":
            text = replace_required(
                text,
                "No published snapshots exist yet.",
                f"This changelog accompanies published protocol version `{version}`.",
                path,
            )
        path.write_text(text, encoding="utf-8")


def retarget_schema(staging: Path, version: str) -> None:
    schema_dir = staging / "schema" / version
    manifest_path = schema_dir / "manifest.json"
    manifest = load_json(manifest_path)

    for path in schema_dir.glob("*.json"):
        if path == manifest_path:
            continue
        schema = load_json(path)
        expected_id = f"https://agenthooksprotocol.org/schemas/draft/{path.name}"
        if schema.get("$id") != expected_id:
            raise FreezeFailure(f"{path}: unexpected draft $id {schema.get('$id')!r}")
        schema["$id"] = (
            f"https://agenthooksprotocol.org/schemas/{version}/{path.name}"
        )
        title = schema.get("title")
        if not isinstance(title, str) or "(Draft)" not in title:
            raise FreezeFailure(f"{path}: missing Draft title marker")
        schema["title"] = title.replace("(Draft)", f"({version})")
        comment = schema.get("$comment")
        if not isinstance(comment, str) or "Mutable AHP draft" not in comment:
            raise FreezeFailure(f"{path}: missing mutable-draft comment marker")
        schema["$comment"] = comment.replace(
            "Mutable AHP draft", f"Published AHP protocol version {version}"
        )
        if path.name == "common.schema.json":
            protocol_version = schema["$defs"]["protocolVersion"]
            if protocol_version.get("const") != "draft":
                raise FreezeFailure(f"{path}: unexpected draft protocolVersion constant")
            protocol_version["const"] = version
        write_json(path, schema)

    manifest["status"] = "Published"
    manifest["snapshotVersion"] = version
    manifest["protocolVersion"] = version
    stable_names = manifest.get("sdkGeneration", {}).get("stableNames", {})
    retargeted_names: dict[str, str] = {}
    for source, name in stable_names.items():
        path, separator, pointer = source.partition("#")
        if not separator:
            raise FreezeFailure(
                f"{manifest_path}: SDK stable name source lacks #: {source!r}"
            )
        retargeted_names[
            f"{retarget_path(path, 'schema', version)}#{pointer}"
        ] = name
    manifest["sdkGeneration"]["stableNames"] = retargeted_names
    for document in manifest["documents"]:
        document["path"] = retarget_path(document.get("path"), "schema", version)
        document["sha256"] = sha256(staging / document["path"])
    write_json(manifest_path, manifest)


def retarget_fixtures(staging: Path, version: str) -> None:
    fixture_dir = staging / "fixtures" / version
    manifest_path = fixture_dir / "manifest.json"
    manifest = load_json(manifest_path)
    manifest["status"] = "Published"
    manifest["snapshotVersion"] = version
    manifest["protocolVersion"] = version

    for case in manifest["cases"]:
        draft_path = case.get("path")
        if not isinstance(draft_path, str):
            raise FreezeFailure(f"{manifest_path}: fixture path is not a string")
        published_path = retarget_path(draft_path, "fixtures", version)
        copied_path = staging / published_path
        text = copied_path.read_text(encoding="utf-8")
        text = text.replace(
            '"protocolVersion": "draft"', f'"protocolVersion": "{version}"'
        )
        text = text.replace(
            '"protocolVersion":"draft"', f'"protocolVersion":"{version}"'
        )
        text = text.replace('"supported": ["draft"]', f'"supported": ["{version}"]')
        copied_path.write_text(text, encoding="utf-8")

        case["path"] = published_path
        case["schema"] = retarget_path(case.get("schema"), "schema", version)
        if "eventSchema" in case:
            case["eventSchema"] = retarget_path(
                case.get("eventSchema"), "schema", version
            )
        case["sha256"] = sha256(copied_path)
    write_json(manifest_path, manifest)


def retarget_conformance(staging: Path, version: str) -> None:
    conformance_dir = staging / "conformance" / version
    manifest_path = conformance_dir / "manifest.json"
    manifest = load_json(manifest_path)
    manifest["status"] = "Published"
    manifest["snapshotVersion"] = version
    manifest["protocolVersion"] = version
    manifest["canonicalRequirements"] = retarget_path(
        manifest.get("canonicalRequirements"), "spec", version
    )

    for profile in manifest["profiles"]:
        draft_path = profile.get("path")
        if not isinstance(draft_path, str):
            raise FreezeFailure(f"{manifest_path}: profile path is not a string")
        published_path = retarget_path(draft_path, "conformance", version)
        copied_path = staging / published_path
        text = copied_path.read_text(encoding="utf-8")
        text = replace_required(
            text,
            "**Status: Working Draft — `draft`.**",
            f"**Status: Published Protocol — `{version}`.**",
            copied_path,
        )
        text = text.replace(
            'protocolVersion: "draft"', f'protocolVersion: "{version}"'
        )
        text = text.replace("protocol `draft`", f"protocol `{version}`")
        copied_path.write_text(text, encoding="utf-8")
        profile["path"] = published_path
        profile["sha256"] = sha256(copied_path)
    write_json(manifest_path, manifest)


def freeze_snapshot(root: Path, version: str) -> None:
    root = root.resolve()
    if not check_conformance.is_date_snapshot(version):
        raise FreezeFailure("release version must be a valid YYYY-MM-DD calendar date")

    draft_result = check_conformance.run_checks(root)
    if draft_result.errors:
        raise FreezeFailure(
            "draft conformance checks failed before freeze:\n- "
            + "\n- ".join(draft_result.errors)
        )

    existing = [
        root / root_name / version
        for root_name in SNAPSHOT_ROOTS
        if (root / root_name / version).exists()
    ]
    if existing:
        raise FreezeFailure(
            "release destination already exists: "
            + ", ".join(str(path.relative_to(root)) for path in existing)
        )

    with tempfile.TemporaryDirectory(prefix=".ahp-release-", dir=root) as temporary:
        staging = Path(temporary)
        for root_name in SNAPSHOT_ROOTS:
            destination = staging / root_name / version
            destination.parent.mkdir(parents=True)
            shutil.copytree(root / root_name / "draft", destination)

        retarget_spec(staging, version)
        retarget_schema(staging, version)
        retarget_fixtures(staging, version)
        retarget_conformance(staging, version)

        result = check_conformance.run_checks(staging, version)
        if result.errors:
            raise FreezeFailure(
                "date snapshot conformance checks failed:\n- "
                + "\n- ".join(result.errors)
            )

        for root_name in SNAPSHOT_ROOTS:
            shutil.move(
                staging / root_name / version,
                root / root_name / version,
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="publication date in YYYY-MM-DD form")
    args = parser.parse_args()
    try:
        freeze_snapshot(ROOT, args.version)
    except (
        FreezeFailure,
        check_conformance.CheckFailure,
        OSError,
        KeyError,
        TypeError,
        json.JSONDecodeError,
    ) as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"created validated AHP snapshot {args.version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

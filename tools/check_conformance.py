#!/usr/bin/env python3
"""Dependency-free structural and golden-fixture checks for AHP snapshots."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
DIALECT = "https://json-schema.org/draft/2020-12/schema"
REQ_RE = re.compile(r"\bAHP-[A-Z]+-\d{3}\b")
SNAPSHOT_RE = re.compile(r"(?:draft|(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))")
MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]]*\]\((?P<target>[^)]+)\)")
PRIVATE_NOTION_RE = re.compile(
    r"(?:https?://(?:www\.)?notion\.(?:so|site)(?:/|\b)|" + "notion" + r"://)",
    re.IGNORECASE,
)


class CheckFailure(Exception):
    """Raised for malformed checker inputs."""


@dataclass(frozen=True)
class Snapshot:
    root: Path
    key: str
    spec_dir: Path
    schema_dir: Path
    fixture_dir: Path
    conformance_dir: Path
    requirements_path: Path
    schema_manifest_path: Path
    fixture_manifest_path: Path
    conformance_manifest_path: Path
    draft_version: str
    protocol_version: str

    @classmethod
    def resolve(cls, root: Path, key: str = "draft") -> "Snapshot":
        root = root.resolve()
        if not isinstance(key, str) or SNAPSHOT_RE.fullmatch(key) is None:
            raise CheckFailure(f"invalid snapshot key: {key!r}")

        spec_dir = root / "spec" / key
        schema_dir = root / "schema" / key
        fixture_dir = root / "fixtures" / key
        conformance_dir = root / "conformance" / key
        roots = {
            "specification": spec_dir,
            "schema": schema_dir,
            "fixture": fixture_dir,
            "conformance": conformance_dir,
        }
        missing = [f"{label} root {path.relative_to(root)}" for label, path in roots.items() if not path.is_dir()]
        if missing:
            raise CheckFailure(f"snapshot {key!r} is incomplete; missing " + ", ".join(missing))

        requirements_path = spec_dir / "requirements.json"
        requirements = load_json(requirements_path, root)
        draft_version = requirements.get("draftVersion")
        protocol_version = requirements.get("protocolVersion")
        if not isinstance(draft_version, str) or not draft_version:
            raise CheckFailure(f"{requirements_path.relative_to(root)}: missing draftVersion")
        if not isinstance(protocol_version, str) or not protocol_version:
            raise CheckFailure(f"{requirements_path.relative_to(root)}: missing protocolVersion")

        return cls(
            root=root,
            key=key,
            spec_dir=spec_dir,
            schema_dir=schema_dir,
            fixture_dir=fixture_dir,
            conformance_dir=conformance_dir,
            requirements_path=requirements_path,
            schema_manifest_path=schema_dir / "manifest.json",
            fixture_manifest_path=fixture_dir / "manifest.json",
            conformance_manifest_path=conformance_dir / "manifest.json",
            draft_version=draft_version,
            protocol_version=protocol_version,
        )


def object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CheckFailure(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def relative_label(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def load_json(path: Path, root: Path = ROOT) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=object_without_duplicates)
    except (OSError, UnicodeError, json.JSONDecodeError, CheckFailure) as exc:
        raise CheckFailure(f"{relative_label(path, root)}: invalid JSON: {exc}") from exc


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def root_path(snapshot: Snapshot, value: str, *, within: Path | None = None) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise CheckFailure(f"invalid repository-relative path: {value!r}")
    parsed = urlparse(value)
    pure = PurePosixPath(value)
    if parsed.scheme or parsed.netloc or pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise CheckFailure(f"invalid repository-relative path: {value!r}")
    if pure.as_posix() != value:
        raise CheckFailure(f"non-canonical repository-relative path: {value!r}")
    candidate = (snapshot.root / pure).resolve()
    try:
        candidate.relative_to(snapshot.root)
    except ValueError as exc:
        raise CheckFailure(f"path escapes repository: {value}") from exc
    if within is not None:
        try:
            candidate.relative_to(within.resolve())
        except ValueError as exc:
            raise CheckFailure(
                f"path is outside selected {snapshot.key!r} snapshot root {within.relative_to(snapshot.root)}: {value}"
            ) from exc
    return candidate


def json_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    return left == right


def type_matches(instance: Any, expected: str) -> bool:
    return {
        "null": instance is None,
        "boolean": isinstance(instance, bool),
        "object": isinstance(instance, dict),
        "array": isinstance(instance, list),
        "string": isinstance(instance, str),
        "integer": isinstance(instance, int) and not isinstance(instance, bool),
        "number": isinstance(instance, (int, float)) and not isinstance(instance, bool),
    }.get(expected, False)


class SchemaStore:
    def __init__(self, snapshot: Snapshot) -> None:
        self.snapshot = snapshot
        self.cache: dict[Path, Any] = {}

    def load(self, path: Path) -> Any:
        path = path.resolve()
        if path not in self.cache:
            self.cache[path] = load_json(path, self.snapshot.root)
        return self.cache[path]

    def resolve(self, reference: str, current: Path) -> tuple[Any, Path]:
        file_part, separator, fragment = reference.partition("#")
        parsed = urlparse(file_part)
        if parsed.scheme or parsed.netloc or file_part.startswith("/"):
            raise CheckFailure(f"{current.relative_to(self.snapshot.root)}: non-offline $ref {reference!r}")
        if file_part and ".." in Path(unquote(file_part)).parts:
            raise CheckFailure(f"{current.relative_to(self.snapshot.root)}: parent traversal in $ref {reference!r}")
        target = (current.parent / unquote(file_part)).resolve() if file_part else current.resolve()
        try:
            target.relative_to(self.snapshot.schema_dir.resolve())
        except ValueError as exc:
            raise CheckFailure(
                f"{current.relative_to(self.snapshot.root)}: $ref leaves selected schema snapshot: {reference!r}"
            ) from exc
        document = self.load(target)
        if separator and fragment:
            if not fragment.startswith("/"):
                raise CheckFailure(
                    f"{current.relative_to(self.snapshot.root)}: unsupported non-pointer fragment: {reference!r}"
                )
            node = document
            for raw_token in fragment[1:].split("/"):
                token = unquote(raw_token).replace("~1", "/").replace("~0", "~")
                try:
                    node = node[int(token)] if isinstance(node, list) else node[token]
                except (KeyError, IndexError, ValueError, TypeError) as exc:
                    raise CheckFailure(
                        f"{current.relative_to(self.snapshot.root)}: unresolved $ref {reference!r}"
                    ) from exc
            return node, target
        return document, target


class SubsetValidator:
    """Small validator for the supported Draft 2020-12 keyword subset."""

    def __init__(self, store: SchemaStore) -> None:
        self.store = store

    def validate(self, instance: Any, schema: Any, schema_path: Path, at: str = "$") -> list[str]:
        if isinstance(schema, bool):
            return [] if schema else [f"{at}: rejected by false schema"]
        if not isinstance(schema, dict):
            return [f"{at}: schema is not an object or boolean"]

        errors: list[str] = []
        if "$ref" in schema:
            try:
                target, target_path = self.store.resolve(schema["$ref"], schema_path)
                errors.extend(self.validate(instance, target, target_path, at))
            except CheckFailure as exc:
                errors.append(str(exc))

        for index, child in enumerate(schema.get("allOf", [])):
            errors.extend(self.validate(instance, child, schema_path, at))

        if "anyOf" in schema:
            branches = [self.validate(instance, child, schema_path, at) for child in schema["anyOf"]]
            if not any(not branch for branch in branches):
                errors.append(f"{at}: does not match anyOf")

        if "oneOf" in schema:
            matches = sum(not self.validate(instance, child, schema_path, at) for child in schema["oneOf"])
            if matches != 1:
                errors.append(f"{at}: matches {matches} oneOf branches, expected 1")

        if "not" in schema and not self.validate(instance, schema["not"], schema_path, at):
            errors.append(f"{at}: matches forbidden schema")

        if "if" in schema:
            condition_matches = not self.validate(instance, schema["if"], schema_path, at)
            selected = schema.get("then") if condition_matches else schema.get("else")
            if selected is not None:
                errors.extend(self.validate(instance, selected, schema_path, at))

        if "const" in schema and not json_equal(instance, schema["const"]):
            errors.append(f"{at}: expected constant {schema['const']!r}")
        if "enum" in schema and not any(json_equal(instance, item) for item in schema["enum"]):
            errors.append(f"{at}: value is not in enum")

        if "type" in schema:
            expected = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
            if not any(type_matches(instance, item) for item in expected):
                return errors + [f"{at}: expected type {schema['type']!r}"]

        if isinstance(instance, dict):
            required = schema.get("required", [])
            for key in required:
                if key not in instance:
                    errors.append(f"{at}: missing required property {key!r}")
            properties = schema.get("properties", {})
            pattern_properties = schema.get("patternProperties", {})
            for key, value in instance.items():
                child_at = f"{at}.{key}"
                if key in properties:
                    errors.extend(self.validate(value, properties[key], schema_path, child_at))
                matched_pattern = False
                for pattern, child in pattern_properties.items():
                    if re.search(pattern, key):
                        matched_pattern = True
                        errors.extend(self.validate(value, child, schema_path, child_at))
                if schema.get("additionalProperties") is False and key not in properties and not matched_pattern:
                    errors.append(f"{child_at}: additional property is not allowed")
                elif isinstance(schema.get("additionalProperties"), dict) and key not in properties and not matched_pattern:
                    errors.extend(self.validate(value, schema["additionalProperties"], schema_path, child_at))
            if "propertyNames" in schema:
                for key in instance:
                    errors.extend(self.validate(key, schema["propertyNames"], schema_path, f"{at}.<propertyName>"))
            if len(instance) < schema.get("minProperties", 0):
                errors.append(f"{at}: too few properties")
            if "maxProperties" in schema and len(instance) > schema["maxProperties"]:
                errors.append(f"{at}: too many properties")

        if isinstance(instance, list):
            if len(instance) < schema.get("minItems", 0):
                errors.append(f"{at}: too few items")
            if "maxItems" in schema and len(instance) > schema["maxItems"]:
                errors.append(f"{at}: too many items")
            if schema.get("uniqueItems"):
                canonical = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
                if len(canonical) != len(set(canonical)):
                    errors.append(f"{at}: items are not unique")
            if "items" in schema:
                for index, item in enumerate(instance):
                    errors.extend(self.validate(item, schema["items"], schema_path, f"{at}[{index}]"))
            if "contains" in schema:
                count = sum(not self.validate(item, schema["contains"], schema_path, f"{at}[{index}]") for index, item in enumerate(instance))
                minimum = schema.get("minContains", 1)
                maximum = schema.get("maxContains")
                if count < minimum or (maximum is not None and count > maximum):
                    errors.append(f"{at}: contains matched {count} items")

        if isinstance(instance, str):
            if len(instance) < schema.get("minLength", 0):
                errors.append(f"{at}: string is too short")
            if "maxLength" in schema and len(instance) > schema["maxLength"]:
                errors.append(f"{at}: string is too long")
            if "pattern" in schema and re.search(schema["pattern"], instance) is None:
                errors.append(f"{at}: string does not match pattern")
            if schema.get("format") == "uri" and not urlparse(instance).scheme:
                errors.append(f"{at}: string is not an absolute URI")
            if schema.get("format") == "date-time":
                try:
                    parsed = datetime.fromisoformat(instance.replace("Z", "+00:00"))
                    if parsed.tzinfo is None:
                        raise ValueError("timezone required")
                except ValueError:
                    errors.append(f"{at}: string is not an RFC 3339 date-time")

        if isinstance(instance, (int, float)) and not isinstance(instance, bool):
            if "minimum" in schema and instance < schema["minimum"]:
                errors.append(f"{at}: number is below minimum")
            if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]:
                errors.append(f"{at}: number is not above exclusiveMinimum")
            if "maximum" in schema and instance > schema["maximum"]:
                errors.append(f"{at}: number is above maximum")

        return errors


def iter_schema_nodes(node: Any):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from iter_schema_nodes(value)
    elif isinstance(node, list):
        for value in node:
            yield from iter_schema_nodes(value)


def snapshot_schema_files(snapshot: Snapshot) -> list[Path]:
    files = set(snapshot.schema_dir.glob("*.schema.json"))
    aggregate = snapshot.schema_dir / "schema.json"
    if aggregate.is_file():
        files.add(aggregate)
    return sorted(files)


def check_schema_structure(snapshot: Snapshot, store: SchemaStore) -> list[str]:
    errors: list[str] = []
    ids: set[str] = set()
    schema_files = snapshot_schema_files(snapshot)
    for path in schema_files:
        try:
            schema = store.load(path)
        except CheckFailure as exc:
            errors.append(str(exc))
            continue
        label = path.relative_to(snapshot.root)
        if not isinstance(schema, dict):
            errors.append(f"{label}: schema root must be an object")
            continue
        if schema.get("$schema") != DIALECT:
            errors.append(f"{label}: must declare Draft 2020-12")
        if "Working Draft" not in schema.get("title", "") or "Working Draft" not in schema.get("$comment", ""):
            errors.append(f"{label}: missing Working Draft markers")
        expected_id = f"https://agenthooksprotocol.org/schemas/{snapshot.draft_version}/{path.name}"
        if schema.get("$id") != expected_id:
            errors.append(f"{label}: unexpected $id")
        if schema.get("$id") in ids:
            errors.append(f"{label}: duplicate $id")
        ids.add(schema.get("$id", ""))
        for node in iter_schema_nodes(schema):
            if "$ref" in node:
                if not isinstance(node["$ref"], str):
                    errors.append(f"{label}: $ref must be a string")
                else:
                    try:
                        store.resolve(node["$ref"], path)
                    except CheckFailure as exc:
                        errors.append(str(exc))
            for keyword in ("required", "enum", "allOf", "anyOf", "oneOf"):
                if keyword in node and not isinstance(node[keyword], list):
                    errors.append(f"{label}: {keyword} must be an array")
            for keyword in ("properties", "patternProperties", "$defs"):
                if keyword in node and not isinstance(node[keyword], dict):
                    errors.append(f"{label}: {keyword} must be an object")
    if not schema_files:
        errors.append("no schemas found")
    return errors


def semantic_errors(instance: Any, schema_path: Path) -> list[str]:
    errors: list[str] = []
    if (
        schema_path.name in {"intercept-request.schema.json", "schema.json"}
        and isinstance(instance, dict)
        and instance.get("method") == "hooks/intercept"
    ):
        try:
            if instance["id"] != instance["params"]["event"]["id"]:
                errors.append("$: JSON-RPC request id must equal params.event.id")
        except (KeyError, TypeError):
            pass
    if schema_path.name == "registration.schema.json" and isinstance(instance, dict):
        hooks = instance.get("hooks")
        if isinstance(hooks, list):
            ids = [hook.get("id") for hook in hooks if isinstance(hook, dict)]
            if len(ids) != len(set(ids)):
                errors.append("$.hooks: backend ids must be unique")
    return errors


def parse_fixture(case: dict[str, Any], path: Path) -> tuple[Any | None, list[str]]:
    errors: list[str] = []
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        return None, [f"cannot read UTF-8 fixture: {exc}"]
    binding = case.get("binding")
    if binding == "stdio-jsonl":
        if not raw.endswith(b"\n") or raw.endswith(b"\n\n") or b"\r" in raw:
            errors.append("stdio JSONL must end in exactly one LF newline")
        lines = text.splitlines()
        if len(lines) != 1:
            errors.append(f"stdio JSONL must contain exactly one physical line; found {len(lines)}")
            return None, errors
        source = lines[0]
    elif binding in {"http-json", "registration-json"}:
        source = text
    else:
        return None, [f"unknown fixture binding {binding!r}"]
    try:
        instance = json.loads(source, object_pairs_hook=object_without_duplicates)
    except (json.JSONDecodeError, CheckFailure) as exc:
        return None, errors + [f"invalid JSON: {exc}"]
    if not isinstance(instance, dict):
        errors.append("fixture must contain one JSON object")
    return instance, errors


def check_fixture_cases(
    snapshot: Snapshot, store: SchemaStore, fixture_manifest: dict[str, Any]
) -> tuple[list[str], int]:
    errors: list[str] = []
    cases = fixture_manifest.get("cases", [])
    seen_ids: set[str] = set()
    validator = SubsetValidator(store)
    aggregate_path = snapshot.schema_dir / "schema.json"
    aggregate = store.load(aggregate_path)
    aggregate_branches = aggregate.get("oneOf", []) if isinstance(aggregate, dict) else []
    aggregate_refs: set[str] = set()
    for branch in aggregate_branches:
        if not isinstance(branch, dict) or not isinstance(branch.get("$ref"), str):
            errors.append("aggregate schema oneOf branches must use local $refs")
            continue
        aggregate_refs.add(branch["$ref"])

    observe_path = snapshot.schema_dir / "observe-notification.schema.json"
    observe = store.load(observe_path)
    try:
        observe_event_branches = observe["allOf"][1]["properties"]["params"]["properties"]["event"]["oneOf"]
    except (KeyError, IndexError, TypeError):
        observe_event_branches = []
        errors.append("observe notification schema event union is missing")
    observe_event_refs: set[str] = set()
    for branch in observe_event_branches:
        if not isinstance(branch, dict) or not isinstance(branch.get("$ref"), str):
            errors.append("observe event oneOf branches must use local $refs")
            continue
        observe_event_refs.add(branch["$ref"])

    covered_aggregate_refs: set[str] = set()
    covered_observe_event_refs: set[str] = set()
    for case in cases:
        case_id = case.get("id", "<missing-id>")
        if case_id in seen_ids:
            errors.append(f"fixture manifest: duplicate case id {case_id}")
        seen_ids.add(case_id)
        fixture_path = root_path(snapshot, case["path"], within=snapshot.fixture_dir)
        schema_path = root_path(snapshot, case["schema"], within=snapshot.schema_dir)
        if not fixture_path.is_file() or not schema_path.is_file():
            errors.append(f"{case_id}: fixture or schema path does not exist")
            continue
        if sha256(fixture_path) != case.get("sha256"):
            errors.append(f"{case_id}: fixture hash drift")
        instance, parse_errors = parse_fixture(case, fixture_path)
        case_errors = list(parse_errors)
        if instance is not None:
            schema = store.load(schema_path)
            case_errors.extend(validator.validate(instance, schema, schema_path))
            case_errors.extend(semantic_errors(instance, schema_path))
        actual_valid = not case_errors
        if actual_valid != case.get("expectedValid"):
            detail = case_errors[0] if case_errors else "fixture unexpectedly passed"
            errors.append(f"{case_id}: expected valid={case.get('expectedValid')}, got {actual_valid}: {detail}")

        event_schema_value = case.get("eventSchema")
        if event_schema_value is not None:
            event_schema_path = root_path(snapshot, event_schema_value, within=snapshot.schema_dir)
            event_errors = list(parse_errors)
            event = None
            if instance is not None:
                try:
                    event = instance["params"]["event"]
                except (KeyError, TypeError):
                    event_errors.append("$: fixture does not contain params.event")
            if event is not None:
                event_schema = store.load(event_schema_path)
                event_errors.extend(validator.validate(event, event_schema, event_schema_path))
            event_valid = not event_errors
            event_expected = case.get("eventExpectedValid", case.get("expectedValid"))
            if event_valid != event_expected:
                detail = event_errors[0] if event_errors else "event unexpectedly passed"
                errors.append(
                    f"{case_id}: expected event valid={event_expected}, got {event_valid}: {detail}"
                )

        aggregate_expected = case.get("aggregateExpectedValid")
        if case.get("binding") != "registration-json" and not isinstance(aggregate_expected, bool):
            errors.append(f"{case_id}: wire fixture is missing aggregateExpectedValid")
            continue
        if not isinstance(aggregate_expected, bool):
            continue

        aggregate_errors = list(parse_errors)
        if instance is not None:
            aggregate_errors.extend(validator.validate(instance, aggregate, aggregate_path))
            aggregate_errors.extend(semantic_errors(instance, aggregate_path))
        aggregate_valid = not aggregate_errors
        if aggregate_valid != aggregate_expected:
            detail = aggregate_errors[0] if aggregate_errors else "fixture unexpectedly passed"
            errors.append(
                f"{case_id}: expected aggregate valid={aggregate_expected}, "
                f"got {aggregate_valid}: {detail}"
            )
        if not aggregate_valid or instance is None:
            continue

        matching_aggregate_refs = {
            branch["$ref"]
            for branch in aggregate_branches
            if isinstance(branch, dict)
            and isinstance(branch.get("$ref"), str)
            and not validator.validate(instance, branch, aggregate_path)
        }
        covered_aggregate_refs.update(matching_aggregate_refs)
        if "observe-notification.schema.json" not in matching_aggregate_refs:
            continue
        try:
            event = instance["params"]["event"]
        except (KeyError, TypeError):
            continue
        covered_observe_event_refs.update(
            branch["$ref"]
            for branch in observe_event_branches
            if isinstance(branch, dict)
            and isinstance(branch.get("$ref"), str)
            and not validator.validate(event, branch, observe_path)
        )

    missing_aggregate = aggregate_refs - covered_aggregate_refs
    if missing_aggregate:
        errors.append(
            "aggregate branches lack valid fixture coverage: "
            + ", ".join(sorted(missing_aggregate))
        )
    missing_observe_events = observe_event_refs - covered_observe_event_refs
    if missing_observe_events:
        errors.append(
            "observe event variants lack valid fixture coverage: "
            + ", ".join(sorted(missing_observe_events))
        )
    return errors, len(cases)


def check_snapshot_versions(
    snapshot: Snapshot,
    manifests: dict[str, dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    for label, manifest in manifests.items():
        if manifest.get("draftVersion") != snapshot.draft_version:
            errors.append(
                f"{label} manifest draftVersion mismatch: expected {snapshot.draft_version!r}, "
                f"got {manifest.get('draftVersion')!r}"
            )
        if manifest.get("protocolVersion") != snapshot.protocol_version:
            errors.append(
                f"{label} manifest protocolVersion mismatch: expected {snapshot.protocol_version!r}, "
                f"got {manifest.get('protocolVersion')!r}"
            )
    return errors


def check_manifest_drift(
    snapshot: Snapshot,
    schema_manifest: dict[str, Any],
    fixture_manifest: dict[str, Any],
    conformance: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    canonical_requirements = root_path(
        snapshot,
        conformance.get("canonicalRequirements", ""),
        within=snapshot.spec_dir,
    )
    if canonical_requirements != snapshot.requirements_path:
        errors.append(
            "conformance manifest canonicalRequirements does not select "
            f"{snapshot.requirements_path.relative_to(snapshot.root)}"
        )

    declared_schemas = {item["path"] for item in schema_manifest.get("documents", [])}
    actual_schemas = {
        str(path.relative_to(snapshot.root)) for path in snapshot_schema_files(snapshot)
    }
    if declared_schemas != actual_schemas:
        errors.append("schema manifest file list drift")
    for item in schema_manifest.get("documents", []):
        path = root_path(snapshot, item["path"], within=snapshot.schema_dir)
        if not path.is_file() or sha256(path) != item.get("sha256"):
            errors.append(f"schema manifest hash drift: {item['path']}")

    declared_fixtures = {item["path"] for item in fixture_manifest.get("cases", [])}
    actual_fixtures = {
        str(path.relative_to(snapshot.root))
        for path in snapshot.fixture_dir.rglob("*")
        if path.is_file()
        and path != snapshot.fixture_manifest_path
        and path.suffix in {".json", ".jsonl"}
    }
    if declared_fixtures != actual_fixtures:
        errors.append("fixture manifest file list drift")

    declared_profiles = {item["path"] for item in conformance.get("profiles", [])}
    actual_profiles = {
        str(path.relative_to(snapshot.root))
        for path in (snapshot.conformance_dir / "profiles").glob("*.md")
    }
    if declared_profiles != actual_profiles:
        errors.append("conformance manifest profile list drift")
    for profile in conformance.get("profiles", []):
        path = root_path(snapshot, profile["path"], within=snapshot.conformance_dir)
        if not path.is_file() or sha256(path) != profile.get("sha256"):
            errors.append(f"conformance profile hash drift: {profile['path']}")
    return errors


def check_requirements(
    snapshot: Snapshot,
    manifest: dict[str, Any],
    conformance: dict[str, Any],
) -> tuple[list[str], int]:
    errors: list[str] = []
    requirements = manifest.get("requirements", [])
    known: set[str] = set()
    for requirement in requirements:
        req_id = requirement.get("id", "")
        if not REQ_RE.fullmatch(req_id):
            errors.append(f"requirements: malformed id {req_id!r}")
        if req_id in known:
            errors.append(f"requirements: duplicate id {req_id}")
        known.add(req_id)
        requirement_line = f"**{req_id} — {requirement.get('level')}.** {requirement.get('text', '')}"
        expected_hash = hashlib.sha256(requirement_line.encode("utf-8")).hexdigest()
        if expected_hash != requirement.get("textSha256"):
            errors.append(f"{req_id}: textSha256 mismatch")
        document = root_path(snapshot, requirement.get("document", ""), within=snapshot.spec_dir)
        if not document.is_file():
            errors.append(f"{req_id}: document does not exist")
            continue
        text = document.read_text(encoding="utf-8")
        anchor = f'<a id="{requirement.get("anchor")}"></a>'
        if text.count(anchor) != 1:
            errors.append(f"{req_id}: anchor count is not one")
        if text.count(requirement_line) != 1:
            errors.append(f"{req_id}: canonical requirement line count is not one")
        section = requirement.get("section", "")
        if re.search(rf"^#{{1,6}} {re.escape(section)}$", text, re.MULTILINE) is None:
            errors.append(f"{req_id}: section heading not found")

    referenced: set[str] = set()
    json_paths = list(snapshot.schema_dir.glob("*.json")) + [
        snapshot.fixture_manifest_path,
        snapshot.conformance_manifest_path,
    ]
    for path in json_paths:
        referenced.update(REQ_RE.findall(path.read_text(encoding="utf-8")))
    unknown = referenced - known
    if unknown:
        errors.append(f"unknown requirement references: {', '.join(sorted(unknown))}")
    covered = {req for profile in conformance.get("profiles", []) for req in profile.get("requirements", [])}
    missing = known - covered
    if missing:
        errors.append(f"requirements absent from conformance manifest: {', '.join(sorted(missing))}")
    return errors, len(requirements)


def check_private_links(root: Path) -> list[str]:
    errors: list[str] = []
    allowed_suffixes = {".json", ".jsonl", ".md", ".py", ".yml", ".yaml", ".txt"}
    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.parts or path.suffix.lower() not in allowed_suffixes:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeError:
            continue
        if PRIVATE_NOTION_RE.search(text):
            errors.append(f"private Notion link found: {path.relative_to(root)}")
    return errors


def check_relative_markdown_links(root: Path) -> list[str]:
    errors: list[str] = []
    for path in root.rglob("*.md"):
        if ".git" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeError:
            continue
        for match in MARKDOWN_LINK_RE.finditer(text):
            target = match.group("target").strip()
            if target.startswith("<") and target.endswith(">"):
                target = target[1:-1]
            parsed = urlparse(target)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            candidate = (path.parent / unquote(parsed.path)).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                errors.append(
                    f"{path.relative_to(root)}: relative Markdown link escapes repository: {target}"
                )
                continue
            if not candidate.exists():
                errors.append(
                    f"{path.relative_to(root)}: broken relative Markdown link: {target}"
                )
    return errors


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_manifests(snapshot: Snapshot) -> dict[str, dict[str, Any]]:
    return {
        "schema": load_json(snapshot.schema_manifest_path, snapshot.root),
        "fixture": load_json(snapshot.fixture_manifest_path, snapshot.root),
        "conformance": load_json(snapshot.conformance_manifest_path, snapshot.root),
    }


def update_manifests(snapshot: Snapshot) -> None:
    if snapshot.key != "draft":
        raise CheckFailure(f"cannot update immutable frozen snapshot {snapshot.key!r}")

    manifests = load_manifests(snapshot)
    schema_manifest = manifests["schema"]
    for item in schema_manifest["documents"]:
        item["sha256"] = sha256(root_path(snapshot, item["path"], within=snapshot.schema_dir))
    write_json(snapshot.schema_manifest_path, schema_manifest)

    fixture_manifest = manifests["fixture"]
    for item in fixture_manifest["cases"]:
        item["sha256"] = sha256(root_path(snapshot, item["path"], within=snapshot.fixture_dir))
    write_json(snapshot.fixture_manifest_path, fixture_manifest)

    conformance = manifests["conformance"]
    for item in conformance["profiles"]:
        item["sha256"] = sha256(root_path(snapshot, item["path"], within=snapshot.conformance_dir))
    write_json(snapshot.conformance_manifest_path, conformance)


@dataclass(frozen=True)
class CheckResult:
    errors: list[str]
    schema_count: int
    fixture_count: int
    requirement_count: int
    profile_count: int


def run_checks(root: Path = ROOT, snapshot_key: str = "draft", *, update: bool = False) -> CheckResult:
    errors: list[str] = []
    schema_count = 0
    fixture_count = 0
    requirement_count = 0
    profile_count = 0
    try:
        snapshot = Snapshot.resolve(root, snapshot_key)
        if update:
            update_manifests(snapshot)

        manifests = load_manifests(snapshot)
        requirements = load_json(snapshot.requirements_path, snapshot.root)
        store = SchemaStore(snapshot)
        errors.extend(check_snapshot_versions(snapshot, manifests))
        errors.extend(check_schema_structure(snapshot, store))
        errors.extend(
            check_manifest_drift(
                snapshot,
                manifests["schema"],
                manifests["fixture"],
                manifests["conformance"],
            )
        )
        fixture_errors, fixture_count = check_fixture_cases(
            snapshot, store, manifests["fixture"]
        )
        errors.extend(fixture_errors)
        requirement_errors, requirement_count = check_requirements(
            snapshot, requirements, manifests["conformance"]
        )
        errors.extend(requirement_errors)
        errors.extend(check_private_links(snapshot.root))
        errors.extend(check_relative_markdown_links(snapshot.root))
        schema_count = len(snapshot_schema_files(snapshot))
        profile_count = len(manifests["conformance"].get("profiles", []))
    except (CheckFailure, KeyError, TypeError) as exc:
        errors.append(str(exc))
    return CheckResult(
        errors=errors,
        schema_count=schema_count,
        fixture_count=fixture_count,
        requirement_count=requirement_count,
        profile_count=profile_count,
    )


def report_result(
    result: CheckResult,
    *,
    stdout: Any = None,
    stderr: Any = None,
) -> int:
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    if result.errors:
        print(f"conformance checks failed ({len(result.errors)}):", file=stderr)
        for error in result.errors:
            print(f"- {error}", file=stderr)
        return 1
    print(
        "conformance checks passed: "
        f"{result.schema_count} schemas, {result.fixture_count} fixtures, "
        f"{result.requirement_count} requirements, {result.profile_count} profiles",
        file=stdout,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", default="draft", help="logical snapshot key (default: draft)")
    parser.add_argument(
        "--update-manifests",
        action="store_true",
        help="refresh declared SHA-256 values in the mutable draft snapshot only",
    )
    args = parser.parse_args()
    result = run_checks(ROOT, args.snapshot, update=args.update_manifests)
    return report_result(result)


if __name__ == "__main__":
    raise SystemExit(main())

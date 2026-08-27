#!/usr/bin/env python3
"""Dependency-free structural and golden-fixture checks for AHP draft artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
DRAFT_VERSION = "0.1.0-draft.1"
DIALECT = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_DIR = ROOT / "schemas" / DRAFT_VERSION
SCHEMA_MANIFEST = SCHEMA_DIR / "manifest.json"
FIXTURE_DIR = ROOT / "fixtures" / DRAFT_VERSION
FIXTURE_MANIFEST = FIXTURE_DIR / "manifest.json"
CONFORMANCE_MANIFEST = ROOT / "conformance" / "manifest.json"
REQUIREMENTS = ROOT / "spec" / "requirements.json"
MIGRATION = ROOT / "spec" / "source-migration.json"
SDK_GENERATION_MANIFEST = ROOT / "sdk-generation" / DRAFT_VERSION / "manifest.json"
REQ_RE = re.compile(r"\bAHP-[A-Z]+-\d{3}\b")
PRIVATE_NOTION_RE = re.compile(
    r"(?:https?://(?:www\.)?notion\.(?:so|site)(?:/|\b)|" + "notion" + r"://)",
    re.IGNORECASE,
)


class CheckFailure(Exception):
    """Raised for malformed checker inputs."""


def object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CheckFailure(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=object_without_duplicates)
    except (OSError, UnicodeError, json.JSONDecodeError, CheckFailure) as exc:
        raise CheckFailure(f"{path.relative_to(ROOT)}: invalid JSON: {exc}") from exc


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def root_path(value: str) -> Path:
    candidate = (ROOT / value).resolve()
    try:
        candidate.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise CheckFailure(f"path escapes repository: {value}") from exc
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
    def __init__(self) -> None:
        self.cache: dict[Path, Any] = {}

    def load(self, path: Path) -> Any:
        path = path.resolve()
        if path not in self.cache:
            self.cache[path] = load_json(path)
        return self.cache[path]

    def resolve(self, reference: str, current: Path) -> tuple[Any, Path]:
        file_part, separator, fragment = reference.partition("#")
        parsed = urlparse(file_part)
        if parsed.scheme or parsed.netloc or file_part.startswith("/"):
            raise CheckFailure(f"{current.relative_to(ROOT)}: non-offline $ref {reference!r}")
        if file_part and ".." in Path(unquote(file_part)).parts:
            raise CheckFailure(f"{current.relative_to(ROOT)}: parent traversal in $ref {reference!r}")
        target = (current.parent / unquote(file_part)).resolve() if file_part else current.resolve()
        try:
            target.relative_to(SCHEMA_DIR.resolve())
        except ValueError as exc:
            raise CheckFailure(f"{current.relative_to(ROOT)}: $ref leaves immutable draft path: {reference!r}") from exc
        document = self.load(target)
        if separator and fragment:
            if not fragment.startswith("/"):
                raise CheckFailure(f"{current.relative_to(ROOT)}: unsupported non-pointer fragment: {reference!r}")
            node = document
            for raw_token in fragment[1:].split("/"):
                token = unquote(raw_token).replace("~1", "/").replace("~0", "~")
                try:
                    node = node[int(token)] if isinstance(node, list) else node[token]
                except (KeyError, IndexError, ValueError, TypeError) as exc:
                    raise CheckFailure(f"{current.relative_to(ROOT)}: unresolved $ref {reference!r}") from exc
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


def check_schema_structure(store: SchemaStore) -> list[str]:
    errors: list[str] = []
    ids: set[str] = set()
    schema_files = sorted(SCHEMA_DIR.glob("*.schema.json"))
    for path in schema_files:
        try:
            schema = store.load(path)
        except CheckFailure as exc:
            errors.append(str(exc))
            continue
        label = path.relative_to(ROOT)
        if not isinstance(schema, dict):
            errors.append(f"{label}: schema root must be an object")
            continue
        if schema.get("$schema") != DIALECT:
            errors.append(f"{label}: must declare Draft 2020-12")
        if "Working Draft" not in schema.get("title", "") or "Working Draft" not in schema.get("$comment", ""):
            errors.append(f"{label}: missing Working Draft markers")
        expected_id = f"https://agenthooksprotocol.org/schemas/{DRAFT_VERSION}/{path.name}"
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
    if schema_path.name == "intercept-request.schema.json" and isinstance(instance, dict):
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


def check_fixture_cases(store: SchemaStore) -> tuple[list[str], int]:
    errors: list[str] = []
    manifest = load_json(FIXTURE_MANIFEST)
    cases = manifest.get("cases", [])
    seen_ids: set[str] = set()
    for case in cases:
        case_id = case.get("id", "<missing-id>")
        if case_id in seen_ids:
            errors.append(f"fixture manifest: duplicate case id {case_id}")
        seen_ids.add(case_id)
        fixture_path = root_path(case["path"])
        schema_path = root_path(case["schema"])
        if not fixture_path.is_file() or not schema_path.is_file():
            errors.append(f"{case_id}: fixture or schema path does not exist")
            continue
        if sha256(fixture_path) != case.get("sha256"):
            errors.append(f"{case_id}: fixture hash drift")
        instance, case_errors = parse_fixture(case, fixture_path)
        if instance is not None:
            schema = store.load(schema_path)
            case_errors.extend(SubsetValidator(store).validate(instance, schema, schema_path))
            case_errors.extend(semantic_errors(instance, schema_path))
        actual_valid = not case_errors
        if actual_valid != case.get("expectedValid"):
            detail = case_errors[0] if case_errors else "fixture unexpectedly passed"
            errors.append(f"{case_id}: expected valid={case.get('expectedValid')}, got {actual_valid}: {detail}")
    return errors, len(cases)


def check_manifest_drift() -> list[str]:
    errors: list[str] = []
    schema_manifest = load_json(SCHEMA_MANIFEST)
    declared_schemas = {item["path"] for item in schema_manifest.get("documents", [])}
    actual_schemas = {str(path.relative_to(ROOT)) for path in SCHEMA_DIR.glob("*.schema.json")}
    if declared_schemas != actual_schemas:
        errors.append("schema manifest file list drift")
    for item in schema_manifest.get("documents", []):
        path = root_path(item["path"])
        if not path.is_file() or sha256(path) != item.get("sha256"):
            errors.append(f"schema manifest hash drift: {item['path']}")

    fixture_manifest = load_json(FIXTURE_MANIFEST)
    declared_fixtures = {item["path"] for item in fixture_manifest.get("cases", [])}
    actual_fixtures = {
        str(path.relative_to(ROOT))
        for path in FIXTURE_DIR.rglob("*")
        if path.is_file() and path != FIXTURE_MANIFEST and path.suffix in {".json", ".jsonl"}
    }
    if declared_fixtures != actual_fixtures:
        errors.append("fixture manifest file list drift")

    conformance = load_json(CONFORMANCE_MANIFEST)
    for profile in conformance.get("profiles", []):
        path = root_path(profile["path"])
        if not path.is_file() or sha256(path) != profile.get("sha256"):
            errors.append(f"conformance profile hash drift: {profile['path']}")
    return errors


def check_requirements() -> tuple[list[str], int]:
    errors: list[str] = []
    manifest = load_json(REQUIREMENTS)
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
        document = root_path(requirement.get("document", ""))
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
    json_paths = list(SCHEMA_DIR.glob("*.json")) + [FIXTURE_MANIFEST, CONFORMANCE_MANIFEST]
    for path in json_paths:
        referenced.update(REQ_RE.findall(path.read_text(encoding="utf-8")))
    unknown = referenced - known
    if unknown:
        errors.append(f"unknown requirement references: {', '.join(sorted(unknown))}")
    conformance = load_json(CONFORMANCE_MANIFEST)
    covered = {req for profile in conformance.get("profiles", []) for req in profile.get("requirements", [])}
    missing = known - covered
    if missing:
        errors.append(f"requirements absent from conformance manifest: {', '.join(sorted(missing))}")
    return errors, len(requirements)


def check_source_migration() -> list[str]:
    errors: list[str] = []
    migration = load_json(MIGRATION)
    proposal = root_path(migration.get("proposalPath", ""))
    canonical = root_path(migration.get("canonicalPath", ""))
    if migration.get("privateSourceLocationStored") is not False:
        errors.append("source migration must not store a private source location")
    if not proposal.is_file() or sha256(proposal) != migration.get("proposalSha256"):
        errors.append("source migration proposal hash mismatch")
    if not canonical.is_file():
        errors.append("source migration canonical path does not exist")
        return errors
    text = canonical.read_text(encoding="utf-8")
    match = re.search(r"^## 24\. Open questions for v0\.1 review\n(?P<body>.*?)(?=^## 25\.)", text, re.MULTILINE | re.DOTALL)
    count = len(re.findall(r"^\d+\. ", match.group("body"), re.MULTILINE)) if match else -1
    if count != migration.get("openQuestionsPreserved"):
        errors.append(f"source migration open-question count mismatch: expected {migration.get('openQuestionsPreserved')}, got {count}")
    return errors


def resolve_pointer(document: Any, pointer: str, label: str) -> Any:
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise CheckFailure(f"{label}: expected JSON Pointer")
    current = document
    for raw_token in pointer[1:].split("/"):
        token = unquote(raw_token).replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            raise CheckFailure(f"{label}: unresolved pointer {pointer!r}")
    return current


def check_sdk_generation() -> tuple[list[str], int]:
    errors: list[str] = []
    manifest = load_json(SDK_GENERATION_MANIFEST)
    if manifest.get("status") != "non-normative":
        errors.append("SDK generation manifest must be non-normative")
    if manifest.get("schemaRevision") != DRAFT_VERSION:
        errors.append("SDK generation revision mismatch")

    schema_pin = manifest.get("schemaManifest", {})
    schema_path = root_path(schema_pin.get("path", ""))
    if schema_path != SCHEMA_MANIFEST.resolve() or not schema_path.is_file():
        errors.append("SDK generation schema manifest pin is invalid")
    elif sha256(schema_path) != schema_pin.get("sha256"):
        errors.append("SDK generation schema manifest hash drift")

    profile_pin = manifest.get("profile", {})
    cases_pin = manifest.get("compatibilityCases", {})
    profile_path = root_path(profile_pin.get("path", ""))
    cases_path = root_path(cases_pin.get("path", ""))
    if not profile_path.is_file() or sha256(profile_path) != profile_pin.get("sha256"):
        errors.append("SDK generation profile hash drift")
    if not cases_path.is_file() or sha256(cases_path) != cases_pin.get("sha256"):
        errors.append("SDK generation corpus hash drift")
    profile = load_json(profile_path)
    cases = load_json(cases_path)
    if profile.get("schemaRevision") != DRAFT_VERSION or cases.get("schemaRevision") != DRAFT_VERSION:
        errors.append("SDK generation profile or corpus revision mismatch")
    if profile.get("protocolVersion") != manifest.get("protocolVersion"):
        errors.append("SDK generation protocol version mismatch")

    schema_manifest = load_json(SCHEMA_MANIFEST)
    schema_paths = {item["path"] for item in schema_manifest.get("documents", [])}
    stable_names = profile.get("stableNames", {})
    if len(set(stable_names.values())) != len(stable_names):
        errors.append("SDK generation stable names are not unique")
    for source, name in stable_names.items():
        path_text, separator, pointer = source.partition("#")
        if not separator or path_text not in schema_paths or not isinstance(name, str) or not name:
            errors.append(f"SDK generation invalid stable name source: {source}")
            continue
        try:
            resolve_pointer(load_json(root_path(path_text)), pointer, source)
        except CheckFailure as exc:
            errors.append(str(exc))

    known_names = set(stable_names.values())
    for root in profile.get("publicRoots", []):
        if root.get("name") not in known_names:
            errors.append(f"SDK generation public root lacks stable name: {root.get('name')}")
        if root.get("schema") not in schema_paths:
            errors.append(f"SDK generation public root has unknown schema: {root.get('schema')}")

    for discriminator in profile.get("discriminators", []):
        schema = discriminator.get("schema", "")
        if schema not in schema_paths:
            errors.append(f"SDK generation discriminator has unknown schema: {schema}")
            continue
        try:
            node = resolve_pointer(load_json(root_path(schema)), discriminator.get("pointer", ""), schema)
            if not isinstance(node, dict) or not isinstance(node.get("oneOf"), list):
                errors.append(
                    f"SDK generation discriminator does not point to oneOf: "
                    f"{schema}{discriminator.get('pointer')}"
                )
        except CheckFailure as exc:
            errors.append(str(exc))

    case_ids: set[str] = set()
    for case in cases.get("cases", []):
        case_id = case.get("id", "")
        if not case_id or case_id in case_ids:
            errors.append(f"SDK generation duplicate or empty case id: {case_id!r}")
        case_ids.add(case_id)
        if case.get("root") not in known_names:
            errors.append(f"SDK generation case has unknown root: {case.get('root')}")
        if not isinstance(case.get("canonicalValid"), bool):
            errors.append(f"SDK generation case lacks canonicalValid boolean: {case_id}")
    return errors, len(case_ids)


def check_private_links() -> list[str]:
    errors: list[str] = []
    allowed_suffixes = {".json", ".jsonl", ".md", ".py", ".yml", ".yaml", ".txt"}
    for path in ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts or path.suffix.lower() not in allowed_suffixes:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeError:
            continue
        if PRIVATE_NOTION_RE.search(text):
            errors.append(f"private Notion link found: {path.relative_to(ROOT)}")
    return errors


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def update_manifests() -> None:
    schema_manifest = load_json(SCHEMA_MANIFEST)
    for item in schema_manifest["documents"]:
        item["sha256"] = sha256(root_path(item["path"]))
    write_json(SCHEMA_MANIFEST, schema_manifest)

    fixture_manifest = load_json(FIXTURE_MANIFEST)
    for item in fixture_manifest["cases"]:
        item["sha256"] = sha256(root_path(item["path"]))
    write_json(FIXTURE_MANIFEST, fixture_manifest)

    conformance = load_json(CONFORMANCE_MANIFEST)
    for item in conformance["profiles"]:
        item["sha256"] = sha256(root_path(item["path"]))
    write_json(CONFORMANCE_MANIFEST, conformance)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update-manifests", action="store_true", help="refresh schema, fixture, and profile SHA-256 values")
    args = parser.parse_args()
    if args.update_manifests:
        update_manifests()

    store = SchemaStore()
    errors: list[str] = []
    try:
        errors.extend(check_schema_structure(store))
        errors.extend(check_manifest_drift())
        fixture_errors, fixture_count = check_fixture_cases(store)
        errors.extend(fixture_errors)
        requirement_errors, requirement_count = check_requirements()
        errors.extend(requirement_errors)
        errors.extend(check_source_migration())
        sdk_errors, sdk_case_count = check_sdk_generation()
        errors.extend(sdk_errors)
        errors.extend(check_private_links())
    except (CheckFailure, KeyError, TypeError) as exc:
        errors.append(str(exc))
        fixture_count = 0
        requirement_count = 0
        sdk_case_count = 0

    if errors:
        print(f"conformance checks failed ({len(errors)}):", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    schema_count = len(list(SCHEMA_DIR.glob("*.schema.json")))
    print(
        f"conformance checks passed: {schema_count} schemas, {fixture_count} fixtures, "
        f"{requirement_count} requirements, {sdk_case_count} SDK generation cases"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

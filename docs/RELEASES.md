# Release and Versioning Policy

## Scope

This policy governs releases of the language-neutral Agent Hooks Protocol. It does not make an SDK, binding, example, adapter, or implementation normative. Every SDK, including every TypeScript SDK, is non-normative and can use its own separately documented release cycle and version numbers.

## Version numbers

Protocol releases use Semantic Versioning 2.0.0 in the form `MAJOR.MINOR.PATCH` and Git tags in the form `vMAJOR.MINOR.PATCH`.

- **MAJOR:** incompatible changes to released normative requirements.
- **MINOR:** backward-compatible additions or deprecations.
- **PATCH:** backward-compatible corrections and clarifications that do not add a requirement or change intended behavior.

Before `1.0.0`, the protocol is initial development. A `0.MINOR.0` release can contain incompatible changes, which must be called out prominently. Once `1.0.0` is released, incompatible normative changes require a new major version.

A change is classified by its effect on the released normative contract, not by file type, line count, or implementation difficulty. When classification is uncertain, use the more conservative version increment and explain it in the release record.

The exact SemVer without the `v` prefix is also the repository snapshot key. For release `0.1.0`, the matching snapshot roots are `spec/0.1.0/`, `schema/0.1.0/`, `fixtures/0.1.0/`, and `conformance/0.1.0/`. Snapshot keys do not have prerelease or build suffixes or aliases such as `latest`, and their numeric components do not use leading zeroes.

The repository snapshot key and the wire `protocolVersion` serve different purposes. Freezing repository snapshot `0.1.0` does not by itself change wire protocol version `0.1`. The `draftVersion` and `protocolVersion` values in the requirement and artifact manifests must agree across all four trees. Any intentional change to those values or to public schema `$id` values is a reviewed release-preparation change, not an automatic consequence of renaming a repository directory.

## Normative release set

Each release must identify the exact repository revision and the language-neutral protocol documents or artifacts that form its normative release set. Material on the default branch is unreleased unless a released document explicitly says otherwise.

AHP proposals explain and authorize project direction. Tests, examples, generated artifacts, SDKs, bindings, adapters, and implementations can provide evidence, but they remain non-normative and are not part of the normative release set. This includes TypeScript SDKs and implementations. An implementation cannot override normative protocol material by behaving differently.

Publication freezes all four parts of the logical repository snapshot, even when the release record identifies only a subset as normative: specification and requirements, JSON Schemas, golden fixtures, and conformance profiles. Markdown and JSON Schema remain source artifacts. A release does not replace them with generated documentation, MDX, TypeScript definitions, SDKs, adapters, or reference implementations.

## Proposal and release relationship

Material protocol and release-policy changes require an Accepted AHP proposal. Acceptance is not publication: a decision becomes part of a released protocol only when its normative changes are incorporated, reviewed, and included in a release.

Substantive changes after AHP acceptance must follow the amendment rules in the [AHP process](../governance/AHP-PROCESS.md).

## Release process

An active maintainer coordinates each release in a public issue or pull request and, before freezing, must:

1. identify included Accepted AHP proposals and other changes;
2. classify compatibility and select the version;
3. update and review intended status or version references, migration guidance, and deprecations in the mutable `draft` snapshot;
4. ensure `spec/draft/`, `schema/draft/`, `fixtures/draft/`, and `conformance/draft/` describe one matching revision;
5. run and record the focused checker tests and draft validation:

   ```sh
   python3 -m unittest discover tools/tests
   python3 tools/check_conformance.py
   ```

6. identify the normative release set and source revision; and
7. prepare release notes describing compatibility, security fixes, and known limitations.

To freeze the prepared draft, set the exact release SemVer, verify that all four destinations are new, and copy all four trees:

```sh
VERSION=0.1.0
python3 - "$VERSION" <<'PY'
import re
import sys

if re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", sys.argv[1]) is None:
    raise SystemExit("VERSION must be an exact MAJOR.MINOR.PATCH")
PY

for root in spec schema fixtures conformance; do
  test ! -e "$root/$VERSION" || {
    echo "$root/$VERSION already exists" >&2
    exit 1
  }
done
for root in spec schema fixtures conformance; do
  cp -R "$root/draft" "$root/$VERSION"
done
```

The copied JSON metadata contains repository-relative `draft` paths. Retargeting them is a reviewed release-preparation edit, not automatic publication. Update only the known path-bearing fields in the copied requirement and artifact manifests; do not mechanically rewrite arbitrary text, wire versions, relative schema `$ref` values, or public schema `$id` values. The following script refuses an unexpected source path instead of silently blessing it:

```sh
python3 - "$VERSION" <<'PY'
import json
import sys
from pathlib import Path

version = sys.argv[1]

def load(path):
    return json.loads(path.read_text(encoding="utf-8"))

def write(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

def retarget(record, field, root):
    value = record.get(field)
    prefix = f"{root}/draft/"
    if not isinstance(value, str) or not value.startswith(prefix):
        raise SystemExit(f"unexpected {field} path: {value!r}")
    record[field] = f"{root}/{version}/{value.removeprefix(prefix)}"

requirements_path = Path(f"spec/{version}/requirements.json")
requirements = load(requirements_path)
for requirement in requirements["requirements"]:
    retarget(requirement, "document", "spec")
write(requirements_path, requirements)

schema_path = Path(f"schema/{version}/manifest.json")
schema = load(schema_path)
for document in schema["documents"]:
    retarget(document, "path", "schema")
write(schema_path, schema)

fixtures_path = Path(f"fixtures/{version}/manifest.json")
fixtures = load(fixtures_path)
for case in fixtures["cases"]:
    retarget(case, "path", "fixtures")
    retarget(case, "schema", "schema")
    if "eventSchema" in case:
        retarget(case, "eventSchema", "schema")
write(fixtures_path, fixtures)

conformance_path = Path(f"conformance/{version}/manifest.json")
conformance = load(conformance_path)
retarget(conformance, "canonicalRequirements", "spec")
for profile in conformance["profiles"]:
    retarget(profile, "path", "conformance")
write(conformance_path, conformance)
PY
```

Validate the selected frozen snapshot before tagging:

```sh
python3 tools/check_conformance.py --snapshot "$VERSION"
```

The checker validates this selected snapshot without creating, retargeting, or updating it. Review the copied paths and complete release diff, then create tag `v$VERSION` and the repository release only after the frozen validation passes. Never use `--update-manifests` with an exact-SemVer snapshot. A release must not silently change after publication; correct it with a new release using the appropriate version increment. Repository hosting metadata can be repaired without changing release contents if the repair is documented.

## Deprecation and removal

A deprecation must state the affected behavior, replacement or migration path, and earliest release in which removal is allowed. After `1.0.0`, removal of released normative behavior requires a major release unless the original contract explicitly bounded that behavior to a shorter lifetime.

Security fixes can require accelerated change. Use the urgent-action rule for private handling, then document compatibility and version impact as soon as disclosure is safe.

## Support and cadence

The project has no fixed release cadence. Release notes identify supported lines when more than the latest release is supported. Unless they say otherwise, only the latest release receives fixes. This policy does not guarantee a release date, maintenance duration, or security response time.

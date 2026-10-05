# SDK model generator

`ahp-codegen` is the official schema-driven SDK model generator. It reads the schema manifest directly; stable SDK names live in that manifest so schema and generation changes are reviewed together. Every named schema-document root receives parse and encode entrypoints. If generated output conflicts with its source schema, the schema takes precedence. Implementations may use this generator, another generator, or handwritten models.

Generated source belongs in each SDK repository together with a lock recording the protocol tag, schema snapshot, generator version, and exact source repository commit. `tools/generate_sdk.py` records its checkout HEAD as `sourceCommit`; commit generator changes before publishing regenerated SDK artifacts. Use `--go-sdk <root>` or `--rust-sdk <root>` to regenerate one SDK, and append `--check` to verify identical output. Rust keeps matching locks in its root and `src/` directories.

## Compatibility model

Parsing and protocol validation are separate operations. Generated parsers preserve forward-compatible data, but a successful parse does not establish that a message is valid AHP.

Generated codecs preserve unknown object properties recursively, retain unknown enum and discriminator values, select known variants exactly, and do not coerce values, apply defaults, fabricate required data, or structurally score union candidates. Their round-trip promise is semantic JSON preservation, not preservation of whitespace, key order, escape spelling, number spelling, or duplicate keys.

Do not use named parse entrypoints as validators or response classifiers. In particular, array cardinality is enforced by canonical validation, so `parseInterceptDenyResponse` and `parseInterceptNoEffectResponse` can both structurally parse the same payload. Canonically validate a response before classifying it or making an authorization decision; `parse*().ok` alone is not authorization.

## Commands

From the repository root:

```sh
cargo run --locked --manifest-path tools/sdk-codegen/Cargo.toml -- \
  check --revision draft

for target in typescript python go rust; do
  cargo run --locked --manifest-path tools/sdk-codegen/Cargo.toml -- \
    generate --revision draft --language "$target" \
    --output "/tmp/ahp.generated.$target"
done
```

Use `--emit-ir` instead of `--language` to inspect the language-neutral lowering. All emitters consume the same IR and implement the same structural parsing compatibility behavior.

The current `draft` snapshot permits integer request IDs and null response IDs. Generated codecs accept only integers that are safely interoperable across all supported SDKs. String-only request IDs require a future schema change; generation does not silently alter the schema.

## SDK synchronization

A push to `main` that changes schema snapshots, the generator, or draft conformance metadata runs `.github/workflows/sync-sdks.yml`. The workflow regenerates the TypeScript, Python, Go, and Rust SDKs and opens or updates one `automation/schema-sync` pull request in each SDK repository. Each SDK records the exact source commit, schema snapshot, manifest digest, and language in `ahp-codegen.lock.json`.

Cross-repository writes use a dedicated GitHub App. Configure `SDK_SYNC_APP_ID` as an Actions variable and `SDK_SYNC_APP_PRIVATE_KEY` as an Actions secret in this repository. The App requires **Contents: read and write** and **Pull requests: read and write** permissions. Each synchronization job mints a short-lived token scoped to its allowlisted target repository. Do not expose these credentials to pull-request workflows.

## Draft composite-schema support

The compiler lowers object/composition siblings as intersections, accepts JSON
Schema type arrays, and recognizes object-count constraints. Typed additional
properties remain preserved JSON in generated codecs; their value constraints,
cardinality, conditionals, formats, and permission rules require canonical schema
validation. This is not a compatibility mode or a relaxation of the wire grammar.

### Exact union selectors and presence predicates

Finite branch selectors must use literal alternatives rather than relying on an
open enum to exclude a sibling's known tag. Execution reasons are normalized this
way without changing canonical wire validity. Required-only schemas retain every
required member as presence-only Any fields; a missing member differs from null.

An inline oneOf may use `x-sdk-discriminator` when it deliberately has exact known
string tags plus a string-valued extension fallback. The compiler checks that the
selector is required in each branch and known literal values are unique. Codecs
select a known literal exactly; other tags are preserved as unknown variants.
Canonical validation still controls allowed extension syntax and required data.
This is not structural candidate scoring or a replacement for schema validation.

## Generated Go semantic API

Go SDK generation requires Go 1.27 or newer. `--language go` still emits the
unchanged root wire models/codecs. `--language go-facade --output <directory>`
emits the semantic packages and named client boundary methods. The synchronization
driver invokes both and formats with the Go 1.27 toolchain:

```sh
python3 tools/generate_sdk.py --go-sdk /path/to/go-sdk
python3 tools/generate_sdk.py --go-sdk /path/to/go-sdk --check
```

The facade consumes the wire emitter's resolved schema names, fields, literals,
and union arms. Role metadata determines package ownership and positional argument
ordering; schema fields determine constructors, options, and conversions. Required
literals are populated, but required enum choices (including stdio lifecycle) have
no invented defaults. Property `default` annotations are carried as private
`Property.constructor_default` IR metadata (`serde(skip)`), copied into Go rendered
fields, and consumed only by facade constructors. They do not enter serialized
validation descriptors or change parsers or other-language generation. Constructors
materialize actual annotated defaults before applying options; optional defaults
set `Optional.Present`, while required fields with defaults become typed options
rather than required positional arguments. Unannotated fields retain their normal
required/optional policy. Defaults are rendered as typed Go scalar expressions at
generation time, with no runtime decoding or panic path. Supported annotations are
booleans, strings, numbers, and homogeneous scalar enums/literals; this covers all
current canonical defaults. Unsupported composite/reference/null defaults and
incompatible scalar values fail generation with the affected field name. No mutable
default storage is shared. Parsers still preserve missing fields exactly.
Functional options set explicit presence; repeated setters
use the final value. Required collections support typed replacement options.

`registration.New` returns a wire registration value. Transport and subscription
constructors return the registration union arms, and effect constructors return
`*ahp.Effect`. Dependencies remain acyclic: semantic data packages depend on root
wire models; `event` additionally uses `tool`; generated client methods depend on
`event` and the handwritten runtime seam. No data package imports client/server.

All 32 concrete draft event selectors receive host-input projections and named
methods. Observation-only methods have no interception options. `ToolBefore[T]`
carries `tool.Input[T]`; the runtime decodes accepted effective input separately.
Composite facts without a concrete wire struct remain raw JSON in projections.
Source/type/manifest are SDK-owned; absent ID/time and optional fields are omitted.

Duration constructors convert nanoseconds to exact decimal milliseconds without
rounding, overflow, or fabricated defaults. Infallible data constructors preserve
fractional/nonpositive values for canonical boundary validation to reject.
`NewInterceptDuration`/`NewUploadDuration` and `Milliseconds` offer eager checked
conversion; `NewInterceptMilliseconds`/`NewUploadMilliseconds` accept explicit wire
numbers. Data construction never bypasses canonical context validation.

The driver emits `internal/canonical/schemas.json` from the same schema documents,
in the same order and with the same bytes as root `schemas.json`; both are covered
by the existing source lock's manifest digest/document hashes and `--check`.
Smoke tests compile constructors, generic inference, and all boundary methods,
and exercise duration edge cases and presence-preserving projection JSON.

### Hooks and typed capability options

The canonical generated boundary receiver is `*client.Hooks` for all 32 draft
selectors. The handwritten runtime owns `New`, `Options.Events`, mode constants,
and the deprecated `Client` compatibility alias. The generated client example
compiles their seam with `map[string]client.EventCapabilities` and typed modes.

`capability.New([]string{"modify"}, capability.WithInputModification(true, false))`
selects the schema-derived open string union arm without exposing union assembly.
`WithEffects(...string)` replaces that effect list. Each modification target in
`Capabilities.modify` receives a `With<Target>Modification(replace, merge bool)`
option; declarations determine the arguments, not validation predicate branches.
Boolean values are encoded directly with `strconv.FormatBool` into fresh raw
backing bytes because the unchanged root wire model uses raw JSON for composite
constraints. There is no runtime JSON decoding, panic, or implicit validation.

`WithElicitationForm()` and `WithElicitationURL()` each set an explicit empty-object
grant. Absent or empty elicitation capability objects grant neither mode. Nested
options preserve sibling grants and overwrite only their own target; full-object
options remain available for advanced use. No helper automatically adds effects,
modes, flow grants, or injection grants. Boundary-specific capability constructors
retain their advanced wire signatures. All target helpers come from the schema
object graph; no handwritten list of event boundaries or modification targets is
maintained.

# SDK model generator

`ahp-codegen` is the official schema-driven SDK model generator. It reads the schema manifest directly; stable SDK names live in that manifest so schema and generation changes are reviewed together. Every named schema-document root receives parse and encode entrypoints. If generated output conflicts with its source schema, the schema takes precedence. Implementations may use this generator, another generator, or handwritten models.

Generated source belongs in each SDK repository together with a lock recording the protocol tag, schema snapshot, generator version, and exact source repository commit. `tools/generate_sdk.py` records its checkout HEAD as `sourceCommit`; commit generator changes before publishing regenerated SDK artifacts. Use `--python-sdk <root>`, `--go-sdk <root>`, or `--rust-sdk <root>` to regenerate one SDK, and append `--check` to verify identical output. Rust keeps matching locks in its root and `src/` directories.

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

## Public API naming

Public union alternatives describe their schema meaning, not their position in `oneOf` or `anyOf`. References retain canonical model names when tags are ambiguous; unique required literal tags (including `transport`) provide concise names. The declared literal `"unknown"` is `UnknownValue`, distinct from the forward-compatible `Unknown` arm. Hashes, ordinal collision suffixes, and concatenated constraint descriptions are not public naming fallbacks. An ambiguous anonymous schema must gain a canonical reference or a distinguishing semantic tag; generation fails with a naming error rather than inventing an identifier.

Public model projection is separate from validation. Exactly identical alternatives may share one public arm, whose projection retains all original source indices. The original validation descriptors, branch multiplicity, order, and `oneOf`/`anyOf` modes remain intact. Distinct canonical references are not collapsed merely because they have the same discriminator or runtime representation. In particular, all eight elicitation primitive schema references remain distinct.

For an enum-plus-string union, `Known` names the enum-typed arm and `Custom` names the raw-string arm. An extensible enum still accepts future strings; decoding can therefore select `Known` for an unrecognized value according to the existing branch order. These names are not a new known/unknown classification rule.

TypeScript uses structural unions and quoted wire-property names. Parentheses preserve nested intersection/union precedence; required-only constraints cannot escape their containing object. Its open `UnknownVariant` type still accepts arbitrary tagged objects: canonical parsing, not static typing alone, enforces validity of known tags.

Runtime loop indexes, schema-descriptor `variants`, generic parameters, and underscore-prefixed Python implementation hints are not public naming defects. Whole-output regression gates also inspect public declarations, enum arms, facade methods, parameters, and aliases.

### Go nullability and presence

A conservatively recognized `null | T` is `Nullable[T]`; an optional nullable property is `Optional[Nullable[T]]`. These dimensions are independent:

| Wire state | Model state |
| --- | --- |
| Optional member absent | `Optional.Present == false` |
| Member explicitly null | `Nullable.Valid == false` |
| Non-null member (including `false`, `0`, or `""`) | `Nullable.Valid == true`, with its payload in `Value` |

`Null[T]()` creates explicit null; `NonNull(value)` selects a payload. Encoding rejects a selected payload that itself serializes to JSON null, including nil pointers, slices, maps, or raw messages. Decoding null clears an earlier non-null payload. Named nullable references are values, not nullable pointers that could bypass wrapper decoding.

`state.NoCandidate()` encodes null. `state.Candidate(nil)` encodes `{ "value": null }`, a present candidate with a null application value. Required nullable absence is rejected by the original descriptor checks in the named `Parse*` entrypoints. Generated object unmarshalling also rejects a missing required nullable member specifically, so absence cannot silently become the wrapper’s zero/null state; this is not universal required-member enforcement, and direct `json.Unmarshal` is not a complete schema validator.

Renaming generated symbols and introducing `Nullable[T]` are source-level API changes even when JSON is unchanged. Consumers must migrate renamed members and types; positional aliases are not retained. Regenerate artifacts from a committed generator revision and update SDK CI source pins with their locks.

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
accepts flattened `event.ToolBeforeInput[T]` (`CallID`, `Name`, `Input`,
`Path`, `Origin`); the runtime decodes accepted effective input separately.
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

## Python semantic facade

`python-facade` emits runtime keyword-only dictionary constructors for every
resolved object root, definition, and nested object, separately from `generated.py`
wire parsing. Required fields remain required; optional fields preserve absence,
explicit null, and schema defaults. Literal tags and protocol version are constructor
conveniences only. Unknown fields remain intact and constructors do not replace
canonical validation.

Generated `_models` exports the complete model surface; `event`, `tool`, `effect`,
`capability`, `transport`, and `subscription` expose semantic aliases. Runtime-owned
`registration.py` can re-export `_models.registration` without losing its validator.
`tool.Call`, `tool.Input`, `tool.Origin`, and `tool.Path.NATIVE` support host event
construction. Path is an open schema string, not an alias for tool origin; `NATIVE`
is the documented canonical example, not a closed list of all paths.

`_boundaries.BoundaryMixin` supplies all 32 async named boundaries. The handwritten
`Hooks` class implements `dispatch(event_name, input, **kwargs)` and inherits this
mixin. Host input projections omit SDK-owned envelope fields.

Capability grants are generated from the capabilities schema's effect vocabulary
and nested target dimensions. For example,
`capability.Declaration(modes=[capability.Mode.INTERCEPT], grants=[capability.Allow(),
capability.ModifyInput(replace=True)])` produces the canonical declaration without
repeating `modify` as both an effect and target key. Grants combine explicit authority;
delivery modes and elicitation form/URL grants are never inferred. Canonical mappings
remain usable directly. Conflicting scalar grant metadata raises `ValueError`.

The driver targets `python-sdk/src/agenthooksprotocol`, formats every generated
Python file with the pinned formatter, and records the immutable generator source
commit in the lock. It never overwrites the package initializer or runtime modules.
## Go Hooks and typed capability options

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
options remain available for advanced use. These advanced raw options never automatically add effects,
modes, flow grants, or injection grants. Boundary-specific capability constructors
retain their advanced wire signatures. All target helpers come from the schema
object graph; no handwritten list of event boundaries or modification targets is
maintained.


## Shared host ergonomics

`src/ergonomics.rs` is SDK-only semantic metadata, not a second wire schema.
All four emitters use its host-input projection. Required `call` and `tool`
wrappers are flattened (`call.id` becomes `callId`, `tool.name/input/origin`
become `name/input/origin`); other nested host facts retain their identity.
Application-owned arguments are never flattened. Optional wrappers retain their
presence and conditional requirements. Host `id` and `time` are optional,
`parentEventId` is preserved, and source/type/protocol version and the configured
session-start manifest remain runtime-owned. The mapping rejects flat-name
collisions and unresolved required wrappers. Assembled requests still require
canonical validation before delivery.

Content-source slots are discovered by following canonical `ContentItem`
references, including array indices and nested facts. Examples include
`instructions`, `summary`, `items`, and `fileChangesBefore`. Native readable
sources remain out of band: only the runtime can select, snapshot, upload and
publish descriptors during the owned call. Slot metadata neither reads a source
nor grants body access. Raw wire content descriptors are unchanged.

Delivery diagnostic codes preserve the existing Go vocabulary across SDKs:
`protocol_rejection`, `remote_rpc`, `transport`, `cancelled`,
`deadline_exceeded`, `preparation`, and `capacity`. These are causes, separate
from delivery stage. A malformed JSON-RPC envelope is protocol rejection, not a
legitimate remote RPC error. Runtime integration owns attribution, failure policy,
synthetic-denial evidence and redaction; generated codes introduce no wire error
codes and no second error framework.

Generated capability composition is separate from advanced raw constructors.
`intercept()` deliberately advertises both intercept and observe, while
`observe()` advertises observation only. Effects and their explicit target grants
are constructed together; form and URL elicitation each require an explicit
grant. Raw omitted fields retain their exact original semantics. Builders are
reusable without shared mutable declarations. Boundary compatibility and
per-call narrowing remain runtime admission checks, not proof that the host
implements an advertised effect.

Initial-state helpers explicitly construct a supplied native decision for this
occurrence. They are not backend allow/deny effects or default approval. No
candidate is canonical `candidate: null`; a supplied JSON-null candidate is
`candidate: {value: null}`. Provenance is ordinary descriptive metadata, not
execution evidence. Settled permission and accepted-input accessors remain
runtime-owned and must not imply authorization merely from successful decoding.

### Isolated staging and verification

Do not regenerate into SDK repositories while their runtimes are being edited.
After committing generator changes, stage all outputs and source locks with:

```sh
python3 -m pip install ruff==0.12.12
python3 tools/generate_sdk.py --output-dir /tmp/ahp-codegen-stage
cargo test --locked --manifest-path tools/sdk-codegen/Cargo.toml
python3 -m unittest tools.tests.test_sdk_generation
```

The staging tree has `typescript/`, `python/`, `go/`, and `rust/` directories.
TypeScript and Rust ergonomic exports accompany their ordinary generated wire
file; Python and Go also emit semantic modules/packages. The regular per-SDK
driver flags install the same artifacts when their owner is ready. Run generation
again after any generator commit so `sourceCommit` names the committed source,
not an earlier checkout with uncommitted generator changes.

### Runtime integration interfaces

- **Go:** `event.ToolBeforeInput[T]` has flattened facts and a canonical
  `MarshalJSON`; every event input implements
  `AHPContentSources() map[string]*content.Source`. Named source fields use
  `*content.Source` or index-preserving `[]*content.Source` and never appear in
  JSON. Paths are event-relative (`/instructions`, `/items/1`). The handwritten
  `content` leaf package avoids an event/client import cycle. The runtime must
  collect bindings before serialization in either delivery mode. `capability.Event`
  and `capability.Mode` can be runtime aliases. Generic receiver methods require
  the declared Go 1.27 toolchain.
- **Python:** `event.*Input.to_wire()` returns canonical host facts. Immutable
  `bind_<slot>_source` methods retain sources outside the mapping and expose
  `content_sources`. `_boundaries.CONTENT_SOURCE_SLOTS` maps event-qualified slot
  keys to event name and canonical path tuple; `*` denotes the concrete array
  index supplied by the binder. Collect bindings before `to_wire()`. `state`,
  `permission`, `candidate`, and `diagnostics` are generated semantic modules;
  the package initializer remains handwritten.
- **TypeScript:** `EventInputs` maps event selectors to flattened input types;
  `toEventInput(type, input)` performs the generated canonical projection.
  `contentSlots[event].slot(index?, source)` constructs named out-of-band binding
  records. Runtime exports can expose these generated helpers without maintaining
  another event-field or content-slot inventory.
- **Rust:** `ergonomic_inputs::*Input` has required-fact constructors, optional
  field setters and fallible `to_event_value()`. Named `<boundary>_sources`
  modules create out-of-band generic source bindings. The generated
  `ahp_ergonomic_hook_methods!` macro calls runtime `input_for(name, input,
  Input::to_event_value)` and returns `hooks::InputBoundary<'_, Input>`. It retains
  the input and projector without serializing until the operation is polled, so
  projection consumes the operation budget. The generated tool boundary is named
  `tool_before_event` to coexist with the handwritten typed primary `tool_before`.
  Existing low-level wire boundary macros remain available. `capability::Event`
  reexports `EventType`; schema-derived `EffectType` and `ModifyTarget` expose
  canonical serde tags and `as_str()` without duplicating wire effect models.

These seams do not replace capability compatibility/narrowing checks, canonical
request validation, content authorization, result settlement or shutdown. Those
remain handwritten runtime responsibilities and require SDK tests in addition to
generator consumer checks.

Known event selectors are available as TypeScript `EventType`/`events`, Rust
`EventType` (canonical serde tags and `as_str()`), Python `event.Type`, and Go
`event.Type`/constants. These describe known SDK selectors; raw wire extension
strings remain supported through low-level APIs. TypeScript host tool-input
projections preserve application generics (`ToolBeforeInput<T = unknown>`),
without casting accepted result input back to the proposed type. Rust
`state::Candidate::try_new`, `effects::try_return`, and target `try_replace`/
`try_merge` helpers preserve ordinary serializer errors for native values.

Capability continuation counts are nonnegative interoperable integers. Generated
Go and Python composition reject negative or out-of-range counts before producing
a declaration (Python also rejects booleans/fractions and missing continue counts).
TypeScript already enforces this range; Rust uses unsigned counts and rejects
values above the safe range. Raw manifests still undergo ordinary canonical
validation; structural parsing alone does not prove numeric validity.

## Go structural decoding contract

Every generated concrete model decoder uses the same `checkNode` descriptor
engine as `Parse*`. Named primitives, literal/enum values, objects (including
flattened intersections), arrays, and unions reject applicable structural errors.
Reference aliases inherit their target's decoder. Nullable aliases use generic
`Nullable[T]`: it handles null and delegates non-null decoding to its payload model.
Numeric, array, and constrained raw-backed nullable payloads receive checked model types when the built-in
Go JSON decoder cannot enforce their structural rules.

Descriptors are cached at package initialization. Public decoding validates once,
then private hydration methods decode the checked subtree; pointer and slice
traversal retain this private path. `Parse*` validates for structured diagnostics
and hydrates through the same path, rather than validating every nested object
again. Union hydration still checks alternatives to select the public arm. No
process-global validation bypass or caller-controlled unchecked API is exposed.
Same-tag alternatives retain source ordering and multiplicity: `oneOf` rejects
multiple matches, while `anyOf` accepts a match. Unknown tags and open enum values
remain warnings for `Parse*`, not direct-decoding errors. Raw JSON and exact
`json.Number` representations are retained by their existing model types.

The contract is generated structural validation, **not full canonical JSON Schema
validation**. The IR does not carry string length/pattern/format, numeric bounds,
or all array/object constraints. The SDK forward-compatible profile preserves
unknown object members, including canonical closed objects; it does not enforce
`additionalProperties: false`. Canonical server validators and caller/request-
dependent checks remain separate. Neither direct decoding nor `Parse*` replaces
those validators.

Direct decoding of previously tolerated invalid models is a behavioral break.
Named primitive aliases become defined types to attach decoders (for example,
`ReverseDnsName(existingString)`); reference aliases use target values instead
of pointers so null cannot bypass decoding. Nullable aliases retain their generic
target types. Explicit caller-owned pointers still follow Go null-pointer semantics. The Go facade converts its string backend-ID argument
without changing the convenience constructor signature.

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

Cross-repository writes reuse the AHP bot. Configure organization Actions secrets `AHP_BOT_APP_ID` and `AHP_BOT_APP_PRIVATE_KEY`, restricted to the `agent-hooks-protocol` and `website` repositories. SDK sync requires **Contents**, **Pull requests**, and **Workflows** read and write permissions approved on the app installation. Each synchronization job mints a short-lived token scoped to its allowlisted target repository. Website dispatch and sync use the same credentials but request only Contents write permission for `website`. Do not expose these credentials to pull-request workflows. See [SDK sync app permissions](../README.md#sdk-sync-app-permissions) for setup and migration order.

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
  JSON. Paths are event-relative (`/items/1`, `/items/2/parts/5`); nil rows and nil entries are gaps.
  Inline text-only slots have no source binding. The handwritten
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

### Cross-language decoding and construction contract

All SDK-owned wire decoding entrypoints share the original structural descriptor
engine with their rich `Parse` APIs. Public projection does not remove constraints
from validation. Parse retains diagnostics, warnings, and raw data; successful
decoding is not canonical validation or authorization.

| SDK | Checked entrypoints | Unchecked construction boundary |
| --- | --- | --- |
| Go | Generated `json.Unmarshal` / `Decoder.Decode` and `Parse*` | Struct literals, field assignment, and explicit caller-owned nullable pointers |
| Rust | Generated Serde `Deserialize` and `parse_*` | Struct literals, infallible builders, and later mutation; validate at a decode/parse boundary |
| Python | Wire facade constructors, `Model.from_dict`, and generated `parse_*` | `json.loads`, TypedDict annotations, input projections, and later dictionary mutation |
| TypeScript | Generated `parse*` on JSON text or JSON-compatible values | `JSON.parse`, interfaces, type assertions, object literals, and later mutation |

Direct decoders reject structural errors such as absent required fields, malformed
known variants, literal mismatches, forbidden-property combinations, and ambiguous
`oneOf` alternatives. Unknown enum strings and unknown variants retain their
warning-based forward compatibility, and unrelated extensions survive roundtrip.
Canonical validators and request-context admission checks remain separate.

Descriptors are cached. Go uses private checked hydration; Rust uses private,
synchronous scoped hydration so nested Serde model calls do not repeatedly validate
whole subtrees. Python wire parsing validates a supplied tree without recursively
calling public constructors. TypeScript parses the original descriptor directly
and does not introduce a separate model-hydration validator.

| SDK | Absent / null / value | Numeric policy |
| --- | --- | --- |
| Go | `Optional[T]` for absence; `Nullable[T]` for null, composed when needed | `json.Number` and raw JSON retain exact numeric text |
| Rust | `Presence::Missing` / `Present`; generated nullable alternatives for null | `JsonNumber` with arbitrary precision; checked `Integer` for schema integers |
| Python | Mapping membership distinguishes absence; `None` means explicit null | Integer values and `Decimal` decoding retain precision; caller-provided floats already have float precision |
| TypeScript | Missing member versus present member; `null` remains distinct | Finite IEEE-754 `number`; JSON parsing cannot retain arbitrary numeric precision or token spelling |

Schema integer positions are restricted to mathematically integral values within
±9,007,199,254,740,991. This is not a blanket bound on unconstrained JSON payloads.
Candidate null and candidate `{value: null}` remain distinct, as do zero, false,
and empty values. A required nullable member must still be present.

The shared `tests/structural-acceptance.json` matrix exercises expected acceptance,
not merely agreement between two potentially incorrect paths. Every target also
checks warnings and accepted roundtrips. Language-specific exported consumer tests
cover original composed constraints, direct nested types, and numeric boundaries.

These changes are behavior-breaking for previously tolerated invalid direct
Rust decoding and Python wire construction. Rust constrained primitive aliases
may become checked newtypes. Capability queries are additive. TypeScript rejects
non-JSON object inputs rather than silently discarding properties; its existing
native raw-JSON/type-annotation boundary remains unchanged.

Python wire constructors and `from_dict` both hydrate nested mappings into typed
models after validation. Explicit Python-name/wire-name duplicate assignments
(for example `tool_name=` with `toolName=`) raise `TypeError`, even when values
agree. A lone wire spelling for an optional/defaulted field is preserved and
validated; required constructor arguments retain their documented Python spelling.
`from_dict` does not reinterpret aliases, so distinct wire and extension keys stay
distinct. Family queries include composed per-occurrence capability arrays, not
only the generic capability model; effect-payload arrays do not expose queries.

### Typed composition payloads

Composition is a validation rule, not a reason to erase a model's fields. Object
intersections project their declared fields into typed public models, including
location/evidence predicates such as “url or gaps” and “command, args, cwd or
gaps.” Predicate alternatives affect presence requirements; they do not replace
the sibling declaration of a gap array with arbitrary JSON. Properties required
only by some alternatives remain optional in the projected model. The original
schema descriptor—not the projection—checks the alternatives and intersections
at each language's existing validation boundary.

MCP HTTP, SSE, stdio, and custom-transport payloads expose typed location fields
and gap records. Construction and field access do not require maps, raw JSON, or
serialization. Unknown object members and unknown transport variants retain the
existing forward-compatible behavior. TypeScript already represents these
constraints with native intersections/unions; its consumer probes check the
known branches separately from the intentionally open unknown-variant arm.

Actual application-owned JSON remains JSON: native event payloads, tool inputs
and outputs, candidate values, extension values, and genuinely unconstrained
schema fields are not assigned invented protocol models. Public model typing is
not a replacement for canonical validators or caller-context checks. In
particular, the generated structural IR still omits canonical constraints such
as string patterns, numeric bounds, and some conditional keywords.

This is a source-level API migration: former Rust JSON newtypes become structs,
arrays, scalar models, or semantic enums; Go raw composition arms become typed
models; Python facade parameters now name their generated nested models. Build
those models instead of supplying raw JSON where a schema describes the fields.
`ModelVisibleItem` exposes composed content variants with a typed required role.

Python facade objects remain mappings and now also provide typed read-only
attributes. An optional attribute returns `None` when absent; mapping membership
continues to distinguish absence from an explicit null. Accessors colliding with
dictionary methods use a trailing underscore (`items_`), leaving `dict.items()`
usable. Wire parsers still return their lossless mapping/TypedDict API, not
hydrated facade instances. Attribute access is available on constructed facade
objects. Type annotations do not replace runtime parsing or validate arbitrary
manual mapping mutations.

### Go capability queries

Go exposes `req.Params.Capabilities.Supports(ahp.EffectDeny)` and
`capabilities.Supports(ahp.EffectName("vendor.custom"))`. The generated
`EffectNameDeny`, `EffectNameModify`, and the other `EffectName*` constants
identify effect families; `EffectDeny` aliases `EffectNameDeny`. This avoids
collisions with existing payload types such as `EffectModify`. Queries inspect typed fields
without serialization and accept the known and custom string representations.
Capability grant builders use the same family membership query for deduplication.

`Supports` reports only advertised family membership: it does not authorize
execution or imply a target, operation, delivery mode, or per-call grant. A nested
modify grant without the `modify` family returns false; a `modify` family alone
does not grant any modification operation. Continue to use the existing grant
builders and host/request validation for operation constraints. Equivalent queries
are available in every SDK without a new operation-authorization API:

| SDK | Family query |
| --- | --- |
| Go | `caps.Supports(ahp.EffectDeny)` |
| Rust | `caps.supports(EffectId::Deny)` |
| Python | `caps.supports(capability.EffectName.DENY)` |
| TypeScript | `supports(caps, effectNames.deny)` |

These work on generic and incoming per-occurrence capabilities. Custom family
strings remain supported. Shared semantic identifiers are derived from the schema;
query helpers do not infer family membership from nested grants.

### Shared constrained references

The `Event` root is the shared public event type for intercept and observe. A
method-specific subset is expressed as `$ref: event.schema.json` with a sibling
`oneOf` containing the permitted original event references. Codegen retains both
constraints in its validation descriptor, but projects that constrained reference
to the same named public model in all four targets, without a wrapper or adapter.
Known events excluded by the subset are rejected; truly unknown event tags retain
forward-compatible unknown-variant diagnostics. Do not replace this constraint
with an open string enum or a nested object predicate.

Explicit `not: {required: [...]}` predicates, including unconditional `allOf`
branches, are carried as forbidden-property sets. Thus `ContentReference` rejects
receipt-only `size` and `sha256` by presence (including null), while unrelated
unknown extra fields retain the existing forward-compatible parser behavior.
`ContentUploadReceipt` is a distinct root for upload size and digest metadata.

### Existing explicit source bindings

Published singleton and one-index source APIs remain for unchanged slots such as
`tool.before.items` and `tool.after.fileChangesBefore`. New canonical message
parts do not introduce indexed caller APIs: embed the owner in the attachment
part instead. Internal slot metadata retains arbitrary nested paths for runtime
extraction; it is not a separate public binding API.

### Direct owned attachments in host inputs

Use host message/part adapters for ordinary construction, not separately indexed
bindings. Wire models and parse/encode functions remain pure JSON. These adapters
visit only schema-owned content slots; tool input, native payloads and extension
objects are not searched for sources. Text stays inline. An owned attachment is
retained outside the wire value without reading, opening, closing or uploading it.

Python event inputs accept canonical host dictionaries directly:

```python
from sdk._models import OwnedAttachment
from sdk.event import ContextCompactBeforeInput

host = ContextCompactBeforeInput(trigger="manual", items=[{
    "role": "user",
    "parts": [
        {"kind": "text", "text": "Summarize this"},
        {"kind": "attachment", "mediaType": "image/png",
         "body": OwnedAttachment(source)},
    ],
}])

# Pass host directly to the runtime boundary method.
```

`source` is the runtime's owned source, not a JSON reference. Python slot keys
remain compatible with `CONTENT_SOURCE_SLOTS` (for example,
`context.compact.before.items_parts[0][1]`). Supplied part/message fields are
preserved. Missing IDs receive synthesized provenance once per constructed input.

TypeScript exposes canonical typed host inputs:

```typescript
const input: HostEventInputs<typeof source>["context.compact.before"] = {
  trigger: "manual",
  items: [{role: "user", parts: [
    {kind: "text", text: "Summarize this"},
    {kind: "attachment", mediaType: "image/png", body: ownedAttachment(source)},
  ]}],
};
// Pass input directly to the runtime boundary method once wired.
// Applications do not call the internal _projectHostInput conversion hook.
```

Bindings contain array-index paths such as `["items", 0, "parts", 1]`.
Owned attachments project initially to metadata selection; `pending` identifies
parts that runtime preparation must restore to body selection with resolved refs.
Synthesized identities are stable for the lifetime of each host object.

Go retains existing wire fields and adds optional schema-derived host overrides:

```go
messages := []*event.ModelVisibleItemInput{{
    ModelVisibleItem: ahp.ModelVisibleItem{Role: "user"},
    Parts: []*event.ContentPartInput{
        {Text: &ahp.TextBodyPart{Text: "Summarize this"}},
        {Attachment: &event.AttachmentBodyInput{
            AttachmentBodyPart: ahp.AttachmentBodyPart{MediaType: "image/png"},
            Body: source,
        }},
    },
}}
input := event.ContextCompactBeforeInput{Trigger: "manual", ItemsHost: &messages}
// Pass input directly to the runtime boundary method.
// The runtime collects /items/0/parts/1 internally.
```

`ItemsHost` overrides only `items`, including advanced bindings within that
subtree. Nil slices/entries preserve their positions for extraction; malformed
host parts fail encoding. Missing identities are initialized once on host objects;
these mutable inputs must not be encoded or modified concurrently.

Rust host envelopes own arbitrary non-serializable source types:

```rust
use sdk::ergonomic_inputs::*;
use sdk::{CanonicalMessageRole, ContextCompactBeforeInputTrigger};

let input = ContextCompactBeforeInput::new(
    vec![], ContextCompactBeforeInputTrigger::Manual,
);
let host = input.with_sources().with_items(vec![MessageInput::from_parts(
    CanonicalMessageRole::User,
    vec![PartInput::inline_text("Summarize this"),
         PartInput::owned(source, "image/png")],
)]);
// Pass host directly to the envelope-aware runtime method once wired.
```

Explicit metadata constructors preserve IDs, categories, media types and
provenance. Convenience constructors generate identities once, not per encoding.
Rust bindings use string-segment paths such as `["items", "0", "parts", "1"]`.

#### Required handwritten runtime wiring

These are generator adapters, not an implemented upload/transport feature:

- Python and Go runtimes must collect `content_sources`/`AHPContentSources()`
  before encoding, plan those sources using the existing ownership rules, and
  replace pending attachment descriptors before validation and send. Python keys
  must be resolved using `CONTENT_SOURCE_SLOTS`; Go keys are JSON Pointers.
- Rust runtime methods accept the constructed `HostInput<I, S>` and internally
  consume the doc-hidden `ProjectHostInput<S>::into_host_event()` hook. Existing
  wire-input hook methods intentionally remain unchanged. Feed the returned
  `(Value, Vec<ContentSourceBinding<S>>)` to planning before delivery.
- TypeScript runtime methods accept `HostEventInputs<S>[K]` and internally invoke
  `_projectHostInput<K, S>(type, input) -> HostInputProjection<S>`. Feed `bindings`
  to planning and use `pending` to restore body selection and resolved refs.
  This hook is exported only for runtime integration and marked `@internal`;
  applications must not call it as an additional construction step.

Python and Go use the local staging placeholder `ahp:owned:pending`; Rust uses
`ahp:host-pending`. Neither is an uploaded receipt or a reference to send as-is.
No cancellation, retry, transfer-of-ownership or source-consumption behavior is
added by conversion. Runtime planning continues to own those policies. Host input
adapters are outbound conversions; wire decoders do not create local source
handles or fetch attachments.

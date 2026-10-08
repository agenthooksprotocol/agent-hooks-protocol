const fs = require("node:fs");
const path = require("node:path");

const [generatedPath, repositoryPath] = process.argv.slice(2);
if (!generatedPath || !repositoryPath) {
  throw new Error("usage: node typescript-smoke.cjs <generated.js> <repository>");
}
const sdk = require(path.resolve(generatedPath));
const fixture = (relative) => JSON.parse(fs.readFileSync(path.join(repositoryPath, relative), "utf8"));

const registration = fixture("fixtures/draft/registration/portable.valid.json");
registration.futureRoot = { nested: true };
registration.hooks[0].transport.futureNested = 7;
let result = sdk.parseRegistration(registration);
if (!result.ok) throw new Error(JSON.stringify(result.diagnostics));
let encoded = JSON.parse(sdk.encodeRegistration(result.value));
if (!encoded.futureRoot.nested || encoded.hooks[0].transport.futureNested !== 7) {
  throw new Error("recursive unknown properties were not preserved");
}

registration.hooks[0].transport = { type: "future", deeply: { preserved: true } };
result = sdk.parseRegistration(registration);
if (!result.ok || !result.diagnostics.some((item) => item.code === "unknown_variant")) {
  throw new Error("unknown discriminator variant was not preserved");
}
encoded = JSON.parse(sdk.encodeRegistration(result.value));
if (!encoded.hooks[0].transport.deeply.preserved) throw new Error("unknown variant payload was lost");

const noDefault = fixture("fixtures/draft/registration/portable.valid.json");
delete noDefault.hooks[1].subscriptions[1].includeNative;
result = sdk.parseRegistration(noDefault);
if (!result.ok) throw new Error(JSON.stringify(result.diagnostics));
encoded = JSON.parse(sdk.encodeRegistration(result.value));
if (Object.prototype.hasOwnProperty.call(encoded.hooks[1].subscriptions[1], "includeNative")) {
  throw new Error("decoder fabricated an absent default");
}

const request = fixture("fixtures/draft/http/intercept-request.valid.json");
result = sdk.parseJsonRpcMessage(request);
if (!result.ok) throw new Error(`request envelope did not select exactly one branch: ${JSON.stringify(result.diagnostics)}`);
request.params.event.tool.origin = "future_origin";
result = sdk.parseInterceptRequest(request);
if (!result.ok || !result.diagnostics.some((item) => item.code === "unknown_enum")) {
  throw new Error(`unknown enum value was not preserved: ${JSON.stringify(result.diagnostics)}`);
}

// Nullable candidates preserve the outer null separately from a candidate
// whose application-owned value is null (or another falsy JSON value).
for (const candidate of [null, { value: null }, { value: false }, { value: 0 }, { value: "" }]) {
  const nullableRequest = fixture("fixtures/draft/http/intercept-request.valid.json");
  nullableRequest.params.state = { permission: "none", candidate };
  const parsed = sdk.parseInterceptRequest(nullableRequest);
  if (!parsed.ok) throw new Error(JSON.stringify(parsed.diagnostics));
  const roundTrip = JSON.parse(sdk.encodeInterceptRequest(parsed.value));
  if (JSON.stringify(roundTrip.params.state.candidate) !== JSON.stringify(candidate)) {
    throw new Error("nullable candidate changed its outer-null/value distinction");
  }
}
const absentCandidate = fixture("fixtures/draft/http/intercept-request.valid.json");
absentCandidate.params.state = { permission: "none" };
if (sdk.parseInterceptRequest(absentCandidate).ok) throw new Error("required nullable candidate was fabricated");

const deny = fixture("fixtures/draft/http/deny-response.valid.json");
deny.result.effects[0].code = null;
result = sdk.parseInterceptDenyResponse(deny);
if (result.ok || result.raw.result.effects[0].code !== null) {
  throw new Error("invalid explicit null was not retained separately from absence");
}

const ambiguous = { jsonrpc: "2.0", id: "event-1", result: {}, error: { code: -32600, message: "bad" } };
result = sdk.parseJsonRpcMessage(ambiguous);
if (result.ok || !result.diagnostics.some((item) => item.code === "no_union_match")) {
  throw new Error("invalid result/error envelope selected a union branch");
}

const malformed = fixture("fixtures/draft/registration/portable.valid.json");
malformed.hooks[0].transport = { type: "http" };
result = sdk.parseRegistration(malformed);
if (result.ok || !result.diagnostics.some((item) => item.code === "invalid_known_variant")) {
  throw new Error("malformed known variant fell back instead of failing");
}

for (const entry of fixture("fixtures/draft/manifest.json").cases) {
  if (!entry.expectedValid || !["http-json", "registration-json"].includes(entry.binding)) continue;
  const original = fixture(entry.path);
  const parsed = (entry.binding === "registration-json" ? sdk.parseRegistration : sdk.parseWireMessage)(original);
  if (!parsed.ok) throw new Error(`canonical fixture ${entry.id}: ${JSON.stringify(parsed.diagnostics)}`);
  const encoded = (entry.binding === "registration-json" ? sdk.encodeRegistration : sdk.encodeWireMessage)(parsed.value);
  require("node:assert/strict").deepEqual(JSON.parse(encoded), original);
}
const supplied = fixture("fixtures/draft/http/model-response-intercept-supplied-stop.valid.json").params.event;
require("node:assert/strict").deepEqual(supplied.execution, { status: "skipped", reason: "supplied_result" });
const execution = sdk.parseExecutionEvent(supplied);
if (!execution.ok) throw new Error(`supplied execution: ${JSON.stringify(execution.diagnostics)}`);
require("node:assert/strict").deepEqual(JSON.parse(sdk.encodeExecutionEvent(execution.value)), supplied);
delete supplied.execution.reason;
if (sdk.parseExecutionEvent(supplied).ok) throw new Error("missing skipped reason selected another execution branch");
supplied.execution.reason = null;
if (sdk.parseExecutionEvent(supplied).ok) throw new Error("null skipped reason passed parsing");

for (const transport of ["http", "stdio"]) {
  const missing = fixture(`fixtures/draft/http/mcp-${transport}-location-missing.invalid.json`).params.event;
  if (sdk.parseToolBeforeEvent(missing).ok) throw new Error(`required-only ${transport} location predicate was lost`);
}

// Auth mechanisms are closed even though fields within a recognized binding are open.
for (const type of ["bearer", "unsupported"]) {
  const auth = { type, tokenEnv: "TOKEN", future: { preserved: true } };
  const upload = { endpoint: "https://upload.example/bytes", timeoutMs: 100, maxBytes: 1024, auth };
  const config = fixture("fixtures/draft/registration/portable.valid.json");
  config.hooks[0].authentication = auth;
  for (const parsed of [sdk.parseContentUpload(upload), sdk.parseRegistration(config)]) {
    if (parsed.ok !== (type === "bearer")) throw new Error(`incorrect auth mechanism acceptance: ${type}`);
  }
}

// All structured transports retain typed location and evidence fields. Parsing
// still enforces the known transport's composed location predicates.
for (const connection of [
  { transport: "http", url: "https://mcp.example", gaps: [{ path: "headers", reason: "redacted" }] },
  { transport: "sse", url: "https://mcp.example/events", gaps: [{ path: "headers", reason: "redacted" }] },
  { transport: "stdio", command: "mcp", args: ["--local"], cwd: "/srv", gaps: [{ path: "env", reason: "redacted" }] },
  { transport: "unix", addressForm: "path", address: "/tmp/mcp.sock", gaps: [{ path: "credentials", reason: "redacted" }] },
]) {
  const event = fixture("fixtures/draft/http/mcp-http-gap.valid.json").params.event;
  event.tool.mcp.connection = connection;
  const parsed = sdk.parseToolBeforeEvent(event);
  if (!parsed.ok) throw new Error(`structured transport rejected: ${JSON.stringify(parsed.diagnostics)}`);
  const decoded = parsed.value.tool.mcp.connection;
  if (decoded.transport !== connection.transport || decoded.gaps[0].reason !== "redacted") {
    throw new Error("typed transport fields were not preserved");
  }
  require("node:assert/strict").deepEqual(JSON.parse(sdk.encodeToolBeforeEvent(parsed.value)).tool.mcp.connection, connection);
}
for (const connection of [
  { transport: "http" },
  { transport: "sse", gaps: [{ path: "url", reason: 42 }] },
  { transport: "stdio", command: "mcp", args: [] },
]) {
  const event = fixture("fixtures/draft/http/mcp-http-gap.valid.json").params.event;
  event.tool.mcp.connection = connection;
  if (sdk.parseToolBeforeEvent(event).ok) throw new Error("composed transport predicate was lost");
}

console.log("generated TypeScript codec smoke tests passed");
// Exercise the semantic surface through the same CI consumer entrypoint.
require("./typescript-ergonomics-smoke.cjs");
require("./capability-typescript.cjs");

// Explicitly forbidden metadata is not an ordinary forward-compatible extra.
for (const key of ["size", "sha256"]) {
  for (const value of [null, 0, "a".repeat(64)]) {
    if (sdk.parseContentReference({ref: "opaque", [key]: value}).ok) throw new Error(`forbidden ${key} accepted`);
  }
}
if (!sdk.parseContentReference({ref: "opaque", future: {preserved: true}}).ok) throw new Error("generic reference extras were closed");

const sharedRequest = fixture("fixtures/draft/http/intercept-request.valid.json");
sharedRequest.params.event = {type:"session.end", id:"id", source:"test", time:"2026-01-01T00:00:00Z"};
if (sdk.parseInterceptRequest(sharedRequest).ok) throw new Error("known observe-only event accepted");
sharedRequest.params.event = {type:"future.event", extension:true};
const futureEvent = sdk.parseInterceptRequest(sharedRequest);
if (!futureEvent.ok || !futureEvent.diagnostics.some(d => d.code === "unknown_variant")) throw new Error("unknown event lost");
if (!sdk.parseContentUploadReceipt({ref:"opaque", size:0, sha256:"a".repeat(64)}).ok) throw new Error("receipt rejected");

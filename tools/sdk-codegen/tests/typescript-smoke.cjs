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
const execution = sdk.parseExecutionEvent(supplied);
if (!execution.ok) throw new Error(`supplied execution: ${JSON.stringify(execution.diagnostics)}`);
require("node:assert/strict").deepEqual(JSON.parse(sdk.encodeExecutionEvent(execution.value)), supplied);
delete supplied.execution.subscriptionId;
if (sdk.parseExecutionEvent(supplied).ok) throw new Error("missing supplier selected another execution branch");
supplied.execution.subscriptionId = null;
if (sdk.parseExecutionEvent(supplied).ok) throw new Error("null supplier passed parsing");

for (const transport of ["http", "stdio"]) {
  const missing = fixture(`fixtures/draft/http/mcp-${transport}-location-missing.invalid.json`).params.event;
  if (sdk.parseToolBeforeEvent(missing).ok) throw new Error(`required-only ${transport} location predicate was lost`);
}

console.log("generated TypeScript codec smoke tests passed");

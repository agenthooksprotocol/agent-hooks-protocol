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
request.params.event.tool.kind = "future_tool";
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

console.log("generated TypeScript codec smoke tests passed");

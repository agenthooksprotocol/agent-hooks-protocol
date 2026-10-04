# Cross-language adapter contract v1

Each SDK owns `interop/adapter.json`: `{ "language":"python", "server":[...], "client":[...] }`; commands execute in SDK cwd, append `--config ABSOLUTE_JSON_PATH`.

Server config: `{transport:"http"|"stdio",readinessFile,scenarioFile,auth:{mode:"none"|"bearer"|"oauth"|"mtls"|"workload",...}}`. Bind loopback port zero, write readiness JSON atomically `{endpoint,controlEndpoint,pid}` after listening. Separate control HTTP GET /health -> `{ready:true}`, GET /receipts -> `{requests:[...]}` redacted, POST /release `{barrier}` and POST /shutdown. HTTP AHP POST /intercept and GET /capabilities. Stdio carries full canonical JSON-RPC envelopes, one compact JSON value per line, with methods `hooks/intercept` and `hooks/capabilities`. Interception uses `params.event` and `params.capabilities`; discovery requires `params.protocolVersion: draft` and returns `result.manifest` according to the canonical schemas. No double wrapping or method aliases; stdout exclusively AHP, diagnostics stderr.

Client config: `{transport,endpoint?,serverCommand?:string[],serverCwd?,serverConfig?,scenarioFile,reportFile,auth:{mode,...}}`. Stdio client spawns serverCommand plus --config serverConfig and cleans up. HTTP controller owns server. Validate requests/responses with generated codecs AND canonical schemas, apply effects locally. Write `{language,results:[{id,status,actual,error?}]}`, statuses passed/failed/unsupported/inapplicable. Applicable failure exits nonzero. No credentials or nondeterministic ports in report.

Central scenario file: `{version:1,scenarios:[{id,request,response,expected,tags?:[],barrier?:string}]}`. Request/response canonical draft. Match request.id and request.params.event.id to scenario id. Expected application result partial object: `{decision:"allow"|"deny"|"ask",executed:boolean,input:any,messages:[],result?:any,flow?:string,injections?:[],continuationInstructions?:[],continuationRemaining?:number}`. Compare all expected keys deeply. Each client independently derives result. Unknown effects fail; malformed responses fail before any effects. Server validates receipt before reply. Negative scenarios can specify `expectError:true` instead of expected. Only rejection after successful response acquisition qualifies; transport failures, failed discovery, timeouts, and spawn failures must fail, not satisfy expectError. The central runner owns scenarios/assertions.

The `auth.mode` values above are deployment test-control labels, not portable AHP registration types or capability entries. Portable AHP authentication supports `bearer` and `oauth`; absent registration authentication permits discovery. Acquired workload/federation tokens use bearer credential references in portable registration. TLS client certificates are configured out of band; TLS-only deployments leave HTTP registration authentication absent. Never advertise `mtls` or `workload` as portable authentication capabilities.

Deployment HTTP/TLS/credential-provider tests retain all five modes. HTTP bearer auth.token. OAuth auth.tokenEndpoint/clientId/clientSecret/audience acquires actual token from synthetic local issuer. mTLS auth.caFile/certFile/keyFile uses real TLS handshake. Workload auth.assertion is signed local JWT checked signature/issuer/audience/purpose/expiry using shared public test-only fixture. Unsupported auth explicitly unsupported, never passed. Stdio process trust only; HTTP auth combinations inapplicable. Endpoint auth and content authorization separate. Reject duplicate Authorization fields before dispatch. Disable redirects for credential-bearing requests and token acquisition: credentials must not transfer to another resource. Signed-token purposes use distinct test-only signing keys, issuer/audience/purpose validation, and a fixed fixture clock.

Explicit control barriers, no sleeps for race ordering. Bounded wall-clock watchdogs and strict process cleanup. Capability discovery independent of session. Readiness uses the controller startup watchdog; do not charge process startup against the discovery response timeout. Keep credentials out of reports and logs. Adapters follow [canonical composition semantics](../spec/draft/base/composition.md); `draft-atomic.ts` provides application examples.

The five synthetic `application-invalid` task-input negatives also declare
`hostExpected`. They test the host's positive-integer task schema, not an AHP
protocol constraint. An adapter that accepts the response in the SDK and validates
in userland must report `sdkAccepted:true`, `hostAccepted:false`, and
`rejectionLayer:"host-input-schema"`, with actual output exactly matching
`hostExpected`: execution is refused, but the effective SDK input and messages
remain. The verifier preserves these diagnostics; it must not invent an SDK
rollback. Existing implementations that install an application validator inside
the SDK transaction can still report genuine `actual:{rejected:true}`. This
alternative never applies to protocol/capability/correlation negatives.

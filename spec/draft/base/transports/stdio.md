# stdio binding

## stdio binding
### Framing

<a id="AHP-STDIO-001"></a>
**AHP-STDIO-001 — MUST.** The stdio binding uses one UTF-8 JSON-RPC object per line on standard output, and diagnostics do not appear on standard output.

Each message MUST be one complete JSON-RPC object encoded as UTF-8 and terminated by a single newline. A message MUST NOT contain a literal unescaped newline inside the JSON text.
The backend reads protocol messages from `stdin` and writes protocol messages to `stdout`. It MAY write UTF-8 diagnostics to `stderr`.
The backend MUST NOT write banners, logs, progress indicators, or other non-protocol content to `stdout`.
### Persistent lifecycle
A persistent backend remains alive for multiple messages. The harness MUST correlate responses by JSON-RPC ID and MAY have only one outstanding intercept request in this protocol revision per persistent backend because interceptor evaluation is serial.
The harness SHOULD terminate a persistent backend that repeatedly emits malformed protocol output.
### Process-per-event lifecycle
A process-per-event backend receives exactly one JSON-RPC message on `stdin`, followed by EOF. It writes at most one response and exits.
For an intercept request, exit status `0` indicates that the process completed its protocol exchange. The response body, not the process exit code, determines the semantic effect. Any nonzero exit, missing response, or malformed response is an operational failure.
For an observe notification, the harness does not wait for a semantic response. Process status may be recorded locally but cannot affect agent execution.
### Process security
The harness MUST invoke the configured command and argument array directly without implicit shell interpolation. It SHOULD provide the smallest practical environment and MUST NOT place bearer tokens or unrelated credentials in event payloads.
### Process environment
A stdio transport MAY configure `env`, an object whose keys are environment variable names and whose values are strings. The harness starts the backend with its smallest practical base environment, as above, and then applies every `env` entry. An entry replaces any base value with the same name. Absent `env` adds nothing.
Names and values are literal strings. The harness MUST NOT perform variable expansion, interpolation, shell evaluation, or secret-reference resolution on them.
The harness MUST NOT log `env` values or include them in diagnostics, events, or telemetry. It MAY report variable names.
`env` configures only the backend process. It is not an authentication binding and does not configure, satisfy, or replace the [authentication bindings](../../capability-auth.md#authentication-bindings), including `upload.auth`. The harness MUST resolve a `tokenEnv` reference in its own environment, never from `env` entries, and MUST NOT copy resolved AHP endpoint credentials into the backend environment. Because registration credentials are never literal values, `env` MUST NOT carry AHP endpoint credentials.

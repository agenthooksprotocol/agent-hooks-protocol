# Tool interception

## Effects
### Effect list

<a id="AHP-DEC-001"></a>
**AHP-DEC-001 — MUST.** A successful interception result contains an ordered `effects` array whose members are all valid and advertised for the current interception.

A successful intercept result MUST contain `effects`, which MUST be a JSON array.
The array MAY be empty or contain compound effects. Every member MUST satisfy its schema and the current request capability grants. Unknown effect fields, types, targets, or operations MUST reject the entire response before any effects are published. See [atomic acceptance and composition](../base/composition.md) and [capability bounds](../capability-auth.md).
### `deny`

<a id="AHP-DEC-002"></a>
**AHP-DEC-002 — MUST.** A harness that accepts a valid `deny` effect does not execute the represented operation.

<table>
<tr>
<td>Field</td>
<td>Type</td>
<td>Requirement</td>
<td>Semantics</td>
</tr>
<tr>
<td>`type`</td>
<td>string</td>
<td>REQUIRED</td>
<td>Exact value `deny`.</td>
</tr>
<tr>
<td>`reason`</td>
<td>string</td>
<td>REQUIRED</td>
<td>Non-empty human-readable explanation.</td>
</tr>
<tr>
<td>`code`</td>
<td>string</td>
<td>OPTIONAL</td>
<td>Stable machine-readable reverse-DNS identifier.</td>
</tr>
<tr>
<td>`extensions`</td>
<td>object</td>
<td>OPTIONAL</td>
<td>Namespaced backend metadata.</td>
</tr>
</table>
A valid `deny` effect is an explicit policy result. It MUST deny regardless of whether the interceptor is configured `fail-open` or `fail-closed`.
A backend SHOULD avoid placing secrets, stack traces, or sensitive policy internals in `reason` because the harness may show it to a user or model.
### No-effect and authorization

<a id="AHP-DEC-003"></a>
**AHP-DEC-003 — MUST NOT.** An empty effect list is not an authorization grant and does not bypass harness permissions, sandboxing, or approval.

An empty effect list means only that the current backend has no objection. It cannot:
- Skip remaining interceptors.
- Override a denial.
- Bypass a host permission prompt.
- Disable sandboxing.
- Grant a tool capability.
An advertised `allow` effect may suppress an ordinary prompt, but cannot override deny, ask, managed policy, access checks, or sandbox controls. Relevant changes invalidate approvals as defined by the composition rules.

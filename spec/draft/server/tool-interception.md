# Tool interception

## Effects
### Effect list

<a id="AHP-DEC-001"></a>
**AHP-DEC-001 — MUST.** A successful interception result contains either an empty `effects` array or exactly one valid `deny` effect.

A successful intercept result MUST contain `effects`, which MUST be a JSON array.
For v0.1, the array MUST contain either:
- No effects, or
- Exactly one `deny` effect.
Multiple `deny` effects from one backend are invalid. Effect ordering and combinations are reserved for a later version.
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
### No `allow` effect

<a id="AHP-DEC-003"></a>
**AHP-DEC-003 — MUST NOT.** An empty effect list is not an authorization grant and does not bypass harness permissions, sandboxing, or approval.

AHP v0.1 has no `allow` effect. An empty effect list means only that the current backend has no objection. It's not possible to:
- Skip remaining interceptors.
- Override a denial.
- Bypass a host permission prompt.
- Disable sandboxing.
- Grant a tool capability.
This distinction avoids importing incompatible provider meanings of “allow.”

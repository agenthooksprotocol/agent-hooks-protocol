# Protocol model and versioning

## Protocol model
### JSON-RPC

<a id="AHP-RPC-001"></a>
**AHP-RPC-001 — MUST.** Protocol messages use UTF-8 JSON-RPC 2.0.
<a id="AHP-RPC-002"></a>
**AHP-RPC-002 — MUST.** An interception that expects a decision uses a `hooks/intercept` request; one-way observation uses a `hooks/observe` notification.

AHP uses [JSON-RPC 2.0](https://www.jsonrpc.org/specification). All messages MUST be UTF-8 encoded.
This protocol revision defines two methods:
<table>
<tr>
<td>Method</td>
<td>JSON-RPC type</td>
<td>Requirement</td>
<td>Purpose</td>
</tr>
<tr>
<td>`hooks/intercept`</td>
<td>Request</td>
<td>Tool Interception profile</td>
<td>Ask a backend for effects before continuing an operation.</td>
</tr>
<tr>
<td>`hooks/observe`</td>
<td>Notification</td>
<td>Lifecycle Observation profile</td>
<td>Deliver a one-way lifecycle event.</td>
</tr>
</table>
Batch JSON-RPC messages MUST NOT be used in this protocol revision.
### Versioning

<a id="AHP-VER-001"></a>
**AHP-VER-001 — MUST.** Every request, notification, and successful result carries `protocolVersion` equal to its protocol snapshot identifier.

Every AHP params object and every successful result MUST contain `protocolVersion` equal to the selected protocol snapshot identifier. The mutable working snapshot uses the literal identifier `draft` on the wire. Publication assigns the release date in `YYYY-MM-DD` form to the copied snapshot, its schemas and manifests, and every on-wire `protocolVersion`.
A backend that does not support the supplied version MUST return the AHP JSON-RPC error `unsupported_protocol_version`. Because process-per-event backends cannot rely on a prior handshake, version and capabilities are carried on each intercept request. Implementations use exact-version matching; date ordering does not imply wire compatibility.
### Unknown fields

<a id="AHP-CORE-003"></a>
**AHP-CORE-003 — MUST.** Receivers ignore unknown object fields unless a claimed extension defines their meaning, while senders do not use unknown fields to alter core semantics.

Receivers MUST ignore unknown fields in otherwise valid objects. Senders MUST NOT infer that an ignored optional field changed backend behavior.
Unknown event types, effect types, and enum values are not ordinary unknown fields. They MUST be handled as unsupported protocol semantics.
### JSON values
Fields described as JSON objects MUST contain objects, not encoded JSON strings. Tool input and output MAY contain any valid JSON values only where explicitly allowed by their event schema.

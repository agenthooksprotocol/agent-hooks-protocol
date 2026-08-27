# JSON-RPC errors

## JSON-RPC errors
Backends use standard JSON-RPC error responses. AHP reserves these server-error codes:
<table>
<tr>
<td>Code</td>
<td>Name</td>
<td>Meaning</td>
</tr>
<tr>
<td>`-32001`</td>
<td>`unsupported_protocol_version`</td>
<td>Backend does not support `params.protocolVersion`.</td>
</tr>
<tr>
<td>`-32002`</td>
<td>`unsupported_event`</td>
<td>Backend does not support the supplied event type in this method.</td>
</tr>
<tr>
<td>`-32003`</td>
<td>`backend_unavailable`</td>
<td>Backend is temporarily unable to evaluate the request.</td>
</tr>
<tr>
<td>`-32004`</td>
<td>`backend_internal_error`</td>
<td>Backend failed while evaluating the request.</td>
</tr>
</table>
Example:
```json
{
  "jsonrpc": "2.0",
  "id": "evt_01JQ8Z2Y6YR0H8N7Q2M3X4V5W6",
  "error": {
    "code": -32001,
    "message": "Unsupported AHP protocol version",
    "data": {
      "supported": ["draft"]
    }
  }
}
```
Any JSON-RPC error is an operational failure and is handled using the subscription's failure policy. Error `data` MUST NOT contain secrets.

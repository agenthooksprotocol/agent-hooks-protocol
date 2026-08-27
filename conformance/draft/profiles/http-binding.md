# HTTP binding profile

**Status: Working Draft — `draft`.**

Each POST request body contains exactly one JSON-RPC object with a JSON media type. A request receives at most one JSON-RPC response object. A notification can use an empty successful HTTP response and never receives a JSON-RPC response body.

Portable bearer authentication stores only an environment-variable reference in registration. The resolved token is sent in the HTTP `Authorization` header and is absent from registration, events, logs, denial reasons, and JSON-RPC error data.

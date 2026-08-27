# Proposed implementation plan

## Proposed implementation plan
1. Publish a JSON Schema for the common envelope, `tool.before`, `deny`, and registration document.
2. Add golden request and response fixtures for stdio and HTTP.
3. Implement an AHP encoder and decoder in `agenthooks` while preserving its native adapter layer.
4. Build a fake backend that can return no effect, deny, malformed output, timeout, and JSON-RPC errors.
5. Build a harness conformance runner covering ordering and failure policy.
6. Test adapters for at least two native harness dialects.
7. Implement one independent policy backend.
8. Resolve the open questions using implementation evidence before publishing the first date-stamped stable release.

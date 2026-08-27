# Capabilities

## 11. Capabilities

<a id="AHP-CAP-001"></a>
**AHP-CAP-001 — MUST.** A harness advertises only effects it can enforce for the current interception, and a backend returns only advertised effects.

Every `hooks/intercept` request MUST include:
```json
{
  "capabilities": {
    "effects": ["deny"]
  }
}
```
A harness claiming the v0.1 Tool Interception profile MUST advertise `deny` for `tool.before`. A backend MUST return only effects advertised in that request.
Returning an unadvertised effect is an operational protocol failure, not an implicit no-op. The harness MUST apply the interceptor's configured failure policy.
Capabilities describe the current event and runtime, not only the harness product. A backend MUST NOT infer capabilities from provider name, provider version, transport, or native payload.
Future standards may define additional core effects. Experimental effects MUST use reverse-DNS names, and the harness must advertise the exact extension effect before a backend returns it.

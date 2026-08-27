# Composition

## Composition
### Interceptor order

<a id="AHP-COMP-001"></a>
**AHP-COMP-001 — MUST.** Matching interceptors run serially in deterministic registration order and evaluation stops on denial or a fail-closed operational failure.

The portable registration document uses an ordered `hooks` array. For a given event, the harness MUST evaluate matching interceptors serially in array order.
For each interceptor:
1. The harness sends `hooks/intercept`.
2. If the result has no effects, the harness proceeds to the next interceptor.
3. If the result contains `deny`, the harness stops the chain and denies the operation.
4. If an operational failure occurs under `fail-open`, the harness records the failure locally and proceeds to the next interceptor.
5. If an operational failure occurs under `fail-closed`, the harness stops the chain and denies the operation.
This order is normative. Harnesses MUST NOT run interceptors concurrently in this protocol revision.
### Observers
Observer delivery MUST NOT delay the interceptor chain or tool execution. A harness MAY dispatch observer notifications concurrently.
Ordering between observer delivery and interceptor completion is unspecified. Backends that make control decisions are responsible for exporting their own durable decision audit, preferably through OpenTelemetry or another dedicated audit pipeline.
### Overlapping subscriptions
A registration document MUST NOT configure the same backend more than once for the same event and mode. A harness MUST reject ambiguous duplicate subscriptions at configuration load time.

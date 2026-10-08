#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: go-smoke.sh <generated.go> <repository>" >&2
  exit 2
fi

generated=$(cd "$(dirname "$1")" && pwd)/$(basename "$1")
repository=$(cd "$2" && pwd)
tmp=$(mktemp -d "${TMPDIR:-/tmp}/ahp-go-smoke.XXXXXX")
trap 'rm -rf "$tmp"' EXIT

cp "$generated" "$tmp/ahp_generated.go"
cat >"$tmp/go.mod" <<'EOF'
module github.com/agenthooksprotocol/go-sdk

go 1.27.0
EOF
cargo run --quiet --locked --manifest-path "$repository/tools/sdk-codegen/Cargo.toml" -- \
  generate --repository "$repository" --revision draft --language go-facade --output "$tmp"
# The handwritten leaf content package owns the source abstraction.
cat >"$tmp/content/source_stub.go" <<'EOF'
package content
import "io"
type Source struct { Reader io.ReadCloser }
func NewSource(reader io.ReadCloser) *Source { return &Source{Reader: reader} }
EOF
# Generated client methods compile against the runtime's explicit shared seam.
cat >"$tmp/client/runtime_stub_test.go" <<'EOF'
package client
import "context"
import "github.com/agenthooksprotocol/go-sdk/event"
import "testing"
import ahp "github.com/agenthooksprotocol/go-sdk"
type Mode string
const (Intercept Mode = "intercept"; Observe Mode = "observe")
type EventCapabilities struct { Modes []Mode; Capabilities *ahp.Capabilities }
type Options struct { Events map[string]EventCapabilities }
func New(ahp.Registration,Options)(*Hooks,error){return &Hooks{},nil}
func (*Hooks) Close()error{return nil}
type Hooks struct{}
type Client = Hooks
type InterceptOption func()
type Result struct{}
type ToolBeforeResult[T any] struct { Input T }
func (*Hooks) intercept(context.Context, string, any, ...InterceptOption) (*Result,error) { return &Result{},nil }
func decodeToolBefore[T any](*Result,error)(*ToolBeforeResult[T],error){return &ToolBeforeResult[T]{},nil}
func TestGenericInference(t *testing.T) {
 type arguments struct { Path string }
 result,err:=new(Hooks).ToolBefore(context.Background(), event.ToolBeforeInput[arguments]{Name:"read", Origin:"native", Input:arguments{Path:"file"}, ToolKind:ahp.Some("task")})
 if err!=nil {t.Fatal(err)}
 var _ arguments = result.Input
}
EOF
export GOTOOLCHAIN=go1.27.0+auto
goroot=$(go env GOROOT)
"$goroot/bin/gofmt" -w "$tmp"/*/generated.go "$tmp/client/boundaries_generated.go" "$tmp/facade_generated_test.go"
cat >"$tmp/ahp_generated_test.go" <<'EOF'
package ahp

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func fixture(t *testing.T, relative string) map[string]any {
	t.Helper()
	data, err := os.ReadFile(filepath.Join(os.Getenv("AHP_REPOSITORY"), relative))
	if err != nil {
		t.Fatal(err)
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	var value map[string]any
	if err := decoder.Decode(&value); err != nil {
		t.Fatal(err)
	}
	return value
}

func encodedObject(t *testing.T, data []byte, err error) map[string]any {
	t.Helper()
	if err != nil {
		t.Fatal(err)
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	var value map[string]any
	if err := decoder.Decode(&value); err != nil {
		t.Fatal(err)
	}
	return value
}

func inputJSON(t *testing.T, value any) []byte {
	t.Helper()
	data, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	return data
}

func hasDiagnostic(diagnostics []ParseDiagnostic, code DiagnosticCode) bool {
	for _, diagnostic := range diagnostics {
		if diagnostic.Code == code {
			return true
		}
	}
	return false
}

func sharedEventType(request InterceptRequest, observation ObserveNotification) *Event {
    var event *Event = request.Params.Event
    event = observation.Params.Event
    return event
}

func TestSharedEventAndContentReferenceContracts(t *testing.T) {
    for _, key := range []string{"size", "sha256"} {
        for _, value := range []any{nil, 0, "hash"} {
            if ParseContentReference(inputJSON(t, map[string]any{"ref":"opaque", key:value})).OK { t.Fatalf("forbidden %s accepted", key) }
        }
    }
    if !ParseContentReference([]byte(`{"ref":"opaque","future":true}`)).OK { t.Fatal("generic extras closed") }
    if !ParseContentUploadReceipt([]byte(`{"ref":"opaque","size":0,"sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}`)).OK { t.Fatal("receipt rejected") }
    request := fixture(t, "fixtures/draft/http/intercept-request.valid.json")
    request["params"].(map[string]any)["event"] = map[string]any{"type":"session.end", "id":"id", "source":"test", "time":"2026-01-01T00:00:00Z"}
    if ParseInterceptRequest(inputJSON(t, request)).OK { t.Fatal("known observe-only event accepted") }
    request["params"].(map[string]any)["event"] = map[string]any{"type":"future.event", "extension":true}
    parsed := ParseInterceptRequest(inputJSON(t, request))
    if !parsed.OK || !hasDiagnostic(parsed.Diagnostics, DiagnosticUnknownVariant) { t.Fatalf("unknown event lost: %#v", parsed.Diagnostics) }
}

func typedEffects(capabilities Capabilities) []CapabilitiesEffectsItem {
	return capabilities.Effects
}

func TestSemanticCapabilityAlternativeNamesPreserveValues(t *testing.T) {
	var known, custom CapabilitiesEffectsItem
	if err := json.Unmarshal([]byte(`"deny"`), &known); err != nil { t.Fatal(err) }
	custom = CapabilitiesEffectsItem{Custom: Some("vendor.future")}
	if !known.Known.Present || string(known.Known.Value) != "deny" || known.Custom.Present {
		t.Fatalf("known alternative: %+v", known)
	}
	if !custom.Custom.Present || custom.Custom.Value != "vendor.future" || custom.Known.Present {
		t.Fatalf("custom alternative: %+v", custom)
	}
	for _, value := range []CapabilitiesEffectsItem{known, custom} {
		data, err := json.Marshal(value)
		if err != nil { t.Fatal(err) }
		var roundTrip CapabilitiesEffectsItem
		if err := json.Unmarshal(data, &roundTrip); err != nil { t.Fatal(err) }
		// The enum remains extensible: parsing an unknown string selects the first
		// matching (enum) branch, just as before the public naming change.
		reencoded, err := json.Marshal(roundTrip)
		if err != nil || string(reencoded) != string(data) { t.Fatalf("round trip: %s, %v", data, err) }
	}
}

func TestGeneratedCodecsPreserveInputSemantics(t *testing.T) {
	var _ ProtocolVersion = ProtocolVersion(ProtocolVersionValue)
	var _ InterceptSubscriptionMode = InterceptSubscriptionModeIntercept

	registration := fixture(t, "fixtures/draft/registration/portable.valid.json")
	registration["futureRoot"] = map[string]any{"nested": true}
	hooks := registration["hooks"].([]any)
	transport := hooks[0].(map[string]any)["transport"].(map[string]any)
	transport["futureNested"] = json.Number("7")
	result := ParseRegistration(inputJSON(t, registration))
	if !result.OK {
		t.Fatalf("registration failed: %+v", result.Diagnostics)
	}
	if len(result.Value.Hooks) == 0 || !result.Value.Hooks[0].Transport.HttpTransport.Present ||
		result.Value.Hooks[0].Transport.HttpTransport.Value.URL == "" {
		t.Fatal("known transport was not exposed as a typed union variant")
	}
	encodedData, encodeErr := EncodeRegistration(result.Value)
	encoded := encodedObject(t, encodedData, encodeErr)
	if encoded["futureRoot"].(map[string]any)["nested"] != true ||
		encoded["hooks"].([]any)[0].(map[string]any)["transport"].(map[string]any)["futureNested"].(json.Number).String() != "7" {
		t.Fatal("recursive unknown properties were not preserved")
	}

	transport = map[string]any{"type": "future", "deeply": map[string]any{"preserved": true}}
	hooks[0].(map[string]any)["transport"] = transport
	result = ParseRegistration(inputJSON(t, registration))
	if !result.OK || !hasDiagnostic(result.Diagnostics, DiagnosticUnknownVariant) {
		t.Fatalf("unknown discriminator variant was not preserved: %+v", result.Diagnostics)
	}
	if len(result.Value.Hooks[0].Transport.Unknown) == 0 {
		t.Fatal("unknown discriminator variant was not exposed as raw JSON")
	}
	encodedData, encodeErr = EncodeRegistration(result.Value)
	encoded = encodedObject(t, encodedData, encodeErr)
	preserved := encoded["hooks"].([]any)[0].(map[string]any)["transport"].(map[string]any)["deeply"].(map[string]any)["preserved"]
	if preserved != true {
		t.Fatal("unknown variant payload was lost")
	}

	noDefault := fixture(t, "fixtures/draft/registration/portable.valid.json")
	subscriptions := noDefault["hooks"].([]any)[1].(map[string]any)["subscriptions"].([]any)
	delete(subscriptions[1].(map[string]any), "includeNative")
	result = ParseRegistration(inputJSON(t, noDefault))
	if !result.OK {
		t.Fatalf("registration without default failed: %+v", result.Diagnostics)
	}
	encodedData, encodeErr = EncodeRegistration(result.Value)
	encoded = encodedObject(t, encodedData, encodeErr)
	encodedSubscriptions := encoded["hooks"].([]any)[1].(map[string]any)["subscriptions"].([]any)
	if _, present := encodedSubscriptions[1].(map[string]any)["includeNative"]; present {
		t.Fatal("decoder fabricated an absent default")
	}

	numeric := fixture(t, "fixtures/draft/registration/portable.valid.json")
	numericSubscriptions := numeric["hooks"].([]any)[0].(map[string]any)["subscriptions"].([]any)
	numericSubscriptions[0].(map[string]any)["timeoutMs"] = json.Number("1e3")
	result = ParseRegistration(inputJSON(t, numeric))
	if !result.OK {
		t.Fatalf("mathematically integral exponent was rejected: %+v", result.Diagnostics)
	}
	typedSubscription := result.Value.Hooks[0].Subscriptions[0].InterceptSubscription
	if !typedSubscription.Present || typedSubscription.Value.TimeoutMs.String() != "1e3" {
		t.Fatal("typed integer did not preserve its JSON number spelling")
	}
	encodedData, encodeErr = EncodeRegistration(result.Value)
	encoded = encodedObject(t, encodedData, encodeErr)
	encodedTimeout := encoded["hooks"].([]any)[0].(map[string]any)["subscriptions"].([]any)[0].(map[string]any)["timeoutMs"].(json.Number)
	if encodedTimeout.String() != "1e3" {
		t.Fatalf("integer spelling changed on round-trip: %s", encodedTimeout)
	}
	numericSubscriptions[0].(map[string]any)["timeoutMs"] = json.Number("1.0000000000000001")
	result = ParseRegistration(inputJSON(t, numeric))
	if result.OK || !hasDiagnostic(result.Diagnostics, DiagnosticInvalidType) {
		t.Fatal("non-integral exact JSON number was accepted as an integer")
	}
	numericSubscriptions[0].(map[string]any)["timeoutMs"] = json.Number("9007199254740992")
	result = ParseRegistration(inputJSON(t, numeric))
	if result.OK || !hasDiagnostic(result.Diagnostics, DiagnosticInvalidType) {
		t.Fatal("integer outside the cross-language safe range was accepted")
	}

	request := fixture(t, "fixtures/draft/http/intercept-request.valid.json")
	messageResult := ParseJsonRpcMessage(inputJSON(t, request))
	if !messageResult.OK {
		t.Fatalf("request envelope did not select one branch: %+v", messageResult.Diagnostics)
	}
	if !messageResult.Value.JsonRpcRequest.Present || messageResult.Value.JsonRpcRequest.Value.Method == "" {
		t.Fatal("JSON-RPC request was not exposed as a typed union variant")
	}
	request["params"].(map[string]any)["event"].(map[string]any)["tool"].(map[string]any)["origin"] = "future_origin"
	requestResult := ParseInterceptRequest(inputJSON(t, request))
	if !requestResult.OK || !hasDiagnostic(requestResult.Diagnostics, DiagnosticUnknownEnum) {
		t.Fatalf("unknown enum value was not preserved: %+v", requestResult.Diagnostics)
	}
	if requestResult.Value.Params.Event.ToolBeforeEvent.Value.Tool.Origin != ExecutionEventToolOrigin("future_origin") {
		t.Fatal("unknown enum value was not exposed through its typed open-enum field")
	}
	requestData, requestEncodeErr := EncodeInterceptRequest(requestResult.Value)
	requestEncoded := encodedObject(t, requestData, requestEncodeErr)
	encodedKind := requestEncoded["params"].(map[string]any)["event"].(map[string]any)["tool"].(map[string]any)["origin"]
	if encodedKind != "future_origin" {
		t.Fatal("unknown enum value was lost on round-trip")
	}

	deny := fixture(t, "fixtures/draft/http/deny-response.valid.json")
	deny["result"].(map[string]any)["effects"].([]any)[0].(map[string]any)["code"] = nil
	denyResult := ParseInterceptDenyResponse(inputJSON(t, deny))
	if denyResult.OK || len(denyResult.Raw) == 0 {
		t.Fatal("invalid explicit null was not retained separately from absence")
	}
	var denyRaw map[string]any
	if err := json.Unmarshal(denyResult.Raw, &denyRaw); err != nil ||
		denyRaw["result"].(map[string]any)["effects"].([]any)[0].(map[string]any)["code"] != nil {
		t.Fatal("failure raw JSON lost explicit null")
	}

	ambiguous := map[string]any{
		"jsonrpc": "2.0", "id": "event-1", "result": map[string]any{},
		"error": map[string]any{"code": json.Number("-32600"), "message": "bad"},
	}
	messageResult = ParseJsonRpcMessage(inputJSON(t, ambiguous))
	if messageResult.OK || !hasDiagnostic(messageResult.Diagnostics, DiagnosticNoUnionMatch) {
		t.Fatalf("invalid result/error envelope selected a branch: %+v", messageResult.Diagnostics)
	}

	malformed := fixture(t, "fixtures/draft/registration/portable.valid.json")
	malformed["hooks"].([]any)[0].(map[string]any)["transport"] = map[string]any{"type": "http"}
	result = ParseRegistration(inputJSON(t, malformed))
	if result.OK || !hasDiagnostic(result.Diagnostics, DiagnosticInvalidKnownVariant) {
		t.Fatalf("malformed known variant fell back: %+v", result.Diagnostics)
	}
}

func TestRequiredCandidateStates(t *testing.T) {
    request := fixture(t, "fixtures/draft/http/intercept-request.valid.json")
    state := map[string]any{"permission": "allow"}
    request["params"].(map[string]any)["state"] = state
    for _, candidate := range []any{nil, map[string]any{"value": nil}, map[string]any{"value": false}, map[string]any{"value": ""}, map[string]any{"value": 0}} {
        state["candidate"] = candidate
        parsed := ParseInterceptRequest(inputJSON(t, request))
        if !parsed.OK { t.Fatalf("candidate %v: %+v", candidate, parsed.Diagnostics) }
        if parsed.Value.Params.State.Value.Candidate.Valid != (candidate != nil) { t.Fatal("candidate null and payload collapsed") }
        encoded, err := EncodeInterceptRequest(parsed.Value)
        if err != nil { t.Fatal(err) }
        if !bytes.Equal(inputJSON(t, encodedObject(t, encoded, nil)), inputJSON(t, request)) { t.Fatal("candidate changed on roundtrip") }
    }
    delete(state, "candidate")
    parsed := ParseInterceptRequest(inputJSON(t, request))
    if parsed.OK || !hasDiagnostic(parsed.Diagnostics, DiagnosticMissingRequired) { t.Fatalf("missing candidate: %+v", parsed.Diagnostics) }
}

func TestCanonicalPositiveFixturesAndSuppliedExecution(t *testing.T) {
    manifest := fixture(t, "fixtures/draft/manifest.json")
    for _, raw := range manifest["cases"].([]any) {
        entry := raw.(map[string]any)
        binding := entry["binding"].(string)
        if !entry["expectedValid"].(bool) || (binding != "http-json" && binding != "registration-json") { continue }
        original := fixture(t, entry["path"].(string))
        var encoded []byte
        var err error
        if binding == "registration-json" {
            parsed := ParseRegistration(inputJSON(t, original))
            if !parsed.OK { t.Fatalf("canonical fixture %s: %+v", entry["id"], parsed.Diagnostics) }
            encoded, err = EncodeRegistration(parsed.Value)
        } else {
            parsed := ParseWireMessage(inputJSON(t, original))
            if !parsed.OK { t.Fatalf("canonical fixture %s: %+v", entry["id"], parsed.Diagnostics) }
            encoded, err = EncodeWireMessage(parsed.Value)
        }
        roundTrip := encodedObject(t, encoded, err)
        if !bytes.Equal(inputJSON(t, roundTrip), inputJSON(t, original)) { t.Fatalf("fixture %s changed on round-trip", entry["id"]) }
    }
    wire := fixture(t, "fixtures/draft/http/model-response-intercept-supplied-stop.valid.json")
    supplied := wire["params"].(map[string]any)["event"].(map[string]any)
    execution := supplied["execution"].(map[string]any)
    if len(execution) != 2 || execution["status"] != "skipped" || execution["reason"] != "supplied_result" { t.Fatal("supplied execution must not require a subscription ID") }
    parsed := ParseExecutionEvent(inputJSON(t, supplied))
    if !parsed.OK { t.Fatalf("supplied execution: %+v", parsed.Diagnostics) }
    encoded, err := EncodeExecutionEvent(parsed.Value)
    if !bytes.Equal(inputJSON(t, encodedObject(t, encoded, err)), inputJSON(t, supplied)) { t.Fatal("supplied execution changed on round-trip") }
    delete(execution, "reason")
    if ParseExecutionEvent(inputJSON(t, supplied)).OK { t.Fatal("missing skipped reason selected another branch") }
    execution["reason"] = nil
    if ParseExecutionEvent(inputJSON(t, supplied)).OK { t.Fatal("null skipped reason passed parsing") }
    for _, transport := range []string{"http", "stdio"} {
        invalid := fixture(t, "fixtures/draft/http/mcp-"+transport+"-location-missing.invalid.json")
        event := invalid["params"].(map[string]any)["event"].(map[string]any)
        if ParseToolBeforeEvent(inputJSON(t, event)).OK { t.Fatalf("required-only %s location predicate was lost", transport) }
    }
}

EOF


# Exercise the same surface consumers use, without private decoding helpers.
cat >"$tmp/exported_decode_test.go" <<'EOF'
package ahp_test

import (
    "bytes"
    "encoding/json"
    "os"
    "path/filepath"
    "reflect"
    "testing"

    ahp "github.com/agenthooksprotocol/go-sdk"
)

func TestTypedCompositionConsumers(t *testing.T) {
    http := ahp.ExecutionEventMcpConnection{HTTP: ahp.Some(ahp.ExecutionEventMcpConnectionHTTP{
        Transport: "http", URL: ahp.Some("https://example.test/mcp"),
        Gaps: ahp.Some([]ahp.ExecutionEventMcpConnectionHTTPGapsItem{{Path: "url", Reason: "redacted"}}),
    })}
    sse := ahp.ExecutionEventMcpConnection{Sse: ahp.Some(ahp.ExecutionEventMcpConnectionSse{
        Transport: "sse", URL: ahp.Some("https://example.test/sse"),
        Gaps: ahp.Some([]ahp.ExecutionEventMcpConnectionSseGapsItem{{Path: "url", Reason: "redacted"}}),
    })}
    stdio := ahp.ExecutionEventMcpConnection{Stdio: ahp.Some(ahp.ExecutionEventMcpConnectionStdio{
        Transport: "stdio", Command: ahp.Some("server"), Args: ahp.Some([]string{"--local"}), CWD: ahp.Some("/tmp"),
        Gaps: ahp.Some([]ahp.ExecutionEventMcpConnectionStdioGapsItem{{Path: "cwd", Reason: "redacted"}}),
    })}
    custom := ahp.ExecutionEventMcpConnection{CustomTransport: ahp.Some(ahp.ExecutionEventMcpConnectionCustomTransport{
        Transport: "socket", Address: ahp.Some("/tmp/mcp.sock"), AddressForm: ahp.Some("unix"),
        Gaps: ahp.Some([]ahp.ExecutionEventMcpConnectionCustomTransportGapsItem{{Path: "address", Reason: "redacted"}}),
    })}
    if http.HTTP.Value.URL.Value != "https://example.test/mcp" || http.HTTP.Value.Gaps.Value[0].Path != "url" ||
        sse.Sse.Value.Gaps.Value[0].Reason != "redacted" || stdio.Stdio.Value.Args.Value[0] != "--local" ||
        stdio.Stdio.Value.Gaps.Value[0].Path != "cwd" || custom.CustomTransport.Value.AddressForm.Value != "unix" ||
        custom.CustomTransport.Value.Gaps.Value[0].Path != "address" { t.Fatal("typed connection fields unavailable") }
    for _, input := range []string{
        `{"transport":"http","url":"https://example.test","future":true}`,
        `{"transport":"sse","gaps":[{"path":"url","reason":"redacted"}]}`,
        `{"transport":"stdio","command":"server","args":[],"cwd":"/tmp"}`,
        `{"transport":"socket","address":"/tmp/mcp.sock","addressForm":"unix"}`,
    } {
        var decoded ahp.ExecutionEventMcpConnection
        if err := json.Unmarshal([]byte(input), &decoded); err != nil { t.Fatalf("typed decode %s: %v", input, err) }
        if !decoded.HTTP.Present && !decoded.Sse.Present && !decoded.Stdio.Present && !decoded.CustomTransport.Present { t.Fatalf("typed branch unavailable: %+v", decoded) }
    }
    for _, input := range []string{
        `{"transport":"http"}`, `{"transport":"sse"}`, `{"transport":"stdio","command":"server"}`,
        `{"transport":"http","gaps":[{"path":"url"}]}`, `{"transport":"http","url":42}`,
    } {
        var decoded ahp.ExecutionEventMcpConnection
        if err := json.Unmarshal([]byte(input), &decoded); err == nil { t.Fatalf("location/structure predicate lost: %s", input) }
    }
    item := ahp.ModelVisibleItem{Metadata: ahp.Some(ahp.ModelVisibleItemMetadata{
        ID: "item", Kind: "text", MediaType: "text/plain", Selection: "metadata", Role: "user",
    })}
    if item.Metadata.Value.Role != "user" || item.Metadata.Value.Kind != "text" { t.Fatal("composed item fields unavailable") }
    for _, input := range []string{
        `{"id":"item","kind":"text","mediaType":"text/plain","selection":"metadata"}`,
        `{"id":"item","kind":"text","mediaType":"text/plain","selection":"body","role":"user"}`,
    } {
        var decoded ahp.ModelVisibleItem
        if err := json.Unmarshal([]byte(input), &decoded); err == nil { t.Fatalf("composed content constraints lost: %s", input) }
    }
    var decoded ahp.ModelVisibleItem
    if err := json.Unmarshal([]byte(`{"id":"item","kind":"text","mediaType":"text/plain","selection":"metadata","role":"user","future":true}`), &decoded); err != nil || decoded.Metadata.Value.Role != "user" || !bytes.Equal(decoded.Metadata.Value.AdditionalProperties["future"], []byte("true")) { t.Fatalf("composed decode: %+v %v", decoded, err) }
}

func exportedFixture(t *testing.T, path string) []byte {
    t.Helper()
    data, err := os.ReadFile(filepath.Join(os.Getenv("AHP_REPOSITORY"), path))
    if err != nil { t.Fatal(err) }
    return data
}

func exportedJSON(t *testing.T, value any) []byte {
    t.Helper()
    data, err := json.Marshal(value)
    if err != nil { t.Fatal(err) }
    return data
}

// Both encoding/json entry points must honor the generated structural contract.
func assertExportedDecode[T any](t *testing.T, data []byte, want bool) T {
    t.Helper()
    var unmarshaled, decoded T
    unmarshalErr := json.Unmarshal(data, &unmarshaled)
    decodeErr := json.NewDecoder(bytes.NewReader(data)).Decode(&decoded)
    if (unmarshalErr == nil) != want || (decodeErr == nil) != want {
        t.Fatalf("acceptance want %t: Unmarshal=%v Decode=%v; input=%s", want, unmarshalErr, decodeErr, data)
    }
    if want && !reflect.DeepEqual(unmarshaled, decoded) {
        t.Fatal("Unmarshal and Decoder.Decode produced different values")
    }
    return unmarshaled
}

func checkAcceptanceCase[T any](t *testing.T, input []byte, want, warning bool, parse func([]byte) ahp.ParseResult[T]) {
    t.Helper()
    parsed := parse(input)
    if parsed.OK != want { t.Fatalf("Parse acceptance: want %t, got %+v", want, parsed.Diagnostics) }
    decoded := assertExportedDecode[T](t, input, want)
    warned := false
    for _, diagnostic := range parsed.Diagnostics { if diagnostic.Severity == ahp.SeverityWarning { warned = true } }
    if warned != warning { t.Fatalf("warning: want %t, got %+v", warning, parsed.Diagnostics) }
    if want {
        if !reflect.DeepEqual(decoded, parsed.Value) { t.Fatal("Decode/Parse differ") }
        var before, after any
        if err := json.Unmarshal(input, &before); err != nil { t.Fatal(err) }
        if err := json.Unmarshal(exportedJSON(t, decoded), &after); err != nil { t.Fatal(err) }
        if !reflect.DeepEqual(before, after) { t.Fatal("accepted value changed in roundtrip") }
    }
}

func TestSharedStructuralAcceptanceMatrix(t *testing.T) {
    var matrix struct { Cases []struct {
        ID string `json:"id"`
        Root string `json:"root"`
        Value json.RawMessage `json:"value"`
        Accepted bool `json:"accepted"`
        Warning bool `json:"warning"`
    } `json:"cases"` }
    if err := json.Unmarshal(exportedFixture(t, "tools/sdk-codegen/tests/structural-acceptance.json"), &matrix); err != nil { t.Fatal(err) }
    for _, entry := range matrix.Cases {
        t.Run(entry.ID, func(t *testing.T) {
            switch entry.Root {
            case "intercept_request": checkAcceptanceCase(t, entry.Value, entry.Accepted, entry.Warning, ahp.ParseInterceptRequest)
            case "content_reference": checkAcceptanceCase(t, entry.Value, entry.Accepted, entry.Warning, ahp.ParseContentReference)
            case "content_upload_receipt": checkAcceptanceCase(t, entry.Value, entry.Accepted, entry.Warning, ahp.ParseContentUploadReceipt)
            default: t.Fatalf("unhandled matrix root %s", entry.Root)
            }
        })
    }
}

func TestExportedCandidateValueRequired(t *testing.T) {
    for _, input := range []string{`{}`, `{"provenance":{}}`, `null`, `[]`} {
        t.Run(input, func(t *testing.T) {
            assertExportedDecode[ahp.InterceptRequestParamsStateCandidateValue](t, []byte(input), false)
        })
    }
    for _, payload := range []string{`null`, `false`, `0`, `""`, `{}`, `[]`} {
        t.Run("value="+payload, func(t *testing.T) {
            data := []byte(`{"value":`+payload+`}`)
            candidate := assertExportedDecode[ahp.InterceptRequestParamsStateCandidateValue](t, data, true)
            if string(candidate.Value) != payload { t.Fatalf("value changed: %s", candidate.Value) }
            if encoded := exportedJSON(t, candidate); !bytes.Equal(encoded, data) {
                t.Fatalf("candidate round-trip: %s", encoded)
            }
        })
    }
}

func TestExportedRequestStructuralValidation(t *testing.T) {
    tests := []struct {
        name string
        change func(map[string]any, map[string]any, map[string]any)
        want bool
    }{
        {"valid", func(r, p, e map[string]any) {}, true},
        {"missing-envelope-id", func(r, p, e map[string]any) { delete(r, "id") }, false},
        {"missing-params", func(r, p, e map[string]any) { delete(r, "params") }, false},
        {"missing-event-id", func(r, p, e map[string]any) { delete(e, "id") }, false},
        {"invalid-jsonrpc-literal", func(r, p, e map[string]any) { r["jsonrpc"] = "1.0" }, false},
        {"invalid-method-literal", func(r, p, e map[string]any) { r["method"] = "hooks/observe" }, false},
        {"known-tool-before-missing-tool", func(r, p, e map[string]any) { delete(e, "tool") }, false},
        {"malformed-nested-tool", func(r, p, e map[string]any) { e["tool"] = []any{} }, false},
        {"malformed-nested-name", func(r, p, e map[string]any) { e["tool"].(map[string]any)["name"] = 42 }, false},
        {"missing-state-candidate", func(r, p, e map[string]any) { p["state"] = map[string]any{"permission":"allow"} }, false},
        {"missing-state-permission", func(r, p, e map[string]any) { p["state"] = map[string]any{"candidate":nil} }, false},
        {"missing-candidate-value", func(r, p, e map[string]any) { p["state"] = map[string]any{"permission":"allow", "candidate":map[string]any{}} }, false},
        {"nullable-candidate-null", func(r, p, e map[string]any) { p["state"] = map[string]any{"permission":"allow", "candidate":nil} }, true},
        {"candidate-value-null", func(r, p, e map[string]any) { p["state"] = map[string]any{"permission":"allow", "candidate":map[string]any{"value":nil}} }, true},
        {"unknown-enum-and-extensions", func(r, p, e map[string]any) {
            e["tool"].(map[string]any)["origin"] = "future_origin"
            e["tool"].(map[string]any)["future_tool_field"] = map[string]any{"nested":[]any{nil, false, "retained"}}
            r["future_envelope_field"] = nil
        }, true},
    }
    for _, tt := range tests {
        t.Run(tt.name, func(t *testing.T) {
            var request map[string]any
            if err := json.Unmarshal(exportedFixture(t, "fixtures/draft/http/intercept-request.valid.json"), &request); err != nil { t.Fatal(err) }
            params := request["params"].(map[string]any)
            event := params["event"].(map[string]any)
            tt.change(request, params, event)
            data := exportedJSON(t, request)
            parsed := ahp.ParseInterceptRequest(data)
            if parsed.OK != tt.want { t.Fatalf("Parse acceptance want %t: %+v", tt.want, parsed.Diagnostics) }
            decoded := assertExportedDecode[ahp.InterceptRequest](t, data, tt.want)
            if !tt.want { return }
            if !reflect.DeepEqual(decoded, parsed.Value) { t.Fatal("Decode and Parse produced different values") }
            var roundTrip map[string]any
            if err := json.Unmarshal(exportedJSON(t, decoded), &roundTrip); err != nil { t.Fatal(err) }
            if !reflect.DeepEqual(request, roundTrip) { t.Fatal("Decode round-trip lost explicit null, enum, or extension data") }
            if tt.name == "nullable-candidate-null" && (!decoded.Params.State.Present || decoded.Params.State.Value.Candidate.Valid) {
                t.Fatal("explicit candidate null lost its nullable state")
            }
            if tt.name == "candidate-value-null" && (!decoded.Params.State.Present || !decoded.Params.State.Value.Candidate.Valid || string(decoded.Params.State.Value.Candidate.Value.Value) != "null") {
                t.Fatal("candidate {value:null} collapsed into a null candidate")
            }
            if tt.name == "unknown-enum-and-extensions" && decoded.Params.Event.ToolBeforeEvent.Value.Tool.Origin != ahp.ExecutionEventToolOrigin("future_origin") {
                t.Fatal("unknown enum did not survive typed decoding")
            }
        })
    }
}

func TestExportedDecodeParseCanonicalFixtureAgreement(t *testing.T) {
    var manifest struct { Cases []struct {
        ID string `json:"id"`
        Path string `json:"path"`
        Binding string `json:"binding"`
        ExpectedValid bool `json:"expectedValid"`
    } `json:"cases"` }
    if err := json.Unmarshal(exportedFixture(t, "fixtures/draft/manifest.json"), &manifest); err != nil { t.Fatal(err) }
    positive, negative := 0, 0
    for _, entry := range manifest.Cases {
        if entry.Binding != "http-json" && entry.Binding != "registration-json" { continue }
        if entry.ExpectedValid { positive++ } else { negative++ }
        t.Run(entry.ID, func(t *testing.T) {
            data := exportedFixture(t, entry.Path)
            // Canonical negatives include contextual/schema constraints outside the
            // SDK structural contract. Require agreement, NOT full schema parity.
            if entry.Binding == "registration-json" {
                parsed := ahp.ParseRegistration(data)
                if entry.ExpectedValid && !parsed.OK { t.Fatalf("positive fixture: %+v", parsed.Diagnostics) }
                decoded := assertExportedDecode[ahp.Registration](t, data, parsed.OK)
                if parsed.OK && !reflect.DeepEqual(decoded, parsed.Value) { t.Fatal("Decode/Parse values differ") }
            } else {
                parsed := ahp.ParseWireMessage(data)
                if entry.ExpectedValid && !parsed.OK { t.Fatalf("positive fixture: %+v", parsed.Diagnostics) }
                decoded := assertExportedDecode[ahp.WireMessage](t, data, parsed.OK)
                if parsed.OK && !reflect.DeepEqual(decoded, parsed.Value) { t.Fatal("Decode/Parse values differ") }
            }
        })
    }
    if positive == 0 || negative == 0 { t.Fatalf("expected positive and negative fixtures, got %d/%d", positive, negative) }
}
EOF
"$goroot/bin/gofmt" -w "$tmp/exported_decode_test.go"

cat >"$tmp/supports_test.go" <<'EOF'
package ahp_test

import (
    "encoding/json"
    "testing"
    ahp "github.com/agenthooksprotocol/go-sdk"
    "github.com/agenthooksprotocol/go-sdk/capability"
)

func TestCapabilitySupports(t *testing.T) {
    if (ahp.Capabilities{}).Supports(ahp.EffectDeny) { t.Fatal("zero grants") }
    for _, effects := range [][]ahp.CapabilitiesEffectsItem{
        {{Known: ahp.Some(ahp.CapabilitiesEffectsItemKnownDeny)}},
        {{Custom: ahp.Some("deny")}},
    } {
        caps := ahp.Capabilities{Effects: effects}
        if !caps.Supports(ahp.EffectDeny) || caps.Supports(ahp.EffectNameAllow) { t.Fatal("family membership") }
    }
    extension := capability.New([]string{"vendor.custom"})
    if !extension.Supports(ahp.EffectName("vendor.custom")) || extension.Supports(ahp.EffectDeny) { t.Fatal("custom family") }
    // Queries follow the encoder's known-arm precedence, even for malformed manual unions.
    invalid := ahp.Capabilities{Effects: []ahp.CapabilitiesEffectsItem{{Known: ahp.Some(ahp.CapabilitiesEffectsItemKnownAllow), Custom: ahp.Some("deny")}}}
    if invalid.Supports(ahp.EffectDeny) { t.Fatal("inactive arm advertised") }
    req := ahp.InterceptRequest{}
    for _, wire := range []string{`{"effects":["deny"]}`, `{"effects":["vendor.custom","deny"]}`} {
        if err := json.Unmarshal([]byte(wire), &req.Params.Capabilities); err != nil { t.Fatal(err) }
        if !req.Params.Capabilities.Supports(ahp.EffectDeny) { t.Fatal("request capability query") }
    }
    req.Params.Capabilities.Effects = []ahp.InterceptRequestParamsCapabilitiesEffectsItem{{Custom: ahp.Some("deny")}}
    if !req.Params.Capabilities.Supports(ahp.EffectDeny) { t.Fatal("request custom representation") }
    event, err := capability.Intercept(capability.Deny(), capability.Deny(), capability.ModifyInput(capability.Replace))
    if err != nil { t.Fatal(err) }
    if !event.Capabilities.Supports(ahp.EffectDeny) || !event.Capabilities.Supports(ahp.EffectNameModify) || len(event.Capabilities.Effects) != 2 { t.Fatal("shared grant membership") }
    // Family support is deliberately not an operation or target authorization query.
    if event.Capabilities.Modify.Value.Input.Value.Merge || event.Capabilities.Modify.Value.Output.Present { t.Fatal("ungranted operations/targets") }
    if _, err := capability.Intercept(capability.ModifyInput(capability.ModifyOperation("unknown"))); err == nil { t.Fatal("unknown operation accepted") }
    if _, err := capability.Intercept(capability.ModifyInput()); err == nil { t.Fatal("empty operation grant accepted") }
    nestedOnly := capability.New([]string{}, capability.WithModify(event.Capabilities.Modify.Value))
    if nestedOnly.Supports(ahp.EffectNameModify) { t.Fatal("nested grant implied family support") }
    familyOnly := capability.New([]string{"modify"})
    if !familyOnly.Supports(ahp.EffectNameModify) || familyOnly.Modify.Present { t.Fatal("family query implied nested grant") }
}
EOF

gofmt -d "$tmp/ahp_generated.go" >"$tmp/gofmt.diff"
if [[ -s "$tmp/gofmt.diff" ]]; then
  echo "generated Go is not gofmt-clean" >&2
  head -n 120 "$tmp/gofmt.diff" >&2
  exit 1
fi

(
  cd "$tmp"
  AHP_REPOSITORY="$repository" go test ./...
)
echo "generated Go codec smoke tests passed"

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
module example.com/ahp-smoke

go 1.22
EOF
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

func typedEffects(capabilities Capabilities) []CapabilitiesEffectsItem {
	return capabilities.Effects
}

func TestGeneratedCodecsPreserveInputSemantics(t *testing.T) {
	var _ ProtocolVersion = ProtocolVersionValue01
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
	request["params"].(map[string]any)["event"].(map[string]any)["tool"].(map[string]any)["kind"] = "future_tool"
	requestResult := ParseInterceptRequest(inputJSON(t, request))
	if !requestResult.OK || !hasDiagnostic(requestResult.Diagnostics, DiagnosticUnknownEnum) {
		t.Fatalf("unknown enum value was not preserved: %+v", requestResult.Diagnostics)
	}
	if requestResult.Value.Params.Event.Tool.Kind != ToolBeforeEventToolKind("future_tool") {
		t.Fatal("unknown enum value was not exposed through its typed open-enum field")
	}
	requestData, requestEncodeErr := EncodeInterceptRequest(requestResult.Value)
	requestEncoded := encodedObject(t, requestData, requestEncodeErr)
	encodedKind := requestEncoded["params"].(map[string]any)["event"].(map[string]any)["tool"].(map[string]any)["kind"]
	if encodedKind != "future_tool" {
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

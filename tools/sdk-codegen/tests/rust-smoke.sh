#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: rust-smoke.sh <generated.rs> <repository>" >&2
  exit 2
fi

generated=$(cd "$(dirname "$1")" && pwd)/$(basename "$1")
repository=$(cd "$2" && pwd)
temporary="$(mktemp -d "${TMPDIR:-/tmp}/ahp-rust-smoke.XXXXXX")"
trap 'rm -rf "$temporary"' EXIT
rust_toolchain="${AHP_RUST_TOOLCHAIN:-1.88.0}"
cargo_command=(cargo "+$rust_toolchain")

mkdir -p "$temporary/emitter/src" "$temporary/consumer/src" "$temporary/consumer/tests"
cp "$generated" "$temporary/consumer/src/lib.rs"
cat >"$temporary/emitter/Cargo.toml" <<'TOML'
[package]
name = "ahp-rust-emitter-smoke"
version = "0.0.0"
edition = "2024"
rust-version = "1.88"
publish = false

[dependencies]
anyhow = "=1.0.104"
serde = { version = "=1.0.229", features = ["derive"] }
serde_json = "=1.0.151"
TOML

cat >"$temporary/emitter/src/main.rs" <<RS
#[path = "$repository/tools/sdk-codegen/src/model.rs"]
mod model;
#[path = "$repository/tools/sdk-codegen/src/langs/rust.rs"]
mod rust;

fn main() -> anyhow::Result<()> {
    let mut arguments = std::env::args_os().skip(1);
    let recursive_output =
        std::path::PathBuf::from(arguments.next().expect("recursive output argument"));
    let collision_output =
        std::path::PathBuf::from(arguments.next().expect("collision output argument"));

    let recursive = model::Ir {
        schema_revision: "recursive-test".into(),
        protocol_version: "1".into(),
        roots: vec![
            model::PublicRoot { name: "Node".into(), schema: "node.json".into() },
            model::PublicRoot { name: "ExactNumber".into(), schema: "number.json".into() },
            model::PublicRoot { name: "SafeInteger".into(), schema: "integer.json".into() },
            model::PublicRoot { name: "NumericLiteral".into(), schema: "literal.json".into() },
            model::PublicRoot { name: "NumericEnum".into(), schema: "enum.json".into() },
        ],
        types: vec![model::NamedType {
            name: "Node".into(),
            source: "node.json#".into(),
            shape: model::Shape::Object {
                properties: vec![model::Property {
                    wire_name: "next".into(),
                    required: false,
                    shape: model::Shape::Ref { name: "Node".into() },
                }],
                forbidden_property_sets: vec![],
                additional: model::AdditionalProperties::Forbidden,
            },
        },
        model::NamedType {
            name: "ExactNumber".into(),
            source: "number.json#".into(),
            shape: model::Shape::Number,
        },
        model::NamedType {
            name: "SafeInteger".into(),
            source: "integer.json#".into(),
            shape: model::Shape::Integer,
        },
        model::NamedType {
            name: "NumericLiteral".into(),
            source: "literal.json#".into(),
            shape: model::Shape::Literal { value: serde_json::json!(1) },
        },
        model::NamedType {
            name: "NumericEnum".into(),
            source: "enum.json#".into(),
            shape: model::Shape::Enum { values: vec![serde_json::json!(1)], open_strings: false },
        }],
    };
    std::fs::write(recursive_output, rust::emit(&recursive)?)?;

    let collision = model::Ir {
        schema_revision: "collision-test".into(),
        protocol_version: "1".into(),
        roots: vec![],
        types: [
            "BTreeMap", "Box", "Default", "Deserialize", "DeserializeOwned",
            "Deserializer", "Deref", "JsonValue", "OnceLock", "Option",
            "Result", "Serialize", "Serializer", "String", "Vec",
        ].into_iter().map(|name| model::NamedType {
            name: name.into(),
            source: format!("{name}.json#"),
            shape: model::Shape::String,
        }).collect(),
    };
    std::fs::write(collision_output, rust::emit(&collision)?)?;
    Ok(())
}
RS

"${cargo_command[@]}" test --quiet --manifest-path "$temporary/emitter/Cargo.toml"
"${cargo_command[@]}" run --quiet --manifest-path "$temporary/emitter/Cargo.toml" -- \
    "$temporary/consumer/src/recursive.rs" "$temporary/consumer/src/collision.rs"
printf '\npub mod recursive;\npub mod collision;\n' >>"$temporary/consumer/src/lib.rs"

cat >"$temporary/consumer/Cargo.toml" <<'TOML'
[package]
name = "ahp-generated-rust-smoke"
version = "0.0.0"
edition = "2024"
rust-version = "1.88"
publish = false

[dependencies]
serde = { version = "=1.0.229", features = ["derive"] }
serde_json = { version = "=1.0.151", features = ["arbitrary_precision"] }
TOML

cat >"$temporary/consumer/tests/smoke.rs" <<'RS'
use std::fs;
use std::path::PathBuf;

use ahp_generated_rust_smoke::{
    BackendTransport, Capabilities, CapabilitiesEffectsItem, DiagnosticCode,
    InterceptSubscriptionFailurePolicy, JsonRpcId, JsonRpcMessage, JsonRpcResponseId,
    ParseDiagnostic, ParseResult, ToolBeforeEventToolKind, encode_registration,
    parse_intercept_deny_response, parse_intercept_request, parse_json_rpc_message,
    parse_registration_value, recursive,
};
use serde_json::{Value, json};

#[test]
fn recursively_referenced_models_compile_and_parse() {
    assert!(recursive::parse_node("{}").is_ok());

    let exact_text = "1.23456789012345678901234567890123456789";
    let exact = recursive::parse_exact_number(exact_text);
    assert!(exact.is_ok());
    assert_eq!(
        recursive::encode_exact_number(exact.value().unwrap()).unwrap(),
        exact_text
    );
    assert!(!recursive::parse_safe_integer("1.0000000000000001").is_ok());

    let literal = recursive::parse_numeric_literal("1.0");
    assert!(literal.is_ok());
    assert_eq!(
        recursive::encode_numeric_literal(literal.value().unwrap()).unwrap(),
        "1"
    );
    let enumeration = recursive::parse_numeric_enum("1.0");
    assert!(enumeration.is_ok());
    assert_eq!(
        recursive::encode_numeric_enum(enumeration.value().unwrap()).unwrap(),
        "1"
    );
}

fn typed_effects(capabilities: &Capabilities) -> &[CapabilitiesEffectsItem] {
    &capabilities.effects
}

fn fixture(relative: &str) -> Value {
    let repository = PathBuf::from(std::env::var_os("AHP_REPOSITORY").expect("AHP_REPOSITORY"));
    serde_json::from_slice(&fs::read(repository.join(relative)).unwrap()).unwrap()
}

fn success<T>(result: ParseResult<T>) -> (T, Value, Vec<ParseDiagnostic>) {
    match result {
        ParseResult::Success { value, raw, diagnostics } => (value, raw, diagnostics),
        ParseResult::Failure { diagnostics, .. } => panic!("parse failed: {diagnostics:#?}"),
    }
}

#[test]
fn preserves_unknown_data_and_absence_on_round_trip() {
    let mut registration = fixture("fixtures/0.1.0-draft.1/registration/portable.valid.json");
    registration["futureRoot"] = serde_json::from_str(
        "1.23456789012345678901234567890123456789",
    ).unwrap();
    registration["hooks"][0]["transport"]["futureNested"] = json!(7);

    let (model, raw, diagnostics) = success(parse_registration_value(registration.clone()));
    assert!(diagnostics.is_empty());
    assert!(matches!(model.hooks[0].transport, BackendTransport::HttpTransport(_)));
    assert_eq!(raw, registration);
    let encoded: Value = serde_json::from_str(&encode_registration(&model).unwrap()).unwrap();
    assert_eq!(encoded, registration);

    let mut without_default = fixture("fixtures/0.1.0-draft.1/registration/portable.valid.json");
    without_default["hooks"][1]["subscriptions"][1]
        .as_object_mut().unwrap().remove("includeNative");
    let (model, _, _) = success(parse_registration_value(without_default.clone()));
    let encoded: Value = serde_json::from_str(&encode_registration(&model).unwrap()).unwrap();
    assert_eq!(encoded, without_default, "decoder fabricated an absent optional member");
}

#[test]
fn closed_unions_reject_malformed_values_and_null_selects_null() {
    assert!(serde_json::from_value::<JsonRpcId>(json!(true)).is_err());
    assert!(serde_json::from_value::<BackendTransport>(json!({"type": "http"})).is_err());

    let mut response = fixture("fixtures/0.1.0-draft.1/http/deny-response.valid.json");
    response["id"] = Value::Null;
    let (response, _, _) = success(parse_intercept_deny_response(
        &serde_json::to_string(&response).unwrap(),
    ));
    assert!(matches!(response.id.as_ref(), JsonRpcResponseId::Null(())));

    let _ = typed_effects as fn(&Capabilities) -> &[CapabilitiesEffectsItem];
}

#[test]
fn preserves_forward_compatible_values_with_warnings() {
    let mut registration = fixture("fixtures/0.1.0-draft.1/registration/portable.valid.json");
    registration["hooks"][0]["transport"] =
        json!({"type": "future", "deeply": {"preserved": true}});
    let (model, _, diagnostics) = success(parse_registration_value(registration.clone()));
    assert!(diagnostics.iter().any(|item| item.code == DiagnosticCode::UnknownVariant));
    assert!(matches!(model.hooks[0].transport, BackendTransport::Unknown(_)));
    let encoded: Value = serde_json::from_str(&encode_registration(&model).unwrap()).unwrap();
    assert_eq!(encoded, registration);

    let mut request = fixture("fixtures/0.1.0-draft.1/http/intercept-request.valid.json");
    request["params"]["event"]["tool"]["kind"] = json!("future_tool");
    let result = parse_intercept_request(&serde_json::to_string(&request).unwrap());
    assert!(result.is_ok());
    assert!(result.diagnostics().iter().any(|item| item.code == DiagnosticCode::UnknownEnum));
    let kind: ToolBeforeEventToolKind = serde_json::from_value(json!("future_tool")).unwrap();
    assert!(matches!(kind, ToolBeforeEventToolKind::Unknown(value) if value == "future_tool"));
    assert_eq!(
        serde_json::to_value(InterceptSubscriptionFailurePolicy::FailOpen).unwrap(),
        json!("fail-open")
    );

    let message = fixture("fixtures/0.1.0-draft.1/http/intercept-request.valid.json");
    let (message, _, _) = success(parse_json_rpc_message(&serde_json::to_string(&message).unwrap()));
    assert!(matches!(message, JsonRpcMessage::JsonRpcRequest(_)));
}

#[test]
fn preserves_integral_number_forms_and_enforces_safe_integer_bounds() {
    let mut integral_float = fixture("fixtures/0.1.0-draft.1/registration/portable.valid.json");
    integral_float["hooks"][0]["subscriptions"][0]["timeoutMs"] =
        serde_json::from_str("1.0").unwrap();
    let (model, raw, diagnostics) = success(parse_registration_value(integral_float.clone()));
    assert!(diagnostics.is_empty());
    assert_eq!(raw, integral_float);
    let encoded: Value = serde_json::from_str(&encode_registration(&model).unwrap()).unwrap();
    assert_eq!(encoded, integral_float);

    for value in [9_007_199_254_740_992_i64, -9_007_199_254_740_992_i64] {
        let mut out_of_range = fixture("fixtures/0.1.0-draft.1/registration/portable.valid.json");
        out_of_range["hooks"][0]["subscriptions"][0]["timeoutMs"] = json!(value);
        let result = parse_registration_value(out_of_range);
        assert!(!result.is_ok());
        assert!(result.diagnostics().iter().any(|item| item.code == DiagnosticCode::InvalidType));
    }
}

#[test]
fn reports_structural_failures_and_keeps_valid_json_raw() {
    let mut deny = fixture("fixtures/0.1.0-draft.1/http/deny-response.valid.json");
    deny["result"]["effects"][0]["code"] = Value::Null;
    let result = parse_intercept_deny_response(&serde_json::to_string(&deny).unwrap());
    assert!(!result.is_ok());
    assert_eq!(result.raw(), Some(&deny));

    let ambiguous = json!({
        "jsonrpc": "2.0", "id": "event-1", "result": {},
        "error": {"code": -32600, "message": "bad"}
    });
    let result = parse_json_rpc_message(&serde_json::to_string(&ambiguous).unwrap());
    assert!(!result.is_ok());
    assert!(result.diagnostics().iter().any(|item| item.code == DiagnosticCode::NoUnionMatch));

    let mut malformed = fixture("fixtures/0.1.0-draft.1/registration/portable.valid.json");
    malformed["hooks"][0]["transport"] = json!({"type": "http"});
    let result = parse_registration_value(malformed);
    assert!(!result.is_ok());
    assert!(result.diagnostics().iter().any(|item| item.code == DiagnosticCode::InvalidKnownVariant));
}
RS

AHP_REPOSITORY="$repository" "${cargo_command[@]}" test --quiet --manifest-path "$temporary/consumer/Cargo.toml"
echo "generated Rust codec smoke tests passed"

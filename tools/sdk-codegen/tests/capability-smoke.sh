#!/usr/bin/env bash
# Compile real draft IR and execute both generated surfaces outside the checkout.
set -euo pipefail
repository=$(cd "$(dirname "$0")/../../.." && pwd)
temporary=$(mktemp -d "${TMPDIR:-/tmp}/ahp-capability-smoke.XXXXXX")
trap 'rm -rf "$temporary"' EXIT
mkdir -p "$temporary/emitter/src" "$temporary/consumer/src"
cp "$repository/tools/sdk-codegen/Cargo.toml" "$temporary/emitter/Cargo.toml"
cp "$repository/tools/sdk-codegen/Cargo.lock" "$temporary/emitter/Cargo.lock"
cat > "$temporary/emitter/src/main.rs" <<RS
#[path = "$repository/tools/sdk-codegen/src/model.rs"] mod model;
#[path = "$repository/tools/sdk-codegen/src/compiler.rs"] mod compiler;
#[path = "$repository/tools/sdk-codegen/src/ergonomics.rs"] mod ergonomics;
#[path = "$repository/tools/sdk-codegen/src/capability_ergonomics.rs"] mod capability_ergonomics;
#[path = "$repository/tools/sdk-codegen/src/langs/mod.rs"] mod langs;
fn main() -> anyhow::Result<()> {
    let repo = std::path::Path::new("$repository");
    let ir = compiler::compile(repo, "draft")?;
    let ts = capability_ergonomics::typescript(&ir)?;
    let rs = capability_ergonomics::rust(&ir)?;
    let schema: serde_json::Value = serde_json::from_str(&std::fs::read_to_string(repo.join("schema/draft/capabilities.schema.json"))?)?;
    for target in schema["properties"]["modify"]["properties"].as_object().unwrap().keys() {
        let mut chars = target.chars();
        let title = chars.next().unwrap().to_uppercase().to_string() + chars.as_str();
        assert!(ts.contains(&format!("modify{title}(")), "missing TS target {target}");
        assert!(rs.contains(&format!("modify_{target}(")), "missing Rust target {target}");
    }
    // Vocabulary traversal resolves IR refs, not a copied effect list.
    let mut changed = compiler::compile(repo, "draft")?;
    let root = changed.types.iter_mut().find(|n| n.name == "Capabilities").unwrap();
    fn add_effect(shape: &mut model::Shape) {
        if let model::Shape::Object { properties, .. } = shape {
            properties.iter_mut().find(|p| p.wire_name == "effects").unwrap().shape = model::Shape::Array { items: Box::new(model::Shape::Enum {values: vec![serde_json::json!("testEffect")], open_strings: false}) };
        } else { panic!("unexpected capability root"); }
    }
    add_effect(&mut root.shape);
    assert!(capability_ergonomics::typescript(&changed)?.contains("testEffect():"));
    assert!(capability_ergonomics::rust(&changed)?.contains("test_effect(&self)"));
    let mut ts_wire = langs::typescript::emit(&ir)?;
    if !ts_wire.contains("export class CapabilityBuilder") { ts_wire.push_str(&ts); }
    let mut rs_wire = langs::rust::emit_from_repository(&ir, repo)?;
    if !rs_wire.contains("pub mod capability {") { rs_wire.push_str(&rs); }
    std::fs::write("$temporary/generated.ts", ts_wire)?;
    std::fs::write("$temporary/consumer/src/lib.rs", rs_wire)?;
    Ok(())
}
RS
export CARGO_TARGET_DIR="${AHP_CAPABILITY_TARGET_DIR:-/tmp/ahp-capability-smoke-target}"
export RUSTFLAGS="${RUSTFLAGS:-} -Awarnings"
cargo +"${AHP_RUST_TOOLCHAIN:-1.88.0}" run --quiet --offline --manifest-path "$temporary/emitter/Cargo.toml"
cat > "$temporary/usage.ts" <<'TS'
import {capabilities, CapabilityDeclaration} from './generated';
const declaration: CapabilityDeclaration = capabilities.intercept().deny().modifyInput({replace:true});
// @ts-expect-error observation-only declarations have no interception grants
capabilities.observe().deny();
// @ts-expect-error only schema-derived operation names are accepted
capabilities.intercept().modifyInput({append:true});
// @ts-expect-error only schema-derived delivery values are accepted
capabilities.intercept().injectContext(['later']);
TS
cp "$repository/tools/sdk-codegen/tests/typescript-generic-input-smoke.ts.in" "$temporary/generic-input.ts"
tsc --strict --noUncheckedIndexedAccess --exactOptionalPropertyTypes --target ES2022 --module commonjs --outDir "$temporary/js" "$temporary/generated.ts" "$temporary/usage.ts" "$temporary/generic-input.ts"
node "$repository/tools/sdk-codegen/tests/capability-typescript.cjs" "$temporary/js/generated.js"
cp "$repository/tools/sdk-codegen/Cargo.toml" "$temporary/consumer/Cargo.toml"
cp "$repository/tools/sdk-codegen/Cargo.lock" "$temporary/consumer/Cargo.lock"
mkdir -p "$temporary/consumer/tests"
cp "$repository/tools/sdk-codegen/tests/capability-rust.rs.in" "$temporary/consumer/tests/capability.rs"
cargo +"${AHP_RUST_TOOLCHAIN:-1.88.0}" test --quiet --offline --manifest-path "$temporary/consumer/Cargo.toml" --test capability

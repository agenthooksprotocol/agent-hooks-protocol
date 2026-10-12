#!/usr/bin/env bash
# Compile and execute catalogue-derived declarations outside SDK worktrees.
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
    let catalogue = capability_ergonomics::catalogue(&ir)?;
    assert_eq!(catalogue.len(), 32);
    let ts = langs::typescript::emit(&ir)?;
    let rs = langs::rust::emit_from_repository(&ir, repo)?;
    assert!(!ts.contains("export class CapabilityBuilder"));
    assert!(!rs.contains("pub fn intercept() -> Builder"));
    std::fs::write("$temporary/generated.ts", ts)?;
    std::fs::write("$temporary/consumer/src/lib.rs", rs)?;
    Ok(())
}
RS
export CARGO_TARGET_DIR="${AHP_CAPABILITY_TARGET_DIR:-/tmp/ahp-capability-smoke-target}"
export RUSTFLAGS="${RUSTFLAGS:-} -Awarnings"
cargo +"${AHP_RUST_TOOLCHAIN:-1.88.0}" run --quiet --offline --manifest-path "$temporary/emitter/Cargo.toml"
cp "$repository/tools/sdk-codegen/tests/typescript-typedcap-smoke.ts.in" "$temporary/usage.ts"
cp "$repository/tools/sdk-codegen/tests/typescript-generic-input-smoke.ts.in" "$temporary/generic-input.ts"
tsc --strict --noUncheckedIndexedAccess --exactOptionalPropertyTypes --target ES2022 --module commonjs --outDir "$temporary/js" "$temporary/generated.ts" "$temporary/usage.ts" "$temporary/generic-input.ts"
# Compile runtime alias repros separately; usage.ts contains intentional type errors
# and must never be executed.
cp "$repository/tools/sdk-codegen/tests/typescript-capability-alias-smoke.ts.in" "$temporary/capability-alias.ts"
tsc --strict --noUncheckedIndexedAccess --exactOptionalPropertyTypes --target ES2022 --module commonjs --outDir "$temporary/alias-js" "$temporary/generated.ts" "$temporary/capability-alias.ts"
node "$repository/tools/sdk-codegen/tests/capability-typescript.cjs" "$temporary/js/generated.js"
node "$temporary/alias-js/capability-alias.js"
cp "$repository/tools/sdk-codegen/Cargo.toml" "$temporary/consumer/Cargo.toml"
cp "$repository/tools/sdk-codegen/Cargo.lock" "$temporary/consumer/Cargo.lock"
mkdir -p "$temporary/consumer/tests"
cp "$repository/tools/sdk-codegen/tests/capability-rust.rs.in" "$temporary/consumer/tests/capability.rs"
cargo +"${AHP_RUST_TOOLCHAIN:-1.88.0}" test --quiet --offline --manifest-path "$temporary/consumer/Cargo.toml" --test capability

# Each misuse must fail independently, rather than hiding behind another error.
negative() {
    printf 'use ahp_codegen::capability::{self as c, HostCapabilities, Modify, Flow};\n#[test] fn invalid() { %s }\n' "$2" > "$temporary/consumer/tests/invalid.rs"
    if cargo +"${AHP_RUST_TOOLCHAIN:-1.88.0}" check --quiet --offline --manifest-path "$temporary/consumer/Cargo.toml" --test invalid > "$temporary/negative.log" 2>&1; then
        echo "invalid Rust capability construction compiled: $1" >&2
        exit 1
    fi
    grep -Eq 'error\[E(0308|0599)\]' "$temporary/negative.log"
}
negative initial 'let _ = HostCapabilities { tool_before: Some(c::tool_before()), ..Default::default() };'
negative wrong-event 'let _ = HostCapabilities { tool_before: Some(c::tool_after().message()), ..Default::default() };'
negative observe-only 'let _ = HostCapabilities { session_end: Some(c::tool_before().observe()), ..Default::default() };'
negative observe-only-initial 'let _ = HostCapabilities { session_end: Some(c::session_end()), ..Default::default() };'
negative observe-only-wrong-event 'let _ = HostCapabilities { session_end: Some(c::turn_end().observe()), ..Default::default() };'
negative observe-only-control 'let _ = c::session_end().deny();'
negative unsupported-effect 'let _ = c::tool_after().deny();'
negative unsupported-target 'let _ = c::tool_before().modify_output(Modify::Replace);'
negative unsupported-flow 'let _ = c::tool_before().flow(Flow::Continue { remaining_continuations: 1, continuation_count: 0, max_continuations: None });'
negative no-build-stage 'let _ = c::tool_before().deny().build();'
echo 'typed capability static/runtime smoke passed (TypeScript and Rust)'

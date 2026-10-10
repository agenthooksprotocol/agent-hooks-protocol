#!/usr/bin/env bash
set -euo pipefail
repository="$(cd "$(dirname "$0")/../../.." && pwd)"
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT
cargo run --manifest-path "$repository/tools/sdk-codegen/Cargo.toml" -- generate --revision draft --language typescript --output "$temporary/generated.ts"
cp "$repository/tools/sdk-codegen/tests/typescript-host-input-smoke.ts.in" "$temporary/host-input.ts"
cp "$repository/tools/sdk-codegen/tests/typescript-generic-input-smoke.ts.in" "$temporary/generic-input.ts"
tsc --strict --target es2020 --module commonjs --outDir "$temporary/js" "$temporary/generated.ts" "$temporary/host-input.ts" "$temporary/generic-input.ts"
node "$repository/tools/sdk-codegen/tests/typescript-host-input-smoke.cjs" "$temporary/js/generated.js"
node "$repository/tools/sdk-codegen/tests/typescript-ergonomics-smoke.cjs" "$temporary/js/generated.js"

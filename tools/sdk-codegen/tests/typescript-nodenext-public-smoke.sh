#!/usr/bin/env bash
set -euo pipefail
if [[ $# -ne 2 ]]; then
  echo 'usage: typescript-nodenext-public-smoke.sh <generated-facade-directory> <repository>' >&2
  exit 2
fi
facade=$(cd "$1" && pwd)
repository=$(cd "$2" && pwd)
tmp=$(mktemp -d "${TMPDIR:-/tmp}/ahp-ts-nodenext.XXXXXX")
trap 'rm -rf "$tmp"' EXIT
package="$tmp/node_modules/ahp-public"
mkdir -p "$package/src"
cp "$facade"/{raw,codecs,types,public}.ts "$package/src/"
cp "$repository/tools/sdk-codegen/tests/typescript-public-api-smoke.ts.in" "$package/src/public-api.ts"
cat > "$package/package.json" <<'JSON'
{"name":"ahp-public","type":"module","exports":{".":{"types":"./dist/public.d.ts","default":"./dist/public.js"},"./generated":{"types":"./dist/codecs.d.ts","default":"./dist/codecs.js"},"./types":{"types":"./dist/types.d.ts","default":"./dist/types.js"}}}
JSON
tsc --strict --target ES2022 --module NodeNext --moduleResolution NodeNext \
  --noUncheckedIndexedAccess --exactOptionalPropertyTypes --declaration \
  --rootDir "$package/src" --outDir "$package/dist" \
  "$package/src/raw.ts" "$package/src/codecs.ts" "$package/src/public.ts" "$package/src/types.ts" "$package/src/public-api.ts"
cat > "$tmp/consumer.mts" <<'TS'
import { draftCodecs } from 'ahp-public';
import { parseInterceptNoEffectResponse, encodeInterceptNoEffectResponse } from 'ahp-public/generated';
import type { Effect, InterceptNoEffectResponse, ParseResult } from 'ahp-public/generated';
import type { EventResponses, ToolBeforeInput } from 'ahp-public/types';
// @ts-expect-error assembly planning is not exported by the public package.
import type { PendingAttachment, HostInputProjection } from 'ahp-public';
// @ts-expect-error assembly planning is not exported by the generated package facade.
import type { _projectHostInput } from 'ahp-public/generated';
// @ts-expect-error raw runtime modules are not public package subpaths.
import type { JsonValue as Hidden } from 'ahp-public/raw';
const omitted = {jsonrpc:'2.0', id:'neutral', result:{protocolVersion:'draft'}};
const before = JSON.stringify(omitted);
for (const effects of [undefined, []] as const) {
  const wire = effects === undefined ? omitted : {...omitted, result:{...omitted.result, effects}};
  const parsed: ParseResult<InterceptNoEffectResponse> = parseInterceptNoEffectResponse(wire);
  if (!parsed.ok || !parsed.value.result) throw new Error('Neutral effects must parse');
  const consumed: readonly Effect[] = parsed.value.result.effects ?? [];
  if (consumed.length !== 0) throw new Error('Neutral consumption must be empty');
  if (Object.hasOwn(parsed.value.result, 'effects') !== (effects !== undefined)) throw new Error('Wire presence changed');
  const encoded = JSON.parse(encodeInterceptNoEffectResponse(parsed.value));
  if (Object.hasOwn(encoded.result, 'effects') !== (effects !== undefined)) throw new Error('Encoded wire presence changed');
}
if (JSON.stringify(omitted) !== before) throw new Error('Original wire input mutated');
if (parseInterceptNoEffectResponse({...omitted, result:{...omitted.result, effects:null}}).ok) throw new Error('Null effects must fail');
if (draftCodecs.parseInterceptNoEffectResponse !== parseInterceptNoEffectResponse) throw new Error('Public codecs disagree');
if (Object.hasOwn(draftCodecs, '_projectHostInput')) throw new Error('Assembly internals leaked');
type Input = ToolBeforeInput<{path:string}>;
type ToolResponse = EventResponses['tool.before'];
const typedInput: Input = {callId:'call', name:'read', input:{path:'owned'}, path:'/tools/read', origin:'native'};
void typedInput;
console.log('NodeNext external-package public facade smoke passed');
TS
tsc --strict --target ES2022 --module NodeNext --moduleResolution NodeNext \
  --noUncheckedIndexedAccess --exactOptionalPropertyTypes --outDir "$tmp/consumer" "$tmp/consumer.mts"
node "$tmp/consumer/consumer.mjs"

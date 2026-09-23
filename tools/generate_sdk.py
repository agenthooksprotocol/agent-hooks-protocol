#!/usr/bin/env python3
"""Generate draft codecs, canonical schemas and source locks without hand edits."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

repository = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--sdk', type=Path, default=repository.parent / 'typescript-sdk')
parser.add_argument('--all', action='store_true', help='also generate sibling Python, Go and Rust SDK artifacts')
parser.add_argument('--check', action='store_true', help='compare fresh artifacts without modifying SDKs')
parser.add_argument('--output-dir', type=Path, help='stage all languages under this directory for CI')
args = parser.parse_args()
if args.output_dir and args.check:
    parser.error('--output-dir cannot be combined with --check')
manifest_path = repository / 'schema/draft/manifest.json'
manifest = json.loads(manifest_path.read_text())
schemas = [json.loads((repository / doc['path']).read_text()) for doc in manifest['documents']]
manifest_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
targets = [('typescript', args.sdk.resolve() / 'packages/sdk/src/draft', 'generated.ts')]
if args.all or args.output_dir:
    targets += [
        ('python', repository.parent / 'python-sdk/src/agent_hooks_protocol', 'generated.py'),
        ('go', repository.parent / 'go-sdk', 'generated.go'),
        ('rust', repository.parent / 'rust-sdk/src', 'generated.rs'),
    ]
if args.output_dir:
    targets = [(language, args.output_dir / language, filename) for language, _, filename in targets]
# Build once, then invoke the same compiled generator for every language.
subprocess.run(['cargo', 'build', '--quiet', '--locked', '--manifest-path', str(repository / 'tools/sdk-codegen/Cargo.toml')], check=True)
metadata = json.loads(subprocess.check_output(['cargo', 'metadata', '--no-deps', '--format-version', '1', '--manifest-path', str(repository / 'tools/sdk-codegen/Cargo.toml')]))
generator = Path(metadata['target_directory']) / 'debug/ahp-codegen'
for language, destination, filename in targets:
    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary)
        subprocess.run([str(generator), 'generate', '--repository', str(repository), '--revision', 'draft', '--language', language, '--output', str(output / filename)], check=True)
        if language == 'go':
            subprocess.run(['gofmt', '-w', str(output / filename)], check=True)
        elif language == 'rust':
            subprocess.run(['rustfmt', '+1.88.0', '--edition', '2024', str(output / filename)], check=True)
        if language == 'typescript':
            (output / 'schemas.ts').write_text('// Generated canonical schema bundle. DO NOT EDIT.\nexport const schemas = ' + json.dumps(schemas, indent=2) + ';\n')
        else:
            (output / 'schemas.json').write_text(json.dumps(schemas, indent=2) + '\n')
        lock = {'schemaRevision': 'draft', 'protocolVersion': 'draft', 'generatorVersion': '0.1.0', 'language': language, 'schemaManifestSha256': manifest_hash, 'documents': manifest['documents']}
        (output / 'ahp-codegen.lock.json').write_text(json.dumps(lock, indent=2) + '\n')
        for artifact in output.iterdir():
            target = destination / artifact.name
            if args.check:
                if not target.is_file() or artifact.read_bytes() != target.read_bytes():
                    raise SystemExit(f'Stale draft artifact: {target}')
            else:
                destination.mkdir(parents=True, exist_ok=True)
                target.write_bytes(artifact.read_bytes())
        print(f'{language}: draft codec, canonical schemas and lock {"match" if args.check else "generated"}.')

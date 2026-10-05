#!/usr/bin/env python3
"""Generate draft codecs, canonical schemas and source locks without hand edits."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

repository = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--sdk', type=Path, default=repository.parent / 'typescript-sdk')
parser.add_argument('--go-sdk', type=Path, help='generate only Go artifacts into this SDK root')
parser.add_argument('--python-sdk', type=Path, help='generate only Python artifacts into this SDK root')
parser.add_argument('--rust-sdk', type=Path, help='generate only Rust artifacts into this SDK root')
parser.add_argument('--all', action='store_true', help='also generate sibling Python, Go and Rust SDK artifacts')
parser.add_argument('--check', action='store_true', help='compare fresh artifacts without modifying SDKs')
parser.add_argument('--output-dir', type=Path, help='stage all languages under this directory for CI')
args = parser.parse_args()
if args.output_dir and args.check:
    parser.error('--output-dir cannot be combined with --check')
if sum(bool(value) for value in (args.go_sdk, args.rust_sdk, args.python_sdk)) > 1:
    parser.error('--go-sdk, --rust-sdk and --python-sdk are mutually exclusive')
source_commit = subprocess.check_output(['git', '-C', str(repository), 'rev-parse', 'HEAD'], text=True).strip()
manifest_path = repository / 'schema/draft/manifest.json'
manifest = json.loads(manifest_path.read_text())
schemas = [json.loads((repository / doc['path']).read_text()) for doc in manifest['documents']]
manifest_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
targets = [('typescript', args.sdk.resolve() / 'packages/sdk/src/draft', 'generated.ts')]
if args.all or args.output_dir:
    targets += [
        ('python', repository.parent / 'python-sdk/src/agenthooksprotocol', 'generated.py'),
        ('go', repository.parent / 'go-sdk', 'generated.go'),
        ('rust', repository.parent / 'rust-sdk/src', 'generated.rs'),
    ]
if args.go_sdk:
    targets = [('go', args.go_sdk.resolve(), 'generated.go')]
if args.rust_sdk:
    targets = [('rust', args.rust_sdk.resolve() / 'src', 'generated.rs')]
if args.python_sdk:
    targets = [('python', args.python_sdk.resolve() / 'src/agenthooksprotocol', 'generated.py')]
if args.output_dir:
    targets = [(language, args.output_dir / language, filename) for language, _, filename in targets]
# Refuse a different Python formatter rather than silently changing committed bytes.
if any(language == 'python' for language, _, _ in targets):
    version = subprocess.check_output([sys.executable, '-m', 'ruff', '--version'], text=True).strip()
    if version != 'ruff 0.12.12':
        raise SystemExit('Install the pinned formatter: python3 -m pip install ruff==0.12.12')
# Build once, then invoke the same compiled generator for every language.
subprocess.run(['cargo', 'build', '--quiet', '--locked', '--manifest-path', str(repository / 'tools/sdk-codegen/Cargo.toml')], check=True)
metadata = json.loads(subprocess.check_output(['cargo', 'metadata', '--no-deps', '--format-version', '1', '--manifest-path', str(repository / 'tools/sdk-codegen/Cargo.toml')]))
generator = Path(metadata['target_directory']) / 'debug/ahp-codegen'
for language, destination, filename in targets:
    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary)
        subprocess.run([str(generator), 'generate', '--repository', str(repository), '--revision', 'draft', '--language', language, '--output', str(output / filename)], check=True)
        if language == 'python':
            subprocess.run([str(generator), 'generate', '--repository', str(repository), '--revision', 'draft', '--language', 'python-facade', '--output', str(output)], check=True)
        if language == 'go':
            subprocess.run([str(generator), 'generate', '--repository', str(repository), '--revision', 'draft', '--language', 'go-facade', '--output', str(output)], check=True)
            go_env = dict(os.environ, GOTOOLCHAIN='go1.27.0+auto')
            goroot = subprocess.check_output(['go', 'env', 'GOROOT'], env=go_env, text=True).strip()
            subprocess.run([str(Path(goroot) / 'bin/gofmt'), '-w', *map(str, output.rglob('*.go'))], check=True)
        elif language == 'rust':
            subprocess.run(['rustfmt', '+1.88.0', '--edition', '2024', str(output / filename)], check=True)
        if language == 'typescript':
            (output / 'schemas.ts').write_text('// Generated canonical schema bundle. DO NOT EDIT.\nexport const schemas = ' + json.dumps(schemas, indent=2) + ';\n')
        else:
            schema_bundle = json.dumps(schemas, indent=2) + '\n'
            (output / 'schemas.json').write_text(schema_bundle)
            if language == 'go':
                canonical = output / 'internal/canonical/schemas.json'
                canonical.parent.mkdir(parents=True, exist_ok=True)
                canonical.write_text(schema_bundle)
        if language == 'typescript':
            for source in ('generated.ts', 'schemas.ts'):
                subprocess.run(['npx', '--yes', 'prettier@3.6.2', '--no-config', '--no-editorconfig', '--write', str(output / source)], check=True)
        elif language == 'python':
            subprocess.run([sys.executable, '-m', 'ruff', 'format', '--isolated', '--target-version', 'py311', *map(str, sorted(output.rglob('*.py')))], check=True)
        lock = {'sourceRepository': 'agenthooksprotocol/agent-hooks-protocol', 'sourceCommit': source_commit, 'schemaRevision': 'draft', 'protocolVersion': 'draft', 'generatorVersion': '0.1.0', 'language': language, 'schemaManifestSha256': manifest_hash, 'documents': manifest['documents']}
        (output / 'ahp-codegen.lock.json').write_text(json.dumps(lock, indent=2) + '\n')
        for artifact in output.rglob("*"):
            if not artifact.is_file():
                continue
            target = destination / artifact.relative_to(output)
            if args.check:
                if not target.is_file() or artifact.read_bytes() != target.read_bytes():
                    raise SystemExit(f'Stale draft artifact: {target}')
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(artifact.read_bytes())
        if language in ('rust', 'python') and not args.output_dir:
            sdk_root = destination.parent if language == 'rust' else destination.parent.parent
            target = sdk_root / 'ahp-codegen.lock.json'
            contents = (output / 'ahp-codegen.lock.json').read_bytes()
            if args.check:
                if not target.is_file() or target.read_bytes() != contents:
                    raise SystemExit(f'Stale draft artifact: {target}')
            else:
                target.write_bytes(contents)
        print(f'{language}: draft codec, canonical schemas and lock {"match" if args.check else "generated"}.')

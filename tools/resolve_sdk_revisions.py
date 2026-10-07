#!/usr/bin/env python3
"""Resolve one immutable SDK snapshot from public GitHub metadata."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess


LANGUAGES = ("go", "python", "rust", "typescript")
SHA = re.compile(r"[0-9a-f]{40}")


def api(endpoint):
    return json.loads(subprocess.check_output(["gh", "api", endpoint], text=True))


def validate_manifest(manifest):
    if (set(manifest) != {"version", "sdks"}
            or type(manifest["version"]) is not int or manifest["version"] != 1
            or set(manifest["sdks"]) != set(LANGUAGES)):
        raise ValueError("Expected version 1 manifest with exactly four SDKs")
    for language, sdk in manifest["sdks"].items():
        if (set(sdk) != {"path", "repository", "revision"}
                or sdk["path"] != f"{language}-sdk"
                or sdk["repository"] != f"agenthooksprotocol/{language}-sdk"
                or not isinstance(sdk["revision"], str)
                or not SHA.fullmatch(sdk["revision"])):
            raise ValueError(f"Unexpected manifest entry: {language}")


def resolve():
    manifest = {"version": 1, "sdks": {}}
    for language in LANGUAGES:
        path = f"{language}-sdk"
        repo = f"agenthooksprotocol/{path}"
        obj = api(f"repos/{repo}/git/ref/heads/main")["object"]
        if obj["type"] != "commit" or not SHA.fullmatch(obj["sha"]):
            raise ValueError(f"Invalid main commit for {repo}")
        manifest["sdks"][language] = {
            "path": path, "repository": repo, "revision": obj["sha"],
        }
        print(f"{repo}:main -> {obj['sha']}")
    validate_manifest(manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = resolve()
    # All reads must succeed before publishing this run's immutable snapshot.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=4) + "\n")
    if "GITHUB_OUTPUT" in os.environ:
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            print("manifest=" + json.dumps(manifest, separators=(",", ":")), file=output)
            for language, sdk in manifest["sdks"].items():
                print(f"{language}={sdk['revision']}", file=output)


if __name__ == "__main__":
    main()

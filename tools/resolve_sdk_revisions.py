#!/usr/bin/env python3
"""Resolve one immutable SDK snapshot from public GitHub metadata."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import quote


MANIFEST = Path(__file__).resolve().parents[1] / "interop/sdk-revisions.json"
LANGUAGES = ("go", "python", "rust", "typescript")
SHA = re.compile(r"[0-9a-f]{40}")
VERSION = r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"


def api(endpoint, *, pages=False):
    command = ["gh", "api", endpoint]
    if pages:
        command += ["--paginate", "--slurp"]
    return json.loads(subprocess.check_output(command, text=True))


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


def latest_release(repo, language):
    prefix = "agenthooksprotocol-v" if language == "typescript" else "v"
    candidates = []
    for page in api(f"repos/{repo}/releases?per_page=100", pages=True):
        for release in page:
            match = re.fullmatch(prefix + VERSION, release["tag_name"])
            if (match and not release["draft"] and not release["prerelease"]
                    and release["published_at"]):
                candidates.append((tuple(map(int, match.groups())), release["tag_name"]))
    return max(candidates)[1] if candidates else None


def tag_commit(repo, tag):
    obj = api(f"repos/{repo}/git/ref/tags/{quote(tag, safe='')}")["object"]
    seen = set()
    while True:
        sha = obj["sha"]
        if not SHA.fullmatch(sha) or sha in seen:
            raise ValueError(f"Invalid or cyclic tag object for {repo}:{tag}")
        seen.add(sha)
        if obj["type"] == "commit":
            return sha
        if obj["type"] != "tag" or len(seen) > 16:
            raise ValueError(f"Tag does not resolve to a commit: {repo}:{tag}")
        obj = api(f"repos/{repo}/git/tags/{sha}")["object"]


def release_succeeded(repo, language, sha):
    endpoint = (f"repos/{repo}/actions/workflows/release.yml/runs"
                f"?event=push&branch=main&head_sha={sha}&per_page=100")
    required_job = "release-please" if language == "go" else "publish"
    for page in api(endpoint, pages=True):
        for run in page["workflow_runs"]:
            if not (run["head_sha"] == sha and run["head_branch"] == "main"
                    and run["event"] == "push"
                    and run["path"] == ".github/workflows/release.yml"
                    and run["status"] == "completed" and run["conclusion"] == "success"):
                continue
            jobs = api(f"repos/{repo}/actions/runs/{run['id']}/attempts/"
                       f"{run['run_attempt']}/jobs?per_page=100", pages=True)
            if any(job["name"] == required_job and job["status"] == "completed"
                   and job["conclusion"] == "success"
                   for page in jobs for job in page["jobs"]):
                return True
    return False


def resolve(manifest, source):
    validate_manifest(manifest)
    for language, sdk in manifest["sdks"].items():
        repo = sdk["repository"]
        if source == "main":
            obj = api(f"repos/{repo}/git/ref/heads/main")["object"]
            if obj["type"] != "commit" or not SHA.fullmatch(obj["sha"]):
                raise ValueError(f"Invalid main commit for {repo}")
            revision = obj["sha"]
            ref = "main"
        else:
            ref = latest_release(repo, language)
            if ref is None:
                raise ValueError(f"No stable release for {repo}")
            revision = tag_commit(repo, ref)
            if not release_succeeded(repo, language, revision):
                raise ValueError(f"Release has not completed successfully: {repo}:{ref}")
        sdk["revision"] = revision
        print(f"{repo}:{ref} -> {revision}")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("main", "release"), default="main")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve() == MANIFEST.resolve():
        parser.error("output must not overwrite the checked-in manifest")
    manifest = resolve(json.loads(MANIFEST.read_text()), args.source)
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

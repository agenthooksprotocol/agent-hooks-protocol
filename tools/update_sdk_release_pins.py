#!/usr/bin/env python3
"""Advance integration pins using public GitHub release/workflow metadata only."""

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
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


def update(manifest):
    validate_manifest(manifest)
    for language, sdk in manifest["sdks"].items():
        repo = sdk["repository"]
        tag = latest_release(repo, language)
        if tag is None:
            print(f"{language}: no stable release; keeping pin")
            continue
        candidate = tag_commit(repo, tag)
        if candidate == sdk["revision"]:
            print(f"{language}: already pinned to {tag}")
            continue
        if not release_succeeded(repo, language, candidate):
            print(f"{language}: {tag} lacks successful release/publish jobs; keeping pin")
            continue
        comparison = api(f"repos/{repo}/compare/{sdk['revision']}...{candidate}")
        status = comparison["status"]
        if status in {"behind", "diverged", "identical"}:
            print(f"{language}: {tag} is {status} relative to pin; keeping pin")
            continue
        if status != "ahead":
            raise ValueError(f"Unexpected comparison status for {repo}: {status}")
        sdk["revision"] = candidate
        print(f"{language}: {tag} -> {candidate}")
    return manifest


def main():
    original = MANIFEST.read_text()
    manifest = update(json.loads(original))
    updated = json.dumps(manifest, indent=4) + "\n"
    if json.loads(original) == manifest:
        return
    # Do not touch the manifest until every SDK's API reads and checks succeed.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=MANIFEST.parent, delete=False) as out:
            temporary = Path(out.name)
            out.write(updated)
        temporary.chmod(MANIFEST.stat().st_mode & 0o777)
        os.replace(temporary, MANIFEST)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    main()

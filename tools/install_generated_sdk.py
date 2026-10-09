#!/usr/bin/env python3
"""Install one generated SDK artifact and report its exact PR staging paths."""

import argparse
import json
from pathlib import Path
import re
import shutil


DESTINATIONS = {
    "typescript": Path("packages/sdk/src/draft"),
    "python": Path("src/agenthooksprotocol"),
    "go": Path("."),
    "rust": Path("src"),
}


def install_sdk(source: Path, sdk: Path, language: str) -> list[str]:
    lock = json.loads((source / "ahp-codegen.lock.json").read_text())
    commit = lock["sourceCommit"]
    if lock["language"] != language or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Expected a matching language and immutable sourceCommit")
    workflow = sdk / ".github/workflows/ci.yml"
    contents = workflow.read_text()
    # Match only the protocol checkout, never action or SDK repository pins.
    pattern = (
        r"(?m)^(?P<indent> +)repository: agenthooksprotocol/agent-hooks-protocol\n"
        r"(?P=indent)ref: [0-9a-f]{40}(?P<comment>[^\n]*)$"
    )
    updated, count = re.subn(
        pattern,
        lambda match: (
            f"{match['indent']}repository: agenthooksprotocol/agent-hooks-protocol\n"
            f"{match['indent']}ref: {commit}{match['comment']}"
        ),
        contents,
    )
    if count != 1:
        raise ValueError("Expected exactly one immutable protocol checkout in SDK CI")

    paths = []
    for artifact in sorted(source.rglob("*")):
        # Smoke tests may leave Python bytecode in the uploaded artifact.
        if "__pycache__" in artifact.parts or artifact.suffix in (".pyc", ".pyo"):
            continue
        if artifact.is_file():
            relative = DESTINATIONS[language] / artifact.relative_to(source)
            target = sdk / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(artifact, target)
            target.chmod(0o644)
            paths.append(relative.as_posix())
    if language in ("python", "rust"):
        shutil.copyfile(source / "ahp-codegen.lock.json", sdk / "ahp-codegen.lock.json")
        (sdk / "ahp-codegen.lock.json").chmod(0o644)
        paths.append("ahp-codegen.lock.json")
    workflow.write_text(updated)
    paths.append(".github/workflows/ci.yml")
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--language", choices=DESTINATIONS, required=True)
    args = parser.parse_args()
    print("\n".join(install_sdk(args.source, args.sdk, args.language)))


if __name__ == "__main__":
    main()

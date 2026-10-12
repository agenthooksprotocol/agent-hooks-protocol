#!/usr/bin/env python3
"""Compile generated boundaries and check codec/default inference with mypy.

Usage: python-boundary-typing-smoke.py <generated-python-directory>
Run with mypy on PATH. Runtime seams are stubs of the handwritten API; the
boundary and canonical model annotations under test are actual generated files.
"""
from __future__ import annotations

import ast
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main() -> None:
    generated = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="ahp-boundary-typing-") as temporary:
        root = Path(temporary)
        package = root / "consumer_sdk"
        shutil.copytree(generated, package)
        (package / "__init__.py").touch()
        (package / "codec.pyi").write_text("from typing import Generic, TypeVar\nT = TypeVar('T')\nclass Codec(Generic[T]): ...\n")
        (package / "_content.pyi").write_text("from typing import Any\nclass ContentContext: ...\nContentSources = dict[tuple[str, ...], Any]\n")
        (package / "_hooks.pyi").write_text("""from typing import Any, Generic, TypeVar
from ._boundaries import BoundaryMixin
from .contract import Candidate
from ._models import Event
I = TypeVar('I')
R = TypeVar('R')
P = TypeVar('P')
class HookResult(Generic[I, R, P]):
    @property
    def event(self) -> Event: ...
    @property
    def input(self) -> I: ...
    @property
    def result(self) -> R: ...
    @property
    def candidate(self) -> Candidate[R, P] | None: ...
class Hooks(BoundaryMixin):
    async def dispatch(self, *args: Any, **kwargs: Any) -> Any: ...
""")
        tree = ast.parse((package / "_boundaries.py").read_text())
        methods = [node.name for owner in tree.body if isinstance(owner, ast.ClassDef)
                   for node in owner.body if isinstance(node, ast.AsyncFunctionDef) and not node.decorator_list]
        assert len(methods) == 32
        lines = ["from typing import assert_type", "from consumer_sdk import _models as m",
                 "from consumer_sdk._hooks import Hooks, HookResult", "from consumer_sdk.codec import Codec",
                 "from consumer_sdk.contract import Candidate",
                 "async def check(h: Hooks, i: Codec[int], r: Codec[str], p: Codec[bool]) -> None:"]
        for method in methods:
            default = {"model_request_before": "m.CanonicalMessages", "context_compact_before": "m.TextParts",
                       "user_elicitation_request": "m.McpElicitationElicitResult"}.get(method, "object")
            for mask in range(8):
                args = [f"{name}_codec={codec}" for index, (name, codec) in enumerate(
                    [("input", "i"), ("result", "r"), ("provenance", "p")]) if mask & (1 << index)]
                types = ["int" if mask & 1 else "object", "str" if mask & 2 else default,
                         "bool" if mask & 4 else "object"]
                name = f"{method}_{mask}"
                lines.append(f"    {name} = await h.{method}({{}}, {', '.join(args)})")
                lines.append(f"    assert_type({name}, HookResult[{', '.join(types)}])")
                lines.append(f"    assert_type({name}.event, m.Event)")
                lines.append(f"    assert_type({name}.result, {types[1]})")
                lines.append(f"    assert_type({name}.candidate, Candidate[{types[1]}, {types[2]}] | None)")
            # A contextual annotation cannot manufacture the missing codecs.
            lines.append(f"    bad_{method}: HookResult[int, str, bool] = await h.{method}({{}})  # type: ignore[assignment]")
        lines.extend([
            "    text = await h.context_compact_before({})",
            "    messages = await h.model_request_before({})",
            "    elicit = await h.user_elicitation_request({})",
            "    assert_type(elicit.result.action, str)",
            "    wrong_messages: m.CanonicalMessages = text.result  # type: ignore[assignment]",
            "    wrong_text: m.TextParts = messages.result  # type: ignore[assignment]",
            "    wrong_answers: str = elicit.result  # type: ignore[assignment]",
        ])
        consumer = root / "consumer.py"
        consumer.write_text("\n".join(lines) + "\n")
        subprocess.run([sys.executable, "-m", "compileall", "-q", str(package)], check=True)
        checked = subprocess.run(["mypy", "--follow-imports=silent", "--ignore-missing-imports",
                                  "--warn-unused-ignores", str(consumer)], cwd=root, text=True, capture_output=True)
        if checked.returncode:
            raise AssertionError(checked.stdout + checked.stderr)
        print("Python boundary typing: 32 origins, 256 codec combinations, rejected codec-free contextual casts")


if __name__ == "__main__":
    main()

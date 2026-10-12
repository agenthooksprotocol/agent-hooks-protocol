"""Check the one public EventType alias and its static string boundary."""
import importlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import typing


def check(package: str) -> tuple[str, ...]:
    event = importlib.import_module(package + ".event")
    models = importlib.import_module(package + "._models")
    assert event.EventType is models.EventType
    assert "EventType" in models.__all__
    assert typing.get_origin(event.EventType) is typing.Literal
    names = typing.get_args(event.EventType)
    assert len(names) == len(set(names)) == 32
    assert all(isinstance(name, str) for name in names)
    for name in names:
        assert json.loads(json.dumps({"type": name})) == {"type": name}
    return names


def check_typing(directory: Path) -> None:
    names = check("facade_contract")
    checker = os.environ.get("MYPY", "mypy")
    with tempfile.TemporaryDirectory(prefix="ahp-event-type-") as temporary:
        root = Path(temporary)
        (root / "facade_contract").symlink_to(directory.resolve(), target_is_directory=True)
        header = "from facade_contract.event import EventType\n\ndef subscribes(event: EventType) -> None:\n    pass\n\n"
        positive = root / "positive.py"
        positive.write_text(header + "\n".join(f"subscribes({name!r})" for name in names) + "\n")
        negative = root / "negative.py"
        negative.write_text(header + 'dynamic: str = "tool.before"\nsubscribes(dynamic)\nsubscribes("tool.not-an-event")\nfrom facade_contract.response import response_for_request\nresponse_for_request(dynamic, "{}")\nresponse_for_request("tool.not-an-event", "{}")\n')
        for source in (positive, negative):
            result = subprocess.run([checker, "--follow-imports=silent", "--ignore-missing-imports",
                                     "--strict", "--show-error-codes", str(source)],
                                    cwd=root, text=True, capture_output=True)
            if source == positive:
                assert result.returncode == 0, result.stdout + result.stderr
            else:
                errors = re.findall(r"negative.py:(\d+): error:.*\[(?:arg-type|call-overload)\]", result.stdout)
                assert result.returncode != 0 and errors == ["7", "8", "10", "11"], result.stdout + result.stderr

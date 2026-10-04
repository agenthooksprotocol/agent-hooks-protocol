"""Go adapter commands shared by standalone matrices and prepared integration runs."""
import os
import atexit
import subprocess
import tempfile
import threading
from pathlib import Path

GO_ADAPTERS = ('elicitation', 'compaction', 'compaction-wire')
GO_SDK = Path(__file__).resolve().parents[2] / 'go-sdk'


_build_lock = threading.Lock()
_builds = {}


def go_command(name, sdk_root=None):
    if name not in GO_ADAPTERS:
        raise ValueError('unknown Go adapter')
    directory = os.environ.get('AHP_GO_ADAPTER_DIR')
    if directory:
        # The orchestrator builds these in a fresh run-owned directory. Never
        # fall back to an old executable if a declared prerequisite is missing.
        return [str(Path(directory) / name)]
    # Build once before launch: `go run` leaves an executable descendant that
    # can hold control sockets open after the launcher is terminated.
    root = Path(GO_SDK if sdk_root is None else sdk_root).resolve()
    key = (str(root), name)
    with _build_lock:
        if key not in _builds:
            directory = tempfile.TemporaryDirectory(prefix='ahp-go-' + name + '-')
            binary = str(Path(directory.name) / name)
            try:
                subprocess.run(['go', '-C', str(root), 'build', '-o', binary,
                                './cmd/' + name], check=True, capture_output=True, timeout=120)
            except BaseException:
                directory.cleanup()
                raise
            atexit.register(directory.cleanup)
            _builds[key] = binary
        return [_builds[key]]

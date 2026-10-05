"""Go adapter commands shared by standalone matrices and prepared integration runs."""
import os
import atexit
import subprocess
import tempfile
import threading
from pathlib import Path

GO_ADAPTERS = ('elicitation', 'compaction', 'compaction-wire',
               'interop', 'lifecycle-client', 'lifecycle-server')
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


def prepared_go_manifest(manifest, roles):
    """Replace only exact known launchers; standalone manifests stay unchanged.

    Prepared paths are authoritative, even if missing: never build or fall back
    to go run or a cached standalone executable in a prepared integration run.
    """
    result = dict(manifest)
    if result.get('language') != 'go' or not os.environ.get('AHP_GO_ADAPTER_DIR'):
        return result
    commands = {
        'client': ('interop', ['client']),
        'server': ('interop', ['server']),
        'lifecycleClient': ('lifecycle-client', []),
        'lifecycleServer': ('lifecycle-server', []),
    }
    for role in roles:
        name, args = commands[role]
        if result.get(role) != ['go', 'run', './cmd/' + name, *args]:
            raise ValueError('unexpected Go ' + role + ' launcher')
        result[role] = go_command(name) + args
    return result

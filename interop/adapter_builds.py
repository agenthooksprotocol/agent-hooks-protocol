"""Go adapter commands shared by standalone matrices and prepared integration runs."""
import os
from pathlib import Path

GO_ADAPTERS = ('elicitation', 'compaction', 'compaction-wire')
GO_SDK = Path(__file__).resolve().parents[2] / 'go-sdk'


def go_command(name):
    if name not in GO_ADAPTERS:
        raise ValueError('unknown Go adapter')
    directory = os.environ.get('AHP_GO_ADAPTER_DIR')
    if directory:
        # The orchestrator builds these in a fresh run-owned directory. Never
        # fall back to an old executable if a declared prerequisite is missing.
        return [str(Path(directory) / name)]
    # Standalone matrix/test invocations also work without hidden prebuilds.
    return ['go', '-C', str(GO_SDK), 'run', './cmd/' + name]

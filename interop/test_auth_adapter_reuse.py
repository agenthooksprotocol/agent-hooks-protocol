"""Authentication probes must consume the same prepared launchers as matrices."""
import unittest
from unittest.mock import Mock, patch

import test_auth_matrix as auth_matrix


class PreparedAuthAdapterTests(unittest.TestCase):
    def test_auth_server_uses_prepared_manifest_loader(self):
        process = Mock()
        endpoints = {'controlEndpoint': 'http://127.0.0.1:1'}
        with patch.object(auth_matrix, 'load_adapter', return_value={'server': ['/runtime/interop', 'server']}) as load, \
                patch.object(auth_matrix.subprocess, 'Popen', return_value=process) as launch, \
                patch.object(auth_matrix, 'wait_ready', return_value=endpoints), \
                patch.object(auth_matrix, 'stop') as stop:
            with auth_matrix.server('rust', 'none') as actual:
                self.assertEqual(actual, endpoints)
        load.assert_called_once_with(auth_matrix.ROOT / 'rust-sdk/interop/adapter.json')
        self.assertEqual(launch.call_args.args[0][:2], ['/runtime/interop', 'server'])
        self.assertEqual(launch.call_args.kwargs['cwd'], auth_matrix.ROOT / 'rust-sdk')
        stop.assert_called_once_with(process)

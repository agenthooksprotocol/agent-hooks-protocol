import os
from pathlib import Path
import unittest
from unittest.mock import patch

from adapter_builds import GO_ADAPTERS, GO_SDK, go_command


class AdapterBuildTests(unittest.TestCase):
    def test_standalone_uses_source_not_global_temp_executable(self):
        with patch.dict(os.environ, {}, clear=True):
            for name in GO_ADAPTERS:
                self.assertEqual(go_command(name),
                                 ['go', '-C', str(GO_SDK), 'run', './cmd/' + name])

    def test_prepared_uses_only_declared_directory(self):
        with patch.dict(os.environ, {'AHP_GO_ADAPTER_DIR': '/isolated/run/bin'}):
            for name in GO_ADAPTERS:
                self.assertEqual(go_command(name), [str(Path('/isolated/run/bin') / name)])
            with self.assertRaises(ValueError):
                go_command('../unknown')

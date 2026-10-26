"""Configuration boundary checks; real FUSE/SMB evidence is documented separately."""
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('entrypoint', Path(__file__).resolve().parents[1]/'docker/entrypoint.py')
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)

class ConfigurationTests(unittest.TestCase):
    def test_defaults_and_fixed_paths(self):
        with patch.dict(os.environ, {}, clear=True):
            cfg = entry.configuration()
        self.assertEqual((cfg['read_ahead_mib'],cfg['read_ahead_low_water_mib']), (999,250))
        self.assertEqual(cfg['source'], '/media')
        self.assertEqual(cfg['mount'], '/export/media')
        self.assertEqual(cfg['rc_port'], 5579)

    def test_invalid_budgets_and_windows_rejected(self):
        for env in ({'READ_AHEAD_MIB':'0'}, {'MIN_FREE_GIB':'0'}, {'MAX_CACHE_GIB':'1001'},
                    {'READ_AHEAD_MIB':'64','LOW_WATER_MIB':'64'}, {'LOW_WATER_MIB':'-1'},
                    {'READ_AHEAD_MIB':'infinity'}):
            with self.subTest(env=env), patch.dict(os.environ,env,clear=True):
                with self.assertRaises(ValueError):entry.configuration()

if __name__ == '__main__':unittest.main()

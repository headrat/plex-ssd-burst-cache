import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
spec=importlib.util.spec_from_file_location('lifecycle', sys.argv.pop(1))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class LifecycleTests(unittest.TestCase):
    def test_missing_storage_launches_nothing(self):
        with patch.object(m,'ready',return_value=False), patch.object(m,'command') as command, patch.object(m,'dashboard_start') as dashboard:
            self.assertEqual(m.start()['result'],'deferred')
            command.assert_not_called();dashboard.assert_not_called()

    def test_existing_cache_does_not_reconnect_share(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'status.json').write_text(json.dumps({'phase':'active','checked_at':m.time.time()}))
            with patch.object(m,'ROOT',root), patch.object(m,'ready',return_value=True), patch.object(m,'dashboard_start',return_value={'pid':2}), patch.object(m,'owner',return_value={'pid':1}), patch.object(m,'command') as command:
                self.assertEqual(m.start()['result'],'already-running')
                self.assertEqual(command.call_count,1)
                self.assertIn('start',command.call_args[0][0])

    def test_cold_start_reconnects_only_trial_share(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'status.json').write_text(json.dumps({'phase':'active','checked_at':m.time.time()}))
            with patch.object(m,'ROOT',root), patch.object(m,'ready',return_value=True), patch.object(m,'dashboard_start',return_value={'pid':2}), patch.object(m,'owner',side_effect=[None,{'pid':1}]), patch.object(m,'command') as command:
                self.assertEqual(m.start()['result'],'started')
                self.assertEqual(command.call_args[0][0],['smbcontrol','smbd','close-share','PlexReadCache'])

    def test_stale_service_does_not_restart_or_reconnect(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'status.json').write_text(json.dumps({'phase':'active','checked_at':1}))
            with patch.object(m,'ROOT',root), patch.object(m,'ready',return_value=True), patch.object(m,'dashboard_start'), patch.object(m,'owner',return_value={'pid':1}), patch.object(m,'command') as command:
                with self.assertRaises(RuntimeError):m.start()
                self.assertEqual(command.call_count,1)

    def test_reused_pid_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'owner.json').write_text(json.dumps({'pid':m.os.getpid(),'start':Path('/proc/self/stat').read_text().split()[21]}))
            with patch.object(m,'ROOT',root):
                with self.assertRaises(RuntimeError):m.owner('owner.json','cache_service.py')
            (root/'owner.json').write_text(json.dumps({'pid':m.os.getpid(),'start':'0'}))
            with patch.object(m,'ROOT',root):self.assertIsNone(m.owner('owner.json','cache_service.py'))

    def test_stop_delegates_existing_cache_cleanup(self):
        with patch.object(m,'owner',return_value=None), patch.object(m,'command') as command, patch.object(m.os.path,'ismount',return_value=False):
            self.assertEqual(m.stop(),{'result':'stopped','mount_present':False})
            self.assertIn('stop',command.call_args[0][0])

unittest.main()

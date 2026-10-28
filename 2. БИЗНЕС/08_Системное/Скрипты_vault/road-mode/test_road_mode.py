"""Behavior checks without changing the Mac's power, sound, or applications."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('road_mode', Path(__file__).with_name('road_mode.py'))
road = importlib.util.module_from_spec(spec)
spec.loader.exec_module(road)


class RoadModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = patch.object(road, 'BASE', Path(self.tmp.name))
        self.state = patch.object(road, 'STATE', Path(self.tmp.name) / 'state.json')
        self.base.start()
        self.state.start()
        self.addCleanup(self.base.stop)
        self.addCleanup(self.state.stop)

    def test_open_lid_does_not_cancel_before_first_close(self):
        state = {'active': True, 'seen_closed': False}
        self.assertFalse(road.should_restore(state, False))
        self.assertFalse(road.should_restore(state, True))
        state['seen_closed'] = True
        self.assertFalse(road.should_restore(state, True))
        self.assertTrue(road.should_restore(state, False))
        state['active'] = False
        self.assertFalse(road.should_restore(state, False))

    def test_duplicate_start_preserves_session_and_does_not_quit_apps(self):
        state = {'active': True, 'seen_closed': True, 'started_at': 'original'}
        road.save(state)
        with patch.object(road, 'run') as run:
            road.start()
        self.assertEqual(road.read(), state)
        run.assert_not_called()

    def test_failed_restore_remains_active_for_retry(self):
        state = {'active': True, 'seen_closed': True}
        road.save(state)
        with patch.object(road, 'power', side_effect=RuntimeError('no grant')):
            with self.assertRaises(RuntimeError):
                road.restore(state, 'lid opened')
        self.assertTrue(road.read()['active'])
        self.assertNotIn('restored_at', road.read())

    def test_restore_unmutes_and_never_reopens_telegram(self):
        state = {'active': True, 'seen_closed': True}
        road.save(state)
        with patch.object(road, 'power') as power, patch.object(road, 'run') as run, patch.object(road, 'set_muted') as mute, patch.object(road, 'verify'):
            road.restore(state, 'lid opened')
        self.assertFalse(road.read()['active'])
        self.assertEqual(road.read()['reason'], 'lid opened')
        self.assertEqual([call.args for call in power.call_args_list], [('lowpowermode', 0), ('disablesleep', 0)])
        commands = [call.args[0] for call in run.call_args_list]
        mute.assert_called_once_with(False)
        self.assertFalse(any('Telegram' in ' '.join(command) for command in commands))

    def test_audio_change_retries_if_device_still_muted(self):
        with patch.object(road, 'run', side_effect=['', 'output muted:true', '', 'output muted:false']) as run, patch.object(road.time, 'sleep'):
            road.set_muted(False)
        self.assertEqual(run.call_count, 4)

    def test_audio_change_fails_if_mute_does_not_apply(self):
        with patch.object(road, 'run', return_value='output muted:true'), patch.object(road.time, 'sleep'):
            with self.assertRaises(RuntimeError):
                road.set_muted(False)

    def test_unreadable_sensor_fails_instead_of_assuming_open(self):
        with patch.object(road, 'run', return_value='no sensor'):
            with self.assertRaises(RuntimeError):
                road.closed()

    def test_verification_rejects_only_one_power_profile_enabled(self):
        with patch.object(road, 'run', return_value='Battery Power:\n lowpowermode 1\nAC Power:\n lowpowermode 0'):
            with self.assertRaises(RuntimeError):
                road.verify(1)


if __name__ == '__main__':
    unittest.main()

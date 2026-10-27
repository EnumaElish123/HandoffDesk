"""Real Tk lifecycle checks for decorative, nonblocking transitions."""
import time
import tkinter as tk
import unittest

from handoffdesk.motion import MotionController


class MotionTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest('Native Tk unavailable: ' + str(exc))
        self.root.withdraw()
        self.root.update()
        self.errors = []
        self.root.report_callback_exception = lambda *error: self.errors.append(error)
        self.motion = MotionController(self.root)

    def tearDown(self):
        self.motion.close()
        try:
            self.root.destroy()
        except tk.TclError:
            pass
        self.assertEqual(self.errors, [])

    def pump(self, predicate, timeout=2):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.root.update()
            if predicate():
                return
            time.sleep(.005)
        self.fail('Transition did not reach the expected visible state')

    def test_transition_is_nonblocking_finishes_once_and_leaves_no_timer(self):
        samples, completed, unrelated_input = [], [], []
        self.root.after(0, lambda: unrelated_input.append('input accepted'))
        self.motion.run('selection', samples.append, duration=100,
                        finish=lambda: completed.append('settled'))
        self.assertEqual(samples, [0.0])
        self.pump(lambda: bool(unrelated_input))
        self.assertEqual(completed, [], 'An unrelated event was held until the animation ended')
        self.pump(lambda: self.motion.active_count == 0)
        self.assertEqual(samples[-1], 1.0)
        self.assertEqual(samples, sorted(samples))
        self.assertTrue(any(0 < progress < 1 for progress in samples))
        self.assertEqual(completed, ['settled'])
        self.assertFalse(self.root.tk.call('after', 'info'))

    def test_rapid_reselection_discards_obsolete_target(self):
        old, current, completed = [], [], []
        self.motion.run('tab', old.append, duration=150,
                        finish=lambda: completed.append('obsolete'))
        self.pump(lambda: len(old) > 1)
        self.motion.run('tab', current.append, duration=60,
                        finish=lambda: completed.append('current'))
        old_samples = list(old)
        self.pump(lambda: self.motion.active_count == 0)
        self.assertEqual(old, old_samples)
        self.assertLess(old[-1], 1.0)
        self.assertEqual(current[-1], 1.0)
        self.assertEqual(completed, ['current'])

    def test_reduced_motion_settles_current_and_future_effects_immediately(self):
        first, second, done = [], [], []
        self.motion.run('first', first.append, finish=lambda: done.append('first'))
        self.motion.set_reduced(True)
        self.assertEqual(first[-1], 1.0)
        self.assertEqual(self.motion.active_count, 0)
        self.motion.run('second', second.append, finish=lambda: done.append('second'))
        self.assertEqual(second, [1.0])
        self.assertEqual(done, ['first', 'second'])
        self.assertFalse(self.root.tk.call('after', 'info'))
        self.motion.set_reduced(False)
        self.motion.run('third', lambda _: None)
        self.assertEqual(self.motion.active_count, 1)

    def test_close_prevents_later_updates_and_new_animations(self):
        samples, done = [], []
        self.motion.run('effect', samples.append, finish=lambda: done.append(True))
        self.motion.close()
        closed_samples = list(samples)
        self.root.update()
        self.assertEqual(samples, closed_samples)
        self.assertFalse(self.motion.run('new', samples.append))
        self.assertEqual(done, [])
        self.assertEqual(self.motion.active_count, 0)
        self.assertFalse(self.root.tk.call('after', 'info'))

    def test_destroyed_widget_cannot_leave_a_callback_running(self):
        label = tk.Label(self.root)
        self.motion.run('label', lambda value: label.configure(text=str(value)), duration=80)
        label.destroy()
        self.pump(lambda: self.motion.active_count == 0)
        self.assertFalse(self.root.tk.call('after', 'info'))

    def test_dialog_fade_restores_opacity_on_cancel_reduce_and_early_close(self):
        dialog = tk.Toplevel(self.root)
        self.assertTrue(self.motion.fade_in(dialog))
        self.assertGreaterEqual(float(dialog.attributes('-alpha')), .94)
        self.motion.set_reduced(True)
        self.assertAlmostEqual(float(dialog.attributes('-alpha')), 1.0)
        self.assertEqual(self.motion.active_count, 0)
        self.motion.set_reduced(False)
        self.motion.fade_in(dialog)
        self.motion.cancel(('window-fade', str(dialog)), finish=False)
        self.assertAlmostEqual(float(dialog.attributes('-alpha')), 1.0)
        self.motion.fade_in(dialog)
        dialog.destroy()
        self.root.update()
        self.assertEqual(self.motion.active_count, 0)
        self.assertFalse(self.root.tk.call('after', 'info'))

    def test_root_destroy_closes_scheduler_without_explicit_shutdown(self):
        samples = []
        self.motion.run('effect', samples.append)
        self.root.destroy()
        self.assertTrue(self.motion.closed)
        self.assertEqual(self.motion.active_count, 0)
        self.assertEqual(samples, [0.0])

    def test_final_frame_can_close_app_without_running_completion_afterwards(self):
        done = []

        def update(progress):
            if progress == 1:
                self.motion.close()

        self.motion.run('last-frame', update, duration=0, finish=lambda: done.append(True))
        self.assertTrue(self.motion.closed)
        self.assertEqual(done, [])

    def test_completion_can_start_a_new_effect_with_the_same_key(self):
        next_samples = []
        self.motion.run('feedback', lambda _: None, duration=0,
                        finish=lambda: self.motion.run('feedback', next_samples.append, duration=50))
        self.assertEqual(self.motion.active_count, 1)
        self.pump(lambda: self.motion.active_count == 0)
        self.assertEqual(next_samples[-1], 1.0)


if __name__ == '__main__':
    unittest.main()

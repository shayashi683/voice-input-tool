import unittest

import numpy as np

from voice_input_tool.audio_pipeline import AudioActivityTracker


class MutableClock:
    def __init__(self):
        self.now = 1.0

    def __call__(self):
        return self.now


class AudioActivityTrackerTests(unittest.TestCase):
    def test_speech_activation_and_release(self):
        clock = MutableClock()
        tracker = AudioActivityTracker(clock=clock)

        self.assertEqual(tracker.status_for_samples(np.ones(32) * 0.1, "listening"), "hearing")
        clock.now += 0.4
        self.assertEqual(tracker.status_for_samples(np.zeros(32), "hearing"), "listening")

    def test_busy_status_is_not_overwritten(self):
        tracker = AudioActivityTracker(clock=lambda: 1.0)
        self.assertIsNone(tracker.status_for_samples(np.ones(32), "processing"))


if __name__ == "__main__":
    unittest.main()


class AudioStatusDispatcherTests(unittest.TestCase):
    def test_slow_display_does_not_block_capture_and_keeps_latest_sample(self):
        import threading
        from voice_input_tool.audio_pipeline import AudioStatusDispatcher
        entered, release, latest = threading.Event(), threading.Event(), threading.Event()
        observed = []
        def on_samples(samples):
            observed.append(samples[0])
            if len(observed) == 1:
                entered.set()
                release.wait(2)
            if samples[0] == 99:
                latest.set()
        dispatcher = AudioStatusDispatcher(lambda _: None, on_samples)
        try:
            dispatcher.samples([0])
            self.assertTrue(entered.wait(2))
            # These calls finish while the observer is blocked.
            for value in range(1, 100):
                dispatcher.samples([value])
            release.set()
            self.assertTrue(latest.wait(2))
            self.assertEqual(observed, [0, 99])
        finally:
            release.set()
            dispatcher.close()

    def test_failing_display_does_not_kill_dispatcher(self):
        import threading
        from voice_input_tool.audio_pipeline import AudioStatusDispatcher
        finished = threading.Event()
        def broken(_):
            raise RuntimeError('display failed')
        dispatcher = AudioStatusDispatcher(broken, lambda _: finished.set())
        try:
            dispatcher.started(0.1)
            dispatcher.samples([1])
            self.assertTrue(finished.wait(2))
        finally:
            dispatcher.close()

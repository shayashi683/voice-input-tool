import threading
import unittest

from voice_input_tool.audio_monitor import AudioDeviceMonitor


class AudioDeviceMonitorTests(unittest.TestCase):
    def test_monitor_retries_after_failure_and_stops(self):
        calls = []
        recovered = threading.Event()

        def maintain():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("device not ready")
            recovered.set()

        monitor = AudioDeviceMonitor(maintain, interval=0.005)
        monitor.start()
        try:
            self.assertTrue(recovered.wait(1))
        finally:
            monitor.close()
        self.assertFalse(monitor._thread.is_alive())

    def test_close_before_first_tick_does_not_open_microphone(self):
        called = threading.Event()
        monitor = AudioDeviceMonitor(called.set, interval=10)
        monitor.start()
        monitor.close()
        self.assertFalse(called.is_set())

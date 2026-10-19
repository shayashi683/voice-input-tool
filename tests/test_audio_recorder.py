import unittest
from unittest.mock import Mock

import numpy as np

from voice_input_tool.audio_recorder import BufferedAudioRecorder


class FakeStream:
    def __init__(self, callback, **_kwargs):
        self.callback = callback
        self.device = _kwargs.get("device")
        self.started = False
        self.closed = False

    @property
    def active(self):
        return self.started

    @active.setter
    def active(self, value):
        self.started = value

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def abort(self):
        self.stop()

    def close(self):
        self.closed = True

    def emit(self, values):
        samples = np.asarray(values, dtype=np.float32).reshape(-1, 1)
        self.callback(samples, len(samples), None, None)


class BufferedAudioRecorderTests(unittest.TestCase):
    def test_unchanged_healthy_stream_keeps_preroll(self):
        recorder, streams, _, _ = self.make_recorder()
        recorder._topology_query = lambda: ((1, 2), (2,))
        recorder.ensure_stream("name:Shure MV7+")
        streams[0].emit([1, 2])
        for _ in range(3):
            self.assertTrue(recorder.ensure_stream("name:Shure MV7+"))
        recorder.start("name:Shure MV7+")
        self.assertEqual(recorder.stop(0).pre_roll_samples, 2)
        self.assertEqual(len(streams), 1)

    def test_topology_change_reselects_preferred_mic_and_preserves_recording(self):
        recorder, streams, _, _ = self.make_recorder()
        topology = [((1, 2), (2,))]
        recorder._topology_query = lambda: topology[0]
        devices = iter([2, 0, 5])

        def resolve(selection):
            self.assertEqual(selection, "name:Shure MV7+")
            self.assertTrue(all(stream.closed for stream in streams))
            return next(devices)

        recorder._device_resolver = resolve
        sink = []
        recorder.start("name:Shure MV7+", audio_sink=sink)
        streams[0].emit([1, 2])
        # USB unplug: fall back to built-in input even if .active remains true.
        topology[0] = ((1,), (1,))
        self.assertTrue(recorder.ensure_stream("name:Shure MV7+"))
        streams[0].emit([99])  # Late callback from removed microphone.
        streams[1].emit([3, 4])
        # USB replug: the same mic is now at a different PortAudio index.
        topology[0] = ((1, 8), (1,))
        self.assertTrue(recorder.ensure_stream("name:Shure MV7+"))
        streams[1].emit([99])
        streams[2].emit([5, 6])
        self.assertTrue(recorder.is_recording)
        result = recorder.stop(0)
        self.assertEqual([stream.device for stream in streams], [2, 0, 5])
        np.testing.assert_array_equal(np.concatenate(result.chunks), [1, 2, 3, 4, 5, 6])
        np.testing.assert_array_equal(np.concatenate(sink), [1, 2, 3, 4, 5, 6])

    def test_callback_stall_reopens_stream_that_still_reports_active(self):
        recorder, streams, _, _ = self.make_recorder()
        now = [10.0]
        recorder._clock = lambda: now[0]
        recorder.ensure_stream()
        now[0] = 12.0
        streams[0].emit([1])
        now[0] = 14.0
        recorder.ensure_stream()
        self.assertEqual(len(streams), 1)
        now[0] = 16.0
        recorder.ensure_stream()
        self.assertEqual(len(streams), 2)
        self.assertTrue(streams[0].closed)

    def test_disconnect_without_fallback_retries_without_discarding_audio(self):
        recorder, streams, _, _ = self.make_recorder()
        recorder._device_resolver = Mock(side_effect=[2, None, 5])
        recorder.start()
        streams[0].emit([1, 2])
        streams[0].active = False
        self.assertFalse(recorder.ensure_stream())
        self.assertTrue(recorder.is_recording)
        self.assertTrue(recorder.ensure_stream())
        streams[1].emit([3, 4])
        np.testing.assert_array_equal(np.concatenate(recorder.stop(0).chunks), [1, 2, 3, 4])

    def test_failed_reconnect_can_retry_during_recording(self):
        recorder, streams, _, _ = self.make_recorder()
        recorder.start()
        streams[0].emit([1, 2])
        streams[0].active = False
        factory = recorder._stream_factory
        recorder._stream_factory = Mock(side_effect=RuntimeError("device initializing"))
        self.assertFalse(recorder.ensure_stream())
        recorder._stream_factory = factory
        self.assertTrue(recorder.ensure_stream())
        streams[1].emit([3])
        np.testing.assert_array_equal(np.concatenate(recorder.stop(0).chunks), [1, 2, 3])

    def test_status_callback_failure_does_not_stop_audio_capture(self):
        recorder, streams, _, _ = self.make_recorder()
        recorder._on_audio_started = Mock(side_effect=RuntimeError("UI failed"))
        recorder._on_live_samples = Mock(side_effect=RuntimeError("UI failed"))
        recorder.start()
        streams[0].emit([1, 2])
        streams[0].emit([3, 4])
        result = recorder.stop(0)
        np.testing.assert_array_equal(np.concatenate(result.chunks), [1, 2, 3, 4])

    def test_close_cannot_discard_active_recording(self):
        recorder, streams, _, _ = self.make_recorder()
        recorder.start()
        streams[0].emit([1, 2])
        with self.assertRaises(RuntimeError):
            recorder.close()
        self.assertTrue(recorder.is_recording)
        self.assertEqual(recorder.stop(0).sample_count, 2)

    def test_dead_stream_is_reopened_before_start(self):
        recorder, streams, _, _ = self.make_recorder()
        recorder.ensure_stream()
        streams[0].active = False
        recorder.start()
        self.assertEqual(len(streams), 2)
        self.assertTrue(streams[0].closed)
        recorder.stop(0)

    def make_recorder(self):
        streams = []
        starts = []
        live_samples = []

        def stream_factory(**kwargs):
            stream = FakeStream(**kwargs)
            streams.append(stream)
            return stream

        def sleeper(_seconds):
            streams[0].emit([4.0, 4.0])

        recorder = BufferedAudioRecorder(
            on_audio_started=starts.append,
            on_live_samples=lambda samples: live_samples.append(samples.copy()),
            stream_factory=stream_factory,
            device_resolver=lambda _device_id: 2,
            device_query=lambda _device_id: {"name": "Test Mic"},
            topology_query=lambda: None,
            sleeper=sleeper,
            clock=lambda: 10.0,
        )
        return recorder, streams, starts, live_samples

    def test_preserves_pre_roll_and_post_roll(self):
        recorder, streams, starts, live_samples = self.make_recorder()
        self.assertTrue(recorder.ensure_stream("2"))
        streams[0].emit([1.0, 1.0])
        streams[0].emit([2.0, 2.0])

        self.assertTrue(recorder.start("2"))
        streams[0].emit([3.0, 3.0])
        recording = recorder.stop(post_roll_seconds=0.5)

        self.assertEqual(recording.pre_roll_samples, 4)
        np.testing.assert_array_equal(
            np.concatenate(recording.chunks),
            np.asarray([1, 1, 2, 2, 3, 3, 4, 4], dtype=np.float32),
        )
        self.assertEqual(len(starts), 1)
        self.assertEqual(len(live_samples), 1)

    def test_previous_recording_is_not_reused_as_next_pre_roll(self):
        recorder, streams, _starts, _live_samples = self.make_recorder()
        recorder.ensure_stream("")
        streams[0].emit([1.0])
        recorder.start("")
        streams[0].emit([2.0])
        recorder.stop(post_roll_seconds=0)

        streams[0].emit([9.0])
        recorder.start("")
        streams[0].emit([8.0])
        recording = recorder.stop(post_roll_seconds=0)

        np.testing.assert_array_equal(
            np.concatenate(recording.chunks),
            np.asarray([9, 8], dtype=np.float32),
        )

    def test_close_reselects_the_current_input_device_on_next_start(self):
        streams = []
        available_devices = iter((2, 5))

        def stream_factory(**kwargs):
            stream = FakeStream(**kwargs)
            streams.append(stream)
            return stream

        recorder = BufferedAudioRecorder(
            stream_factory=stream_factory,
            device_resolver=lambda _device_id: next(available_devices),
            device_query=lambda device_id: {"name": f"Mic {device_id}"},
            topology_query=lambda: None,
        )

        self.assertTrue(recorder.start(""))
        recorder.stop(post_roll_seconds=0)
        recorder.close()
        self.assertTrue(recorder.start(""))

        self.assertEqual([stream.device for stream in streams], [2, 5])
        self.assertTrue(streams[0].closed)
        self.assertTrue(streams[1].started)


if __name__ == "__main__":
    unittest.main()

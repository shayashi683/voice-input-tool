import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import numpy as np

from voice_input_tool.audio_recorder import BufferedAudioRecorder
from voice_input_tool.audio_pipeline import AudioActivityTracker
from voice_input_tool.engine import VoiceInputApp
from voice_input_tool.recording_store import RecordingStore
from voice_input_tool.recording_processor import RecordingProcessor
from voice_input_tool.transcription import TranscriptionService
from tests.test_audio_recorder import FakeStream


class VoiceInputAppTests(unittest.TestCase):
    def make_app(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        app = VoiceInputApp.__new__(VoiceInputApp)
        app._control_lock = threading.RLock()
        app._shutting_down = False
        app._session = None
        app._unsaved_recordings = []
        app._store = RecordingStore(directory.name)
        app._recorder = BufferedAudioRecorder(stream_factory=FakeStream,
                                              device_resolver=lambda _: 0,
                                              device_query=lambda _: {}, sleeper=lambda _: None,
                                              topology_query=lambda: None)
        app._processor = Mock()
        app._processor.busy = False
        app._status_controller = Mock()
        app._audio_activity = AudioActivityTracker()
        app._audio_status = Mock()
        app._audio_monitor = Mock()
        app.config = {}
        app.use_llm = False
        app._hotkey_listener = None
        self.addCleanup(app.shutdown)
        return app

    def test_next_recording_is_accepted_while_previous_is_processing(self):
        app = self.make_app()
        app.start_recording(target_pid=42)
        stream = app._recorder._stream
        stream.emit([1, 2])
        app.stop_recording()
        first = app._store.list()[0]['id']
        app._processor.busy = True
        app.start_recording(target_pid=43)
        self.assertTrue(app.is_recording)
        stream.emit([3, 4])
        app.stop_recording()
        second = app._store.list()[1]['id']
        self.assertIs(app._recorder._stream, stream)
        np.testing.assert_array_equal(app._store.load_audio(first), [1, 2])
        np.testing.assert_array_equal(app._store.load_audio(second), [3, 4])
        self.assertEqual(app._store.read(first)['target_pid'], 42)
        self.assertEqual(app._store.read(second)['target_pid'], 43)
        self.assertEqual(app._processor.submit.call_count, 2)

    def test_model_change_during_recording_applies_only_to_next_recording(self):
        app = self.make_app()
        app.config = {"asr_backend": "reazonspeech"}
        app.start_recording(target_pid=42)
        app._recorder._stream.emit([0.1, 0.2])
        app.config = {"asr_backend": "gpt-transcribe"}
        app.stop_recording()
        first = app._store.list()[0]

        app.start_recording(target_pid=42)
        app._recorder._stream.emit([0.3, 0.4])
        app.stop_recording()
        second = app._store.list()[1]
        self.assertEqual(first["asr_backend"], "reazonspeech")
        self.assertEqual(second["asr_backend"], "gpt-transcribe")

        cloud = Mock()
        cloud.transcribe.return_value = "次の録音"
        local_recognizer = Mock(return_value="最初の録音")
        service = TranscriptionService(
            app.config, recognizer=object(), speech_recognizer=local_recognizer,
            cloud_transcriber=cloud,
        )
        worker = RecordingProcessor(app._store, service, None,
                                    on_status=Mock(), on_finished=Mock(), notify=Mock())
        worker.process(first["id"], deliver=False)
        self.assertEqual(app._store.read(first["id"])["text"], "最初の録音")
        cloud.transcribe.assert_not_called()
        worker.process(second["id"], deliver=False)
        self.assertEqual(app._store.read(second["id"])["text"], "次の録音")
        local_recognizer.assert_called_once()
        cloud.transcribe.assert_called_once()

    def test_correction_opt_in_is_saved_when_recording_begins(self):
        app = self.make_app()
        app.use_llm = True
        app.start_recording(target_pid=42)
        app._recorder._stream.emit([0.1, 0.2])
        app.use_llm = False
        app.stop_recording()
        first = app._store.list()[0]
        app.start_recording(target_pid=42)
        app._recorder._stream.emit([0.3, 0.4])
        app.stop_recording()
        second = app._store.list()[1]
        self.assertTrue(first["use_llm"])
        self.assertEqual(first["correction_provider"], "openai")
        self.assertFalse(second["use_llm"])
        self.assertIsNone(second["correction_provider"])

    def test_shutdown_durably_saves_active_recording_before_closing(self):
        app = self.make_app()
        app.start_recording(target_pid=42)
        app._recorder._stream.emit([1, 2, 3])
        app.shutdown()
        record = app._store.list()[0]
        np.testing.assert_array_equal(app._store.load_audio(record['id']), [1, 2, 3])
        app._processor.shutdown.assert_called_once()
        self.assertFalse(app._recorder.stream_ready)

    def test_journal_failure_rewrites_complete_recording_from_memory(self):
        app = self.make_app()
        app.start_recording(target_pid=42)
        app._recorder._stream.emit([1, 2, 3])
        journal = app._session[1]
        original_finish = journal.finish
        def fail():
            original_finish()
            raise OSError('disk write failed')
        journal.finish = fail
        app.stop_recording()
        record = app._store.list()[0]
        np.testing.assert_array_equal(app._store.load_audio(record['id']), [1, 2, 3])
        app._processor.submit.assert_called_once()

    @patch('voice_input_tool.engine.notify_user')
    def test_storage_failure_keeps_memory_copy_for_shutdown_retry(self, notify):
        app = self.make_app()
        app.start_recording(target_pid=42)
        app._recorder._stream.emit([1, 2, 3])
        with patch.object(app._store, 'update', side_effect=OSError('disk full')):
            app.stop_recording()
        self.assertEqual(len(app._unsaved_recordings), 1)
        app.shutdown()
        self.assertEqual(app._unsaved_recordings, [])
        self.assertEqual(app._store.list()[0]['state'], 'queued')

    def test_prepare_audio_stream_uses_configured_input(self):
        app = self.make_app()
        app._recorder = Mock(is_recording=False, is_stopping=False)
        app._recorder.ensure_stream.return_value = True
        app.config = {'input_device_id': '7'}
        self.assertTrue(app.prepare_audio_stream())
        app._recorder.ensure_stream.assert_called_once_with('7')

    def test_monitor_recovers_during_recording_without_losing_journal(self):
        app = self.make_app()
        app.start_recording(target_pid=42)
        stream = app._recorder._stream
        stream.emit([1, 2])
        stream.active = False
        app._maintain_audio_stream()
        self.assertTrue(app.is_recording)
        self.assertIsNot(app._recorder._stream, stream)
        app._recorder._stream.emit([3, 4])
        app.stop_recording()
        record = app._store.list()[0]
        np.testing.assert_array_equal(app._store.load_audio(record['id']), [1, 2, 3, 4])

    def test_monitor_does_not_reopen_after_shutdown(self):
        app = self.make_app()
        app.shutdown()
        with patch.object(app._recorder, 'ensure_stream') as ensure:
            app._maintain_audio_stream()
        ensure.assert_not_called()
        app._audio_monitor.close.assert_called_once()

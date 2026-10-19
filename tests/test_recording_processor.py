import json
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

from voice_input_tool.recording_store import RecordingStore
from voice_input_tool.recording_processor import RecordingProcessor
from voice_input_tool.text_delivery import DeliveryResult
from voice_input_tool.transcription import TranscriptionService
from voice_input_tool.config import normalize_config


class RecordingProcessorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = RecordingStore(self.temp.name)
        self.transcription = Mock()
        self.transcription.transcribe.return_value = SimpleNamespace(text='テスト原文')
        self.transcription.correct.return_value = 'テスト原文。'
        self.delivery = Mock()
        self.delivery.deliver.return_value = DeliveryResult('paste_sent')
        self.worker = RecordingProcessor(self.store, self.transcription, self.delivery,
                                         on_status=Mock(), on_finished=Mock(), notify=Mock())
        self.addCleanup(self.worker.shutdown)

    def recording(self, use_llm=False, asr_backend="reazonspeech"):
        recording_id = self.store.create(use_llm=use_llm, target_pid=42, asr_backend=asr_backend,
                                         correction_provider="openai" if use_llm else None)
        self.store.save_audio(recording_id, (np.ones(1600, dtype=np.float32),))
        return recording_id

    def test_raw_and_corrected_text_are_durable_before_delivery(self):
        recording_id = self.recording(True)
        def deliver(*args):
            record = RecordingStore(self.temp.name).read(recording_id)
            self.assertEqual(record['raw_text'], 'テスト原文')
            self.assertEqual(record['text'], 'テスト原文。')
            self.assertEqual(record['state'], 'delivering')
            return DeliveryResult('paste_sent')
        self.delivery.deliver.side_effect = deliver
        self.worker.process(recording_id, 'target', True)
        self.assertEqual(self.store.read(recording_id)['state'], 'paste_sent')

    def test_asr_failure_keeps_audio_and_can_retry(self):
        recording_id = self.recording()
        self.transcription.transcribe.side_effect = RuntimeError('model failed')
        self.worker.process(recording_id, deliver=True)
        self.assertEqual(self.store.read(recording_id)['state'], 'failed')
        self.assertEqual(len(self.store.load_audio(recording_id)), 1600)
        self.delivery.deliver.assert_not_called()
        self.transcription.transcribe.side_effect = None
        self.worker.process(recording_id)
        self.assertEqual(self.store.read(recording_id)['text'], 'テスト原文')

    def test_backend_is_saved_with_the_transcript(self):
        recording_id = self.recording(asr_backend="gpt-transcribe")
        self.transcription.transcribe.return_value = SimpleNamespace(
            text="クラウドの認識結果。", backend="gpt-transcribe")
        self.worker.process(recording_id)
        record = self.store.read(recording_id)
        self.assertEqual(record["asr_backend"], "gpt-transcribe")
        self.assertEqual(record["text"], "クラウドの認識結果。")
        self.assertEqual(self.transcription.transcribe.call_args.kwargs["backend"], "gpt-transcribe")

    def test_empty_asr_is_explicit_failure_with_audio_retained(self):
        recording_id = self.recording()
        self.transcription.transcribe.return_value.text = ''
        self.worker.process(recording_id, deliver=True)
        self.assertEqual(self.store.read(recording_id)['state'], 'failed')
        self.assertTrue(self.store.read(recording_id)['error'])
        self.delivery.deliver.assert_not_called()

    def test_empty_or_failed_correction_falls_back_to_raw_text(self):
        for error in (None, RuntimeError('timeout')):
            with self.subTest(error=error):
                recording_id = self.recording(True)
                self.transcription.correct.side_effect = error
                self.transcription.correct.return_value = ''
                self.worker.process(recording_id, deliver=True)
                self.assertEqual(self.store.read(recording_id)['text'], 'テスト原文')

    def test_delivery_failure_keeps_text(self):
        recording_id = self.recording()
        self.delivery.deliver.side_effect = OSError('clipboard failed')
        self.worker.process(recording_id, deliver=True)
        self.assertEqual(self.store.read(recording_id)['state'], 'failed')
        self.assertEqual(self.store.read(recording_id)['text'], 'テスト原文')

    def test_shutdown_during_asr_saves_result_without_pasting(self):
        recording_id = self.recording()
        entered = threading.Event()
        release = threading.Event()
        def transcribe(_, *, backend=None):
            entered.set()
            self.assertTrue(release.wait(3))
            return SimpleNamespace(text='終了前の音声')
        self.transcription.transcribe.side_effect = transcribe
        # Start first so startup recovery doesn't also enqueue this live job.
        self.store.update(recording_id, state='failed')
        self.worker.start()
        self.worker.submit(recording_id)
        self.assertTrue(entered.wait(3))
        self.worker.shutdown(timeout=0)
        release.set()
        self.worker._thread.join(3)
        self.assertFalse(self.worker._thread.is_alive())
        self.assertEqual(self.store.read(recording_id)['text'], '終了前の音声')
        self.delivery.deliver.assert_not_called()

    def test_worker_continues_after_failure_and_processes_in_order(self):
        self.worker.start()
        self.transcription.transcribe.side_effect = [RuntimeError('first failed'), SimpleNamespace(text='二番目')]
        ids = [self.recording(), self.recording()]
        for recording_id in ids:
            self.worker.submit(recording_id)
        self.worker._queue.join()
        self.assertEqual(self.store.read(ids[0])['state'], 'failed')
        self.assertEqual(self.store.read(ids[1])['state'], 'paste_sent')
        self.delivery.deliver.assert_called_once_with('二番目', None, 42)

    def test_restart_recognizes_interrupted_audio_but_does_not_paste(self):
        recording_id = self.recording()
        self.worker.start()
        self.worker._queue.join()
        self.assertEqual(self.store.read(recording_id)['state'], 'recovered')
        self.delivery.deliver.assert_not_called()

    def test_legacy_recovery_stays_local_when_current_setting_is_cloud(self):
        recording_id = self.recording(use_llm=True)
        legacy = self.store.read(recording_id)
        del legacy["asr_backend"]
        del legacy["correction_provider"]
        (self.store.directory(recording_id) / "recording.json").write_text(json.dumps(legacy))
        cloud_factory = Mock(side_effect=AssertionError("legacy audio must not be uploaded"))
        local_recognizer = Mock(return_value="過去のローカル録音")
        corrector = Mock()
        corrector.has_api_key.return_value = True
        config = normalize_config({
            "asr_backend": "gpt-transcribe", "openai_api_key": "new-openai-key",
            "use_llm": True, "llm_model": "openai/gpt-oss-120b",
        })
        self.assertFalse(config["use_llm"])
        self.worker.transcription = TranscriptionService(
            config,
            recognizer=object(),
            speech_recognizer=local_recognizer,
            cloud_transcriber_factory=cloud_factory,
            corrector=corrector,
        )
        self.worker.start()
        self.worker._queue.join()
        recovered = self.store.read(recording_id)
        self.assertEqual(recovered["text"], "過去のローカル録音")
        self.assertEqual(recovered["asr_backend"], "reazonspeech")
        self.assertEqual(recovered["state"], "recovered")
        local_recognizer.assert_called_once()
        cloud_factory.assert_not_called()
        corrector.correct.assert_not_called()
        self.delivery.deliver.assert_not_called()

    def test_openai_correction_opt_in_survives_restart(self):
        recording_id = self.recording(use_llm=True)
        self.worker.start()
        self.worker._queue.join()
        self.assertEqual(self.store.read(recording_id)["text"], "テスト原文。")
        self.transcription.correct.assert_called_once_with("テスト原文")
        self.delivery.deliver.assert_not_called()

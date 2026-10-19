from contextlib import redirect_stdout
import io
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from voice_input_tool.recording_history import run_history_command
from voice_input_tool.recording_store import RecordingStore
from voice_input_tool.transcription import TranscriptionService


class RecordingHistoryTests(unittest.TestCase):
    def test_explicit_retry_uses_selected_cloud_backend_and_preserves_local_source(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RecordingStore(directory)
            source_id = store.create(asr_backend="reazonspeech")
            store.save_audio(source_id, (np.ones(1600, dtype=np.float32),))
            store.update(source_id, state="recovered", raw_text="元の結果", text="元の結果")
            source = store.read(source_id)
            cloud = Mock()
            cloud.transcribe.return_value = "クラウドで再認識"
            local_factory = Mock(side_effect=AssertionError("explicit cloud retry must not load local model"))
            config = {"asr_backend": "gpt-transcribe"}
            service = TranscriptionService(config, cloud_transcriber=cloud, recognizer_factory=local_factory)
            args = SimpleNamespace(history=False, show_recording=None, retry_recording=source_id)

            with patch("voice_input_tool.recording_history.RecordingStore", return_value=store), \
                 patch("voice_input_tool.transcription.TranscriptionService", return_value=service), \
                 redirect_stdout(io.StringIO()):
                status = run_history_command(args, config, use_llm=False)

            self.assertEqual(status, 0)
            self.assertEqual(store.read(source_id), source)
            retries = [record for record in store.list() if record["id"] != source_id]
            self.assertEqual(len(retries), 1)
            self.assertEqual(retries[0]["source_recording_id"], source_id)
            self.assertEqual(retries[0]["asr_backend"], "gpt-transcribe")
            self.assertIsNone(retries[0]["correction_provider"])
            self.assertEqual(retries[0]["state"], "recovered")
            self.assertEqual(retries[0]["text"], "クラウドで再認識")
            cloud.transcribe.assert_called_once()
            local_factory.assert_not_called()

    def test_explicit_retry_can_opt_legacy_recording_into_openai_correction(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RecordingStore(directory)
            source_id = store.create(use_llm=True)
            store.save_audio(source_id, (np.ones(1600, dtype=np.float32),))
            source = store.read(source_id)
            corrector = Mock()
            corrector.has_api_key.return_value = True
            corrector.correct.return_value = "再認識しました。"
            config = {"asr_backend": "reazonspeech", "use_llm": True}
            service = TranscriptionService(
                config, recognizer=object(), speech_recognizer=lambda *_: "再認識しました",
                corrector=corrector,
            )
            args = SimpleNamespace(history=False, show_recording=None, retry_recording=source_id)
            with patch("voice_input_tool.recording_history.RecordingStore", return_value=store), \
                 patch("voice_input_tool.transcription.TranscriptionService", return_value=service), \
                 redirect_stdout(io.StringIO()):
                status = run_history_command(args, config, use_llm=True)
            self.assertEqual(status, 0)
            self.assertEqual(store.read(source_id), source)
            retry = next(record for record in store.list() if record["id"] != source_id)
            self.assertEqual(retry["correction_provider"], "openai")
            self.assertEqual(retry["text"], "再認識しました。")
            corrector.correct.assert_called_once_with("再認識しました")


if __name__ == "__main__":
    unittest.main()

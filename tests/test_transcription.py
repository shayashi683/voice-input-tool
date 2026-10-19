import unittest
from unittest.mock import Mock

import numpy as np

from voice_input_tool.transcription import TranscriptionService


class FakeCorrector:
    def __init__(self):
        self.config = None

    def configure(self, config):
        self.config = config

    def has_api_key(self):
        return True

    def correct(self, text):
        return text + "。"


class TranscriptionServiceTests(unittest.TestCase):
    def test_cloud_backend_does_not_load_local_model(self):
        cloud = Mock()
        cloud.transcribe.return_value = "日本語の認識結果。"
        local_factory = Mock(side_effect=AssertionError("local model loaded"))
        service = TranscriptionService(
            {"asr_backend": "gpt-transcribe", "asr_keywords": ["SQL"]},
            cloud_transcriber=cloud,
            recognizer_factory=local_factory,
            corrector=FakeCorrector(),
        )
        samples = np.ones(1600, dtype=np.float32)
        result = service.transcribe(samples)
        self.assertEqual(result.text, "日本語の認識結果。")
        self.assertEqual(result.backend, "gpt-transcribe")
        self.assertIs(cloud.transcribe.call_args.args[0], samples)
        local_factory.assert_not_called()

    def test_cloud_failure_propagates_without_silent_local_fallback(self):
        cloud = Mock()
        cloud.transcribe.side_effect = RuntimeError("API unavailable")
        local_factory = Mock()
        service = TranscriptionService(
            {"asr_backend": "gpt-transcribe"}, cloud_transcriber=cloud,
            recognizer_factory=local_factory, corrector=FakeCorrector(),
        )
        with self.assertRaisesRegex(RuntimeError, "API unavailable"):
            service.transcribe(np.ones(1600, dtype=np.float32))
        local_factory.assert_not_called()

    def test_config_change_switches_backend_and_updates_cloud_hints(self):
        cloud = Mock()
        cloud.transcribe.return_value = "クラウド"
        cloud_factory = Mock(return_value=cloud)
        service = TranscriptionService(
            {}, recognizer=object(), speech_recognizer=lambda *_: "ローカル",
            cloud_transcriber_factory=cloud_factory, corrector=FakeCorrector(),
        )
        samples = np.ones(1600, dtype=np.float32)
        self.assertEqual(service.transcribe(samples).text, "ローカル")
        cloud_factory.assert_not_called()
        service.configure({"asr_backend": "gpt-transcribe", "asr_keywords": ["SQL"]})
        self.assertEqual(service.transcribe(samples).text, "クラウド")
        cloud_factory.assert_called_once()
        service.configure({"asr_backend": "gpt-transcribe", "asr_keywords": ["MQL"]})
        service.transcribe(samples)
        self.assertEqual(cloud.configure.call_args.args[0]["asr_keywords"], ["MQL"])
        service.configure({"asr_backend": "reazonspeech"})
        self.assertEqual(service.transcribe(samples).text, "ローカル")

    def test_recognizer_is_loaded_once(self):
        created = []
        recognizer = object()

        def factory():
            created.append(True)
            return recognizer

        service = TranscriptionService(
            {},
            recognizer_factory=factory,
            speech_recognizer=lambda value, _samples: "結果" if value is recognizer else "",
            corrector=FakeCorrector(),
            clock=lambda: 1.0,
        )
        first = service.transcribe(np.zeros(10, dtype=np.float32))
        second = service.transcribe(np.zeros(10, dtype=np.float32))

        self.assertEqual(first.text, "結果")
        self.assertEqual(second.text, "結果")
        self.assertEqual(len(created), 1)
        self.assertEqual(service.correct("結果"), "結果。")

    def test_per_recording_backend_override_keeps_current_cloud_credentials_and_hints(self):
        cloud = Mock()
        cloud.transcribe.return_value = "クラウド"
        cloud_factory = Mock(return_value=cloud)
        config = {"asr_backend": "reazonspeech", "openai_api_key": "current-key", "asr_keywords": ["SQL"]}
        service = TranscriptionService(
            config, recognizer=object(), speech_recognizer=lambda *_: "ローカル",
            cloud_transcriber_factory=cloud_factory, corrector=FakeCorrector(),
        )
        samples = np.ones(16, dtype=np.float32)
        result = service.transcribe(samples, backend="gpt-transcribe")
        self.assertEqual((result.text, result.backend), ("クラウド", "gpt-transcribe"))
        cloud_factory.assert_called_once_with(config)
        # Overriding one recording must not change the selected default.
        self.assertEqual(service.transcribe(samples).backend, "reazonspeech")


if __name__ == "__main__":
    unittest.main()

import io
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

import httpx
import numpy as np
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    PermissionDeniedError,
    RateLimitError,
)

from voice_input_tool.gpt_transcription import GPTTranscriber, GPTTranscriptionError


class GPTTranscriptionTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.client = Mock()
        self.client.audio.transcriptions.create.return_value = SimpleNamespace(text=" 日本語の結果。 ")
        self.factory = Mock(return_value=self.client)
        self.config = {"openai_api_key": "test-key"}
        self.transcriber = GPTTranscriber(self.config, client_factory=self.factory)

    def test_preserves_full_recording_as_pcm16_mono_wav(self):
        samples = np.array([-2, -1, -0.5, 0, 0.5, 1, 2], dtype=np.float32)
        text = self.transcriber.transcribe(samples)
        self.assertEqual(text, "日本語の結果。")
        self.factory.assert_called_once_with(
            api_key="test-key", base_url="https://api.openai.com/v1", timeout=60.0, max_retries=1
        )
        self.client.audio.transcriptions.create.assert_called_once()
        request = self.client.audio.transcriptions.create.call_args.kwargs
        self.assertEqual(request["model"], "gpt-transcribe")
        name, payload, content_type = request["file"]
        self.assertEqual((name, content_type), ("recording.wav", "audio/wav"))
        with wave.open(io.BytesIO(payload), "rb") as wav:
            self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()), (1, 2, 16000))
            self.assertEqual(wav.getnframes(), len(samples))
            np.testing.assert_array_equal(
                np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2"),
                [-32768, -32768, -16384, 0, 16384, 32767, 32767],
            )

    def test_sends_context_and_plural_language_hints(self):
        self.transcriber.configure({
            **self.config,
            "asr_prompt": " 営業指標についての会話です。 ",
            "asr_keywords": ["SQL", "MQL", "SQL", " プロジェクトA "],
            "asr_languages": ["ja", "en"],
        })
        self.transcriber.transcribe(np.zeros(8, dtype=np.float32))
        request = self.client.audio.transcriptions.create.call_args.kwargs
        self.assertEqual(request["prompt"], "営業指標についての会話です。")
        self.assertEqual(request["extra_body"], {
            "keywords": ["SQL", "MQL", "プロジェクトA"], "languages": ["ja", "en"]
        })
        self.assertNotIn("language", request)
        self.assertNotIn("language", request["extra_body"])

    def test_default_languages_and_empty_optional_prompt(self):
        self.transcriber.transcribe(np.zeros(8))
        request = self.client.audio.transcriptions.create.call_args.kwargs
        self.assertEqual(request["extra_body"], {"languages": ["ja", "en"]})
        self.assertNotIn("prompt", request)

    def test_missing_key_has_actionable_error_without_request(self):
        self.transcriber.configure({})
        self.assertFalse(self.transcriber.has_api_key())
        with self.assertRaisesRegex(GPTTranscriptionError, "OpenAI APIキーが未設定"):
            self.transcriber.transcribe(np.zeros(8))
        self.factory.assert_not_called()

    def test_environment_key_is_supported_and_config_key_takes_precedence(self):
        os.environ["OPENAI_API_KEY"] = "environment-key"
        self.transcriber.configure({})
        self.assertTrue(self.transcriber.has_api_key())
        self.transcriber.transcribe(np.zeros(8))
        self.assertEqual(self.factory.call_args.kwargs["api_key"], "environment-key")
        self.transcriber.configure(self.config)
        self.transcriber.transcribe(np.zeros(8))
        self.assertEqual(self.factory.call_args.kwargs["api_key"], "test-key")

    def test_client_is_reused_until_key_changes(self):
        self.transcriber.transcribe(np.zeros(8))
        self.transcriber.configure({**self.config, "asr_prompt": "新しい話題"})
        self.transcriber.transcribe(np.zeros(8))
        self.assertEqual(self.factory.call_count, 1)
        self.transcriber.configure({"openai_api_key": "new-key"})
        self.transcriber.transcribe(np.zeros(8))
        self.assertEqual(self.factory.call_count, 2)
        self.assertEqual(self.factory.call_args.kwargs["api_key"], "new-key")

    def test_empty_recording_does_not_create_client(self):
        self.transcriber.configure({})
        self.assertEqual(self.transcriber.transcribe(np.array([], dtype=np.float32)), "")
        self.factory.assert_not_called()

    def test_invalid_audio_never_reaches_api(self):
        for samples in (
            np.zeros((8, 1)), np.array(0.5), np.array([np.nan]), np.array([np.inf]),
            np.array(["audio"]), np.array([complex(1, 1)]), [[0], [0, 1]],
        ):
            with self.subTest(samples=repr(samples)):
                with self.assertRaises(GPTTranscriptionError):
                    self.transcriber.transcribe(samples)
        self.factory.assert_not_called()

    def test_invalid_sample_rate_never_reaches_api(self):
        for rate in (0, -1, True, 16000.5, "16000", 2**31):
            with self.subTest(rate=rate):
                with self.assertRaisesRegex(GPTTranscriptionError, "サンプルレート"):
                    self.transcriber.transcribe(np.zeros(8), sample_rate=rate)
        self.factory.assert_not_called()

    def test_wav_has_requested_sample_rate(self):
        self.transcriber.transcribe(np.zeros(8), sample_rate=24000)
        payload = self.client.audio.transcriptions.create.call_args.kwargs["file"][1]
        with wave.open(io.BytesIO(payload), "rb") as wav:
            self.assertEqual(wav.getframerate(), 24000)

    def test_float16_positive_peak_does_not_wrap_to_negative_pcm(self):
        self.transcriber.transcribe(np.array([-1, 1], dtype=np.float16))
        payload = self.client.audio.transcriptions.create.call_args.kwargs["file"][1]
        with wave.open(io.BytesIO(payload), "rb") as wav:
            np.testing.assert_array_equal(np.frombuffer(wav.readframes(2), dtype="<i2"), [-32768, 32767])

    def test_wav_size_limit_includes_header_and_never_splits_audio(self):
        with patch("voice_input_tool.gpt_transcription.MAX_AUDIO_BYTES", 50):
            self.transcriber.transcribe(np.zeros(3))
            with self.assertRaisesRegex(GPTTranscriptionError, "元音声.*保存"):
                self.transcriber.transcribe(np.zeros(4))
        self.client.audio.transcriptions.create.assert_called_once()

    def test_disallowed_keyword_characters_are_rejected_before_upload(self):
        for keyword in ("<SQL>", "SQL\nMQL", "SQL\rMQL"):
            with self.subTest(keyword=keyword):
                self.transcriber.configure({**self.config, "asr_keywords": [keyword]})
                with self.assertRaisesRegex(GPTTranscriptionError, "認識用語"):
                    self.transcriber.transcribe(np.zeros(8))
        self.factory.assert_not_called()

    def test_invalid_response_text_is_rejected(self):
        for response in (SimpleNamespace(), SimpleNamespace(text=None), SimpleNamespace(text=12)):
            with self.subTest(response=response):
                self.client.audio.transcriptions.create.return_value = response
                with self.assertRaisesRegex(GPTTranscriptionError, "結果を取得"):
                    self.transcriber.transcribe(np.zeros(8))

    def test_empty_response_is_valid_for_silence(self):
        self.client.audio.transcriptions.create.return_value = SimpleNamespace(text="  ")
        self.assertEqual(self.transcriber.transcribe(np.zeros(8)), "")

    def test_api_failures_are_actionable_and_do_not_expose_response_or_key(self):
        secret = "secret-key-and-private-audio"
        request = httpx.Request("POST", "https://api.openai.com/v1/audio/transcriptions")
        errors = [
            (AuthenticationError(secret, response=httpx.Response(401, request=request), body=None), "APIキーが無効"),
            (PermissionDeniedError(secret, response=httpx.Response(403, request=request), body=None), "権限"),
            (APIStatusError(secret, response=httpx.Response(404, request=request), body=None), "モデル利用権限"),
            (RateLimitError(secret, response=httpx.Response(429, request=request), body=None), "利用上限"),
            (APITimeoutError(request=request), "タイムアウト"),
            (APIConnectionError(message=secret, request=request), "接続できません"),
            (RuntimeError(secret), "文字起こしに失敗"),
        ]
        for error, expected in errors:
            with self.subTest(error=type(error).__name__):
                self.client.audio.transcriptions.create.side_effect = error
                with self.assertRaisesRegex(GPTTranscriptionError, expected) as caught:
                    self.transcriber.transcribe(np.zeros(8))
                self.assertNotIn(secret, str(caught.exception))
                self.assertTrue(caught.exception.__suppress_context__)
                self.assertIsNone(caught.exception.__cause__)

    def test_client_creation_error_does_not_expose_key(self):
        self.factory.side_effect = ValueError("test-key")
        with self.assertRaises(GPTTranscriptionError) as caught:
            self.transcriber.transcribe(np.zeros(8))
        self.assertNotIn("test-key", str(caught.exception))


if __name__ == "__main__":
    unittest.main()

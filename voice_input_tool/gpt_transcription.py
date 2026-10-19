"""OpenAI GPT-Transcribe for complete, in-memory recordings."""

from dataclasses import dataclass
import io
from numbers import Integral
import os
import wave

import numpy as np

try:
    from openai import (
        APIConnectionError,
        APIStatusError,
        APITimeoutError,
        AuthenticationError,
        OpenAI,
        PermissionDeniedError,
        RateLimitError,
    )
except ImportError:
    OpenAI = None
    APIConnectionError = APIStatusError = APITimeoutError = ()
    AuthenticationError = PermissionDeniedError = RateLimitError = ()


MODEL = "gpt-transcribe"
MAX_AUDIO_BYTES = 25_000_000
_WAV_HEADER_BYTES = 44


class GPTTranscriptionError(RuntimeError):
    """A transcription failure safe to display or log without API payloads."""


@dataclass(frozen=True)
class _Settings:
    api_key: str
    prompt: str
    keywords: tuple[str, ...]
    languages: tuple[str, ...]


def _string_list(value, default=()):
    if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
        return tuple(default)
    return tuple(dict.fromkeys(item.strip() for item in value if item.strip()))


def _safe_api_error(error):
    # Never include str(error), response bodies, request headers, or chained
    # exceptions: all can contain the API key, audio, or transcription context.
    if isinstance(error, AuthenticationError):
        return "OpenAI APIキーが無効です。設定のOpenAI APIキーを確認してください。"
    if isinstance(error, PermissionDeniedError):
        return "GPT-Transcribeを利用する権限がありません。OpenAIのプロジェクト設定を確認してください。"
    if isinstance(error, APITimeoutError) or isinstance(error, TimeoutError):
        return "GPT-Transcribeの応答がタイムアウトしました。録音履歴から再試行してください。"
    if isinstance(error, RateLimitError):
        return "OpenAI APIの利用上限に達しました。残高・利用制限を確認し、録音履歴から再試行してください。"
    if isinstance(error, APIStatusError) and error.status_code == 404:
        return "GPT-Transcribeを利用できません。OpenAIのモデル利用権限を確認してください。"
    if isinstance(error, APIConnectionError):
        return "OpenAI APIに接続できません。ネットワークを確認し、録音履歴から再試行してください。"
    return "GPT-Transcribeで文字起こしに失敗しました。録音履歴から再試行してください。"


class GPTTranscriber:
    """Send one PCM WAV recording to the official OpenAI transcription API."""

    def __init__(self, config=None, client_factory=None):
        self._client_factory = client_factory if client_factory is not None else OpenAI
        self._client = None
        self._client_key = None
        self.configure(config or {})

    def configure(self, config):
        key = config.get("openai_api_key", "")
        prompt = config.get("asr_prompt", "")
        # One assignment makes each request use a coherent settings snapshot.
        self._settings = _Settings(
            api_key=key.strip() if isinstance(key, str) else "",
            prompt=prompt.strip() if isinstance(prompt, str) else "",
            keywords=_string_list(config.get("asr_keywords", [])),
            languages=_string_list(config.get("asr_languages", ["ja", "en"]), ("ja", "en")),
        )

    def has_api_key(self):
        return bool(self._settings.api_key or os.environ.get("OPENAI_API_KEY", "").strip())

    def _client_for(self, key):
        if self._client_factory is None:
            raise GPTTranscriptionError("openai パッケージがインストールされていません。")
        if self._client is None or self._client_key != key:
            self._client = self._client_factory(
                api_key=key,
                base_url="https://api.openai.com/v1",
                timeout=60.0,
                max_retries=1,
            )
            self._client_key = key
        return self._client

    def transcribe(self, samples, sample_rate=16000) -> str:
        if isinstance(sample_rate, bool) or not isinstance(sample_rate, Integral) or not 0 < sample_rate <= 2**31 - 1:
            raise GPTTranscriptionError("録音のサンプルレートが不正です。")
        try:
            audio = np.asarray(samples)
        except (TypeError, ValueError):
            raise GPTTranscriptionError("録音音声を読み取れません。") from None
        if audio.ndim != 1 or audio.dtype.kind not in "fiu":
            raise GPTTranscriptionError("録音音声はモノラルの数値配列である必要があります。")
        if audio.size == 0:
            return ""
        if audio.size * 2 + _WAV_HEADER_BYTES > MAX_AUDIO_BYTES:
            raise GPTTranscriptionError(
                "録音がGPT-Transcribeの25MB上限を超えています。"
                "元音声を録音履歴から保存し、短い録音に分けて再試行してください。"
            )
        if not np.isfinite(audio).all():
            raise GPTTranscriptionError("録音音声に不正な数値が含まれています。")

        settings = self._settings
        if any(any(char in keyword for char in "<>\r\n") for keyword in settings.keywords):
            raise GPTTranscriptionError("認識用語に <、>、改行は使用できません。設定の認識用語を確認してください。")
        key = settings.api_key or os.environ.get("OPENAI_API_KEY", "").strip()
        if not key:
            raise GPTTranscriptionError(
                "OpenAI APIキーが未設定です。設定画面でOpenAI APIキーを入力してください"
                "（OPENAI_API_KEY環境変数でも設定できます）。"
            )

        normalized = np.clip(audio, -1.0, 1.0).astype(np.float32, copy=False)
        pcm = np.clip(np.rint(normalized * 32768.0), -32768, 32767).astype("<i2")
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(int(sample_rate))
            wav.writeframes(pcm.tobytes())

        extra_body = {}
        if settings.keywords:
            extra_body["keywords"] = list(settings.keywords)
        if settings.languages:
            extra_body["languages"] = list(settings.languages)
        request = {
            "model": MODEL,
            "file": ("recording.wav", buffer.getvalue(), "audio/wav"),
            "response_format": "json",
        }
        if settings.prompt:
            request["prompt"] = settings.prompt
        if extra_body:
            request["extra_body"] = extra_body

        try:
            response = self._client_for(key).audio.transcriptions.create(**request)
        except GPTTranscriptionError:
            raise
        except Exception as error:
            raise GPTTranscriptionError(_safe_api_error(error)) from None
        text = getattr(response, "text", None)
        if not isinstance(text, str):
            raise GPTTranscriptionError("GPT-Transcribeから文字起こし結果を取得できませんでした。")
        # Silence is allowed to produce empty text; it is not an API failure.
        return text.strip()

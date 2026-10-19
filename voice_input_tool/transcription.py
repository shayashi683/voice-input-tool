"""Select cloud or local ASR, with optional text correction."""

from dataclasses import dataclass
import logging
import threading
import time

from voice_input_tool.llm_correction import LLMCorrector
from voice_input_tool.gpt_transcription import GPTTranscriber
from voice_input_tool.speech_engine import create_recognizer, recognize_speech

log = logging.getLogger("voice_input")


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    elapsed_seconds: float
    backend: str = "reazonspeech"


class TranscriptionService:
    def __init__(
        self,
        config,
        recognizer=None,
        *,
        recognizer_factory=create_recognizer,
        speech_recognizer=recognize_speech,
        corrector=None,
        cloud_transcriber=None,
        cloud_transcriber_factory=GPTTranscriber,
        clock=time.monotonic,
    ):
        self._recognizer = recognizer
        self._recognizer_factory = recognizer_factory
        self._speech_recognizer = speech_recognizer
        self._corrector = corrector or LLMCorrector(config)
        self._cloud_transcriber = cloud_transcriber
        self._cloud_transcriber_factory = cloud_transcriber_factory
        self._config = dict(config)
        self._clock = clock
        self._config_lock = threading.Lock()
        self._recognizer_lock = threading.Lock()
        self._decode_lock = threading.Lock()
        self._correction_lock = threading.Lock()
        log.info("音声認識エンジン: %s", self._config.get("asr_backend", "reazonspeech"))

    def configure(self, config):
        with self._config_lock:
            self._config = dict(config)
        with self._correction_lock:
            self._corrector.configure(config)

    def has_correction_key(self) -> bool:
        return self._corrector.has_api_key()

    def ensure_recognizer(self):
        if self._recognizer is not None:
            return self._recognizer

        with self._recognizer_lock:
            if self._recognizer is None:
                log.info("ASRモデル読み込み開始")
                started_at = self._clock()
                self._recognizer = self._recognizer_factory()
                log.info("ASRモデル読み込み完了 (%.1fs)", self._clock() - started_at)
        return self._recognizer

    def transcribe(self, speech_samples, *, backend=None) -> TranscriptionResult:
        started_at = self._clock()
        with self._decode_lock:
            with self._config_lock:
                config = dict(self._config)
            if backend is None:
                backend = config.get("asr_backend", "reazonspeech")
            if backend == "gpt-transcribe":
                if self._cloud_transcriber is None:
                    self._cloud_transcriber = self._cloud_transcriber_factory(config)
                else:
                    self._cloud_transcriber.configure(config)
                text = self._cloud_transcriber.transcribe(speech_samples)
            elif backend == "reazonspeech":
                text = self._speech_recognizer(self.ensure_recognizer(), speech_samples)
            else:
                raise ValueError("未対応の音声認識エンジンです。設定を確認してください。")
        elapsed = self._clock() - started_at
        log.info("音声認識完了: backend=%s elapsed=%.2fs characters=%d", backend, elapsed, len(text))
        return TranscriptionResult(text=text, elapsed_seconds=elapsed, backend=backend)

    def correct(self, text):
        with self._correction_lock:
            return self._corrector.correct(text)

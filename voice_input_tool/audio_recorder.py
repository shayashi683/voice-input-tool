"""Buffered microphone recording with pre-roll and post-roll protection."""

from collections import deque
from dataclasses import dataclass
import logging
import math
import threading
import time
from typing import Callable, Optional

import numpy as np
import sounddevice as sd

from voice_input_tool.audio_constants import (
    BLOCK_SIZE,
    CHANNELS,
    POST_ROLL_SECONDS,
    PRE_ROLL_SECONDS,
    SAMPLE_RATE,
)
from voice_input_tool.audio_devices import audio_device_topology, resolve_input_device

log = logging.getLogger("voice_input")


@dataclass(frozen=True)
class Recording:
    chunks: tuple[np.ndarray, ...]
    pre_roll_samples: int

    @property
    def sample_count(self) -> int:
        return sum(len(chunk) for chunk in self.chunks)

    @property
    def duration_seconds(self) -> float:
        return self.sample_count / SAMPLE_RATE


class BufferedAudioRecorder:
    """Own the audio stream and protect the beginning/end of each recording."""

    def __init__(
        self,
        on_audio_started: Optional[Callable[[float], None]] = None,
        on_live_samples: Optional[Callable[[np.ndarray], None]] = None,
        *,
        stream_factory=sd.InputStream,
        device_resolver=resolve_input_device,
        device_query=sd.query_devices,
        topology_query=audio_device_topology,
        callback_timeout=3.0,
        sleeper=time.sleep,
        clock=time.monotonic,
    ):
        self._on_audio_started = on_audio_started
        self._on_live_samples = on_live_samples
        self._stream_factory = stream_factory
        self._device_resolver = device_resolver
        self._device_query = device_query
        self._topology_query = topology_query
        self._callback_timeout = callback_timeout
        self._sleeper = sleeper
        self._clock = clock

        max_chunks = max(1, math.ceil(PRE_ROLL_SECONDS * SAMPLE_RATE / BLOCK_SIZE))
        self._pre_roll_chunks = deque(maxlen=max_chunks)
        self._recorded_chunks = []
        self._pre_roll_samples_at_start = 0
        self._stream = None
        self._stream_selection = None
        self._stream_topology = None
        self._last_callback_at = None
        self._opened_at = None
        self._stream_generation = 0
        self._is_recording = False
        self._is_stopping = False
        self._has_audio_started = False
        self._start_requested_at = None
        self._lock = threading.Lock()
        self._lifecycle_lock = threading.RLock()
        self._audio_sink = None

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._is_recording

    @property
    def is_stopping(self) -> bool:
        with self._lock:
            return self._is_stopping

    @property
    def stream_ready(self) -> bool:
        return self._stream is not None

    def ensure_stream(self, input_device_id="") -> bool:
        with self._lifecycle_lock:
            return self._ensure_stream(input_device_id)

    def _ensure_stream(self, input_device_id="") -> bool:
        topology = self._topology_query()
        if self._stream is not None:
            try:
                active = bool(self._stream.active)
            except Exception:
                active = False
            with self._lock:
                last_audio = self._last_callback_at if self._last_callback_at is not None else self._opened_at
            healthy = active and last_audio is not None and self._clock() - last_audio < self._callback_timeout
            changed = topology is not None and self._stream_topology is not None and topology != self._stream_topology
            if healthy and not changed and input_device_id == self._stream_selection:
                if topology is not None:
                    self._stream_topology = topology
                return True
            log.info("マイク接続を開き直します: active=%s audio_flow=%s devices_changed=%s",
                     active, healthy, changed)
        if self._stream is not None:
            # Preserve the recording and its durable journal during hotplug recovery.
            self._release_stream()

        stream = None
        try:
            input_device = self._device_resolver(input_device_id)
            if input_device is None:
                log.error("利用可能な入力マイクが見つかりません")
                return False

            try:
                device = self._device_query(input_device)
                device_label = device.get("name", str(input_device))
            except Exception:
                device_label = str(input_device)

            with self._lock:
                self._stream_generation += 1
                generation = self._stream_generation
                self._last_callback_at = None
            stream = self._stream_factory(
                device=input_device,
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                blocksize=BLOCK_SIZE,
                dtype="float32",
                callback=lambda *args: self._audio_callback(*args, generation=generation),
            )
            stream.start()
            self._stream = stream
            self._opened_at = self._clock()
            self._stream_selection = input_device_id
            self._stream_topology = topology
            log.info("オーディオストリーム準備完了: %s", device_label)
            return True
        except Exception as error:
            log.error("オーディオストリーム準備エラー: %s", error)
            with self._lock:
                self._stream_generation += 1
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    log.exception("失敗したストリームを閉じられませんでした")
            self._stream = None
            return False

    def close(self) -> None:
        with self._lifecycle_lock:
            if self.is_recording or self.is_stopping:
                raise RuntimeError("録音を停止・保存してからストリームを閉じてください")
            self._close_stream()

    def _close_stream(self):
        self._release_stream()
        with self._lock:
            self._recorded_chunks = []
            self._is_recording = False
            self._is_stopping = False
            self._has_audio_started = False
            self._start_requested_at = None
            self._audio_sink = None

    def _release_stream(self):
        # Invalidate callbacks before touching the handle; drivers can deliver a
        # final callback after a USB device disappears or its stream is aborted.
        with self._lock:
            self._stream_generation += 1
            self._last_callback_at = None
            self._pre_roll_chunks.clear()
        stream = self._stream
        self._stream = None
        self._opened_at = None
        if stream is not None:
            try:
                stream.abort()
            except Exception:
                log.exception("オーディオストリーム終了エラー")
            finally:
                try:
                    stream.close()
                except Exception:
                    log.exception("オーディオストリーム解放エラー")

    def start(self, input_device_id="", audio_sink=None) -> bool:
        with self._lifecycle_lock:
            return self._start(input_device_id, audio_sink)

    def _start(self, input_device_id, audio_sink):
        with self._lock:
            if self._is_recording or self._is_stopping:
                return False

        if not self.ensure_stream(input_device_id):
            return False

        with self._lock:
            self._recorded_chunks = [chunk.copy() for chunk in self._pre_roll_chunks]
            self._pre_roll_chunks.clear()
            self._pre_roll_samples_at_start = sum(len(chunk) for chunk in self._recorded_chunks)
            self._audio_sink = audio_sink
            if audio_sink is not None:
                for chunk in self._recorded_chunks:
                    audio_sink.append(chunk)
            self._is_recording = True
            self._is_stopping = False
            self._has_audio_started = False
            self._start_requested_at = self._clock()

        if self._pre_roll_samples_at_start:
            log.info(
                "録音開始プリロール追加: %.2f秒 chunks=%d",
                self._pre_roll_samples_at_start / SAMPLE_RATE,
                len(self._recorded_chunks),
            )
        return True

    def stop(self, post_roll_seconds=POST_ROLL_SECONDS) -> Optional[Recording]:
        with self._lifecycle_lock:
            return self._stop(post_roll_seconds)

    def _stop(self, post_roll_seconds):
        with self._lock:
            if not self._is_recording or self._is_stopping:
                return None
            self._is_stopping = True

        if post_roll_seconds > 0:
            log.info("録音停止要求: 末尾保護 %.2f秒", post_roll_seconds)
            self._sleeper(post_roll_seconds)

        with self._lock:
            self._is_recording = False
            self._is_stopping = False
            chunks = tuple(self._recorded_chunks)
            pre_roll_samples = self._pre_roll_samples_at_start
            self._recorded_chunks = []
            self._pre_roll_samples_at_start = 0
            self._has_audio_started = False
            self._start_requested_at = None
            self._audio_sink = None

        return Recording(chunks=chunks, pre_roll_samples=pre_roll_samples)

    def _audio_callback(self, indata, frames, time_info, status, *, generation) -> None:
        if status:
            log.warning("オーディオステータス: %s", status)
        samples = indata[:, 0].astype(np.float32, copy=True)

        callback_started = False
        started_after = 0.0
        with self._lock:
            if generation != self._stream_generation:
                return
            self._last_callback_at = self._clock()
            if not self._is_recording:
                self._pre_roll_chunks.append(samples)
                return

            self._recorded_chunks.append(samples)
            if self._audio_sink is not None:
                self._audio_sink.append(samples)
            if self._is_stopping:
                return

            if not self._has_audio_started:
                self._has_audio_started = True
                callback_started = True
                if self._start_requested_at is not None:
                    started_after = self._clock() - self._start_requested_at

        if callback_started and self._on_audio_started is not None:
            self._notify(self._on_audio_started, started_after)
        if self._on_live_samples is not None:
            self._notify(self._on_live_samples, samples)

    @staticmethod
    def _notify(callback, value):
        try:
            callback(value)
        except Exception:
            # PortAudio stops invoking a callback that lets an exception escape.
            log.exception("録音状態の表示に失敗しました。録音は継続します")

"""Audio activity helpers for live recording status."""

import logging
import time
import threading

import numpy as np

from voice_input_tool.statuses import BUSY_STATUSES, HEARING, LISTENING

log = logging.getLogger("voice_input")

AUDIO_ACTIVITY_RMS_THRESHOLD = 0.025
AUDIO_ACTIVITY_RELEASE_RMS_THRESHOLD = 0.015
AUDIO_ACTIVITY_HOLD_SECONDS = 0.35
AUDIO_INITIAL_NOISE_FLOOR = 0.003
AUDIO_NOISE_FLOOR_ALPHA_IDLE = 0.08
AUDIO_NOISE_FLOOR_ALPHA_ACTIVE = 0.01
AUDIO_NOISE_ON_MULTIPLIER = 2.2
AUDIO_NOISE_OFF_MULTIPLIER = 1.4
AUDIO_STATUS_UPDATE_INTERVAL = 0.08


class AudioStatusDispatcher:
    """Coalesce display updates so slow UI/disk work cannot block PortAudio."""

    def __init__(self, on_started, on_samples):
        self._on_started = on_started
        self._on_samples = on_samples
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._stopped = False
        self._started = None
        self._samples = None
        self._thread = threading.Thread(target=self._run, name="audio-status", daemon=True)
        self._thread.start()

    def started(self, elapsed):
        with self._lock:
            self._started = elapsed
            self._event.set()

    def samples(self, samples):
        with self._lock:
            self._samples = samples
            self._event.set()

    def close(self):
        with self._lock:
            self._stopped = True
            self._event.set()
        self._thread.join(timeout=1.0)

    def _run(self):
        while True:
            self._event.wait()
            with self._lock:
                if self._stopped:
                    return
                started, samples = self._started, self._samples
                self._started = self._samples = None
                self._event.clear()
            for callback, value in ((self._on_started, started), (self._on_samples, samples)):
                if value is not None:
                    try:
                        callback(value)
                    except Exception:
                        log.exception("録音状態の更新に失敗しました")


class AudioActivityTracker:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self.reset()

    def reset(self):
        self.last_status_update = 0.0
        self.noise_floor = AUDIO_INITIAL_NOISE_FLOOR
        self.is_speaking = False
        self.last_active_at = 0.0

    def status_for_samples(self, samples, current_status):
        if current_status in BUSY_STATUSES:
            return None

        now = self._clock()
        if now - self.last_status_update < AUDIO_STATUS_UPDATE_INTERVAL:
            return None
        self.last_status_update = now

        rms = float(np.sqrt(np.mean(samples * samples))) if len(samples) else 0.0
        alpha = AUDIO_NOISE_FLOOR_ALPHA_ACTIVE if self.is_speaking else AUDIO_NOISE_FLOOR_ALPHA_IDLE
        self.noise_floor = max(0.00001, (1.0 - alpha) * self.noise_floor + alpha * rms)

        on_threshold = max(AUDIO_ACTIVITY_RMS_THRESHOLD, self.noise_floor * AUDIO_NOISE_ON_MULTIPLIER)
        off_threshold = max(AUDIO_ACTIVITY_RELEASE_RMS_THRESHOLD, self.noise_floor * AUDIO_NOISE_OFF_MULTIPLIER)

        if rms >= on_threshold:
            self.is_speaking = True
            self.last_active_at = now
        elif self.is_speaking and (
            rms <= off_threshold
            and now - self.last_active_at >= AUDIO_ACTIVITY_HOLD_SECONDS
        ):
            self.is_speaking = False

        new_status = HEARING if self.is_speaking else LISTENING
        if new_status != current_status:
            log.info(
                "音声状態: %s rms=%.4f noise=%.4f on=%.4f off=%.4f",
                new_status,
                rms,
                self.noise_floor,
                on_threshold,
                off_threshold,
            )
        return new_status

"""Keep the input stream connected even between recordings."""

import logging
import threading

log = logging.getLogger("voice_input")


class AudioDeviceMonitor:
    def __init__(self, maintain_stream, interval=1.0):
        self._maintain_stream = maintain_stream
        self._interval = interval
        self._closed = threading.Event()
        self._thread = threading.Thread(target=self._run, name="audio-device-monitor", daemon=True)

    def start(self):
        self._thread.start()

    def _run(self):
        while not self._closed.wait(self._interval):
            try:
                self._maintain_stream()
            except Exception:
                log.exception("マイクの再接続に失敗しました。次回の確認で再試行します")

    def close(self):
        self._closed.set()
        if self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=3.0)

"""Durable recording journal. Audio is saved before any ASR or paste attempt."""

import json
import logging
import os
from pathlib import Path
import queue
import tempfile
import threading
import time
import uuid

import numpy as np

from voice_input_tool.app_paths import RECORDINGS_DIR
from voice_input_tool.audio_constants import SAMPLE_RATE

log = logging.getLogger("voice_input")


def atomic_write(path, data):
    """Replace one checkpoint, keeping the old one intact on failure."""
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=".checkpoint-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class AudioJournal:
    """Move disk I/O off the microphone callback; checkpoint at least every second.

    The recorder also retains its chunks until finish, allowing a complete rewrite
    if this writer fails. A process crash can lose the most recent queued samples.
    """

    def __init__(self, path):
        self.path = Path(path)
        self._handle = open(os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb")
        self._queue = queue.SimpleQueue()
        self.error = None
        self._thread = threading.Thread(target=self._run, name="audio-journal", daemon=True)
        self._thread.start()

    def append(self, samples):
        # Samples belong to the recorder and are never mutated after capture.
        if self.error is None:
            self._queue.put(samples)

    def finish(self):
        self._queue.put(None)
        self._thread.join()
        if self.error is not None:
            raise OSError("録音の逐次保存に失敗しました") from self.error

    def _run(self):
        try:
            with self._handle as handle:
                last_sync = time.monotonic()
                while True:
                    try:
                        samples = self._queue.get(timeout=0.5)
                    except queue.Empty:
                        samples = ()
                    if samples is None:
                        handle.flush()
                        os.fsync(handle.fileno())
                        return
                    if len(samples):
                        handle.write(np.asarray(samples, dtype="<f4").tobytes())
                    if time.monotonic() - last_sync >= 0.5:
                        handle.flush()
                        os.fsync(handle.fileno())
                        last_sync = time.monotonic()
        except Exception as error:
            self.error = error
            log.exception("録音の逐次保存エラー: %s", self.path)
            while True:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break


class RecordingStore:
    def __init__(self, root=RECORDINGS_DIR):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = threading.RLock()

    def directory(self, recording_id):
        # IDs may also come from the CLI. Never interpret them as paths.
        if str(uuid.UUID(str(recording_id))) != recording_id:
            raise ValueError("録音IDが正しくありません")
        return self.root / recording_id

    def create(self, *, target_pid=None, use_llm=False, asr_backend="reazonspeech",
               correction_provider=None):
        recording_id = str(uuid.uuid4())
        directory = self.directory(recording_id)
        directory.mkdir(mode=0o700)
        payload = dict(id=recording_id, created_at=time.time(), state="recording",
                       sample_rate=SAMPLE_RATE, target_pid=target_pid, use_llm=use_llm,
                       asr_backend=asr_backend,
                       correction_provider=correction_provider if use_llm else None,
                       raw_text="", text="", error=None)
        self._save(payload)
        return recording_id

    def _save(self, payload):
        payload["updated_at"] = time.time()
        atomic_write(self.directory(payload["id"]) / "recording.json",
                     json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))

    def read(self, recording_id):
        with self._lock:
            return json.loads((self.directory(recording_id) / "recording.json").read_text())

    def update(self, recording_id, **changes):
        with self._lock:
            payload = self.read(recording_id)
            payload.update(changes)
            self._save(payload)
            # JSON is authoritative; readable copies can be regenerated after a crash.
            for field, filename in (("raw_text", "raw.txt"), ("text", "transcript.txt")):
                if field in changes:
                    atomic_write(self.directory(recording_id) / filename,
                                 changes[field].encode("utf-8"))
            return payload

    def open_audio(self, recording_id):
        return AudioJournal(self.directory(recording_id) / "audio.f32")

    def save_audio(self, recording_id, chunks):
        atomic_write(self.directory(recording_id) / "audio.f32",
                     np.concatenate(chunks).astype("<f4", copy=False).tobytes())

    def load_audio(self, recording_id):
        path = self.directory(recording_id) / "audio.f32"
        # A killed writer can leave an incomplete final float; retain all complete samples.
        count = path.stat().st_size // 4
        if not count:
            raise ValueError("復元できる音声データがありません")
        return np.fromfile(path, dtype="<f4", count=count)

    def list(self):
        records = []
        for path in self.root.glob("*/recording.json"):
            try:
                records.append(self.read(path.parent.name))
            except (OSError, ValueError, TypeError):
                log.exception("録音履歴を読み込めません: %s", path)
        return sorted(records, key=lambda item: item["created_at"])

    def recover(self):
        """Return interrupted audio jobs; never replay old paste attempts."""
        pending = []
        for record in self.list():
            if record["state"] not in {"recording", "queued", "transcribing", "correcting", "delivering"}:
                continue
            recording_id = record["id"]
            if record.get("text") or record.get("raw_text"):
                text = record.get("text") or record["raw_text"]
                self.update(recording_id, state="recovered", text=text,
                            error="処理が中断されました。履歴からテキストを回収できます。")
            else:
                self.update(recording_id, state="queued", recovered=True)
                pending.append(recording_id)
        return pending

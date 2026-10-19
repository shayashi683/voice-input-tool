"""Single ASR worker with durable checkpoints and independent microphone capture."""

import logging
import queue
import threading

import numpy as np

from voice_input_tool.audio_constants import ASR_TRAILING_SILENCE_SECONDS, SAMPLE_RATE
from voice_input_tool.statuses import CORRECTING, INSERTING, PROCESSING

log = logging.getLogger("voice_input")


class RecordingProcessor:
    def __init__(self, store, transcription, delivery, *, on_status, on_finished, notify):
        self.store = store
        self.transcription = transcription
        self.delivery = delivery
        self.on_status = on_status
        self.on_finished = on_finished
        self.notify = notify
        self._queue = queue.Queue()
        self._stopping = threading.Event()
        self._thread = threading.Thread(target=self._run, name="transcription-worker", daemon=True)

    def start(self):
        recovered = self.store.recover()
        for recording_id in recovered:
            self.submit(recording_id, deliver=False)
        self._thread.start()
        if recovered or any(r["state"] == "recovered" for r in self.store.list()):
            self._notify("中断された録音を復元します", "復元結果は録音履歴に保存されます")

    @property
    def busy(self):
        return bool(self._queue.unfinished_tasks)

    def submit(self, recording_id, target_app=None, *, deliver=True):
        self.store.update(recording_id, state="queued", error=None)
        self._queue.put((recording_id, target_app, deliver))

    def shutdown(self, timeout=2.0):
        # Audio and queue membership already exist on disk. Do not paste late results.
        self._stopping.set()
        if self._thread.is_alive():
            self._thread.join(timeout)

    def _notify(self, title, message):
        try:
            self.notify("Voice Input", title, message)
        except Exception:
            log.exception("通知を表示できませんでした")

    def _run(self):
        while not self._stopping.is_set():
            try:
                job = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                self.process(*job)
            except Exception:
                # A broken status callback or storage failure must not kill the worker.
                log.exception("録音処理ワーカーのエラー")
            finally:
                self._queue.task_done()
                try:
                    self.on_finished()
                except Exception:
                    log.exception("録音処理状態を更新できません")

    def process(self, recording_id, target_app=None, deliver=False):
        try:
            record = self.store.read(recording_id)
            self.store.update(recording_id, state="transcribing")
            self.on_status(PROCESSING)
            samples = self.store.load_audio(recording_id)
            padding = np.zeros(int(ASR_TRAILING_SILENCE_SECONDS * SAMPLE_RATE), dtype=np.float32)
            # Keep the model chosen when recording began, including across an
            # app restart. Legacy recordings were local and remain local.
            result = self.transcription.transcribe(
                np.concatenate((samples, padding)),
                backend=record.get("asr_backend", "reazonspeech"),
            )
            raw_text = result.text.strip()
            recognition = {"raw_text": raw_text}
            backend = getattr(result, "backend", None)
            if isinstance(backend, str):
                recognition["asr_backend"] = backend
            self.store.update(recording_id, **recognition)
            if not raw_text:
                raise ValueError("認識結果が空でした。音声を保存しました。再文字起こしできます。")

            text = raw_text
            # A prior release's correction opt-in does not authorize sending its
            # text to a new provider when interrupted recordings are recovered.
            if (record["use_llm"] and record.get("correction_provider") == "openai"
                    and self.transcription.has_correction_key() and not self._stopping.is_set()):
                self.store.update(recording_id, state="correcting")
                self.on_status(CORRECTING)
                try:
                    text = self.transcription.correct(raw_text)
                    if not text or not text.strip():
                        raise ValueError("補正結果が空でした")
                except Exception:
                    log.exception("LLM補正に失敗したため原文を保持します: %s", recording_id)
                    text = raw_text

            # This checkpoint precedes every external delivery side effect.
            self.store.update(recording_id, text=text, state="recovered")
            if deliver and not self._stopping.is_set():
                self.store.update(recording_id, state="delivering")
                self.on_status(INSERTING)
                outcome = self.delivery.deliver(text, target_app, record.get("target_pid"))
                self.store.update(recording_id, state=outcome.status)
                if outcome.status != "paste_sent":
                    self._notify("認識結果を履歴に保存しました", "貼り付けを確認できませんでした。履歴から回収できます。")
            else:
                self._notify("文字起こしを履歴に保存しました", text[:100])
        except Exception as error:
            log.exception("録音処理失敗: %s", recording_id)
            try:
                self.store.update(recording_id, state="failed", error=str(error))
            except Exception:
                log.exception("失敗状態の保存にも失敗しました: %s", recording_id)
            self._notify("文字起こしを完了できませんでした", "保存済みの音声・認識結果は録音履歴から回収できます。")

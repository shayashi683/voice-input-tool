#!/usr/bin/env python3
"""
Voice Input Tool - ReazonSpeech ASR + Optional LLM Correction
Mac Mini (Apple Silicon) 向け ローカル音声入力ツール

メニューバーアイコンから操作:
  録音開始/停止、設定画面、終了
"""

import threading
import logging
from voice_input_tool.audio_recorder import BufferedAudioRecorder
from voice_input_tool.audio_devices import stable_input_device_id
from voice_input_tool.audio_monitor import AudioDeviceMonitor
from voice_input_tool.audio_constants import (
    PRE_ROLL_SECONDS,
)
from voice_input_tool.audio_pipeline import AudioActivityTracker, AudioStatusDispatcher
from voice_input_tool.app_status import AppStatusController
from voice_input_tool.config import load_config, normalize_config, save_config
from voice_input_tool.hotkeys import DEFAULT_RECORD_HOTKEY, hotkey_to_display
from voice_input_tool.logging_setup import configure_logging
from voice_input_tool.macos_text import (
    get_frontmost_application,
    target_pid as app_target_pid,
)
from voice_input_tool.notifications import notify_user
from voice_input_tool.statuses import (
    IDLE,
    LISTENING,
    PROCESSING,
    STARTING,
)
from voice_input_tool.recording_store import RecordingStore
from voice_input_tool.recording_processor import RecordingProcessor
from voice_input_tool.text_delivery import TextDeliveryService
from voice_input_tool.transcription import TranscriptionService

configure_logging()
log = logging.getLogger("voice_input")

# メニューバーアプリ
import rumps

try:
    from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
    HAS_APPKIT_APPLICATION = True
except ImportError:
    NSApplication = None
    NSApplicationActivationPolicyAccessory = None
    HAS_APPKIT_APPLICATION = False

# キーボードホットキー
try:
    from pynput import keyboard
    HAS_HOTKEY = True
except ImportError:
    keyboard = None
    HAS_HOTKEY = False

try:
    from PyObjCTools import AppHelper
    HAS_APP_HELPER = True
except ImportError:
    AppHelper = None
    HAS_APP_HELPER = False

# ============================================================
# メニューバーアプリ
# ============================================================

class _HeadlessMenuItem:
    def __init__(self, title=""):
        self.title = title


class VoiceInputApp(rumps.App):
    def __init__(
        self,
        recognizer=None,
        use_llm=None,
        headless=False,
        native_output=False,
        config=None,
        recording_store=None,
    ):
        self._headless = headless
        self.config = normalize_config(config if config is not None else load_config())
        device_id = self.config["input_device_id"]
        stable_id = stable_input_device_id(device_id)
        if stable_id != device_id:
            self.config["input_device_id"] = stable_id
            try:
                save_config(self.config)
                log.info("入力マイク設定を名前で保存しました: %s", stable_id)
            except OSError:
                log.exception("入力マイクの移行設定を保存できませんでした")
        if use_llm is None:
            use_llm = self.config["use_llm"]
        if not headless:
            super().__init__(
                "Voice Input",
                icon=None,
                title="🎙",
                quit_button=None,
            )
        else:
            self.title = "VI"
            self.icon = None
        self.use_llm = use_llm
        self._control_lock = threading.RLock()
        self._shutting_down = False
        self._session = None
        self._unsaved_recordings = []
        self._store = recording_store or RecordingStore()
        self._settings_ctrl = None
        self._target_app = None
        self._target_pid = None
        self._status_controller = None
        self._audio_activity = AudioActivityTracker()
        self._audio_status = AudioStatusDispatcher(self._on_audio_started, self._update_live_audio_status)
        self._recorder = BufferedAudioRecorder(
            on_audio_started=self._audio_status.started,
            on_live_samples=self._audio_status.samples,
        )
        self._transcription = TranscriptionService(self.config, recognizer=recognizer)
        self._delivery = TextDeliveryService(native_output=native_output)

        self._hotkey_listener = None

        # メニュー構成
        hotkey_display = self._get_hotkey_display()
        llm_label = "LLM補正: ON" if self.use_llm else "LLM補正: OFF"
        if headless:
            self.record_button = _HeadlessMenuItem(f"録音開始 ({hotkey_display})")
            self.llm_status = _HeadlessMenuItem(llm_label)
            self.settings_button = _HeadlessMenuItem("設定...")
        else:
            self.record_button = rumps.MenuItem(
                f"録音開始 ({hotkey_display})", callback=self.toggle_recording
            )
            self.llm_status = rumps.MenuItem(llm_label, callback=self.toggle_llm)
            self.settings_button = rumps.MenuItem("設定...", callback=self.open_settings)

            self.menu = [
                self.record_button,
                None,
                self.llm_status,
                self.settings_button,
                rumps.MenuItem("録音履歴を開く", callback=self.open_history),
                rumps.MenuItem("終了", callback=self.quit_app),
            ]

        call_after = AppHelper.callAfter if HAS_APP_HELPER else None
        timer_factory = lambda callback, interval: rumps.Timer(callback, interval)
        self._status_controller = AppStatusController(
            app=self,
            record_button=self.record_button,
            get_hotkey_display=self._get_hotkey_display,
            use_llm=lambda: self.use_llm,
            headless=headless,
            call_after=call_after,
            timer_factory=timer_factory,
        )

        self._processor = RecordingProcessor(
            self._store, self._transcription, self._delivery,
            on_status=self._set_processing_status,
            on_finished=self._restore_recording_status,
            notify=notify_user,
        )
        self._processor.start()

        # In native/headless mode the Objective-C status app owns the global hotkey.
        # Registering a second Python listener can cause duplicate toggles and extra
        # accessibility/input-monitoring prompts.
        if not self._headless:
            self._register_hotkey()

        self._audio_monitor = AudioDeviceMonitor(self._maintain_audio_stream)
        self._audio_monitor.start()

    def _set_status(self, status, force=False):
        self._status_controller.set(status, force=force)

    def _stop_status_icon_animation(self):
        self._status_controller.stop_icon_animation()

    def _current_status(self):
        return self._status_controller.current()

    def _restore_recording_status(self):
        with self._control_lock:
            self._set_status(LISTENING if self.is_recording else
                             PROCESSING if self._processor.busy else IDLE)

    def _set_processing_status(self, status):
        with self._control_lock:
            if not self.is_recording:
                self._set_status(status)

    @property
    def is_recording(self):
        return self._recorder.is_recording

    def _get_hotkey_display(self):
        """設定からホットキーの表示文字列を取得"""
        hotkey = self.config.get("hotkey_record", DEFAULT_RECORD_HOTKEY)
        return hotkey_to_display(hotkey)

    def _close_audio_stream(self):
        self._recorder.close()

    def prepare_audio_stream(self):
        """Keep a live input buffer so speech at hotkey time is not lost."""
        with self._control_lock:
            if self._shutting_down or self.is_recording or self._recorder.is_stopping:
                return False
            prepared = self._recorder.ensure_stream(self.config.get("input_device_id", ""))
            if prepared:
                log.info(
                    "待機中マイクストリームを事前準備しました: pre_roll=%.2fs",
                    PRE_ROLL_SECONDS,
                )
            return prepared

    def _maintain_audio_stream(self):
        with self._control_lock:
            if not self._shutting_down:
                self._recorder.ensure_stream(self.config.get("input_device_id", ""))

    def _register_hotkey(self):
        """グローバルホットキーを登録"""
        if not HAS_HOTKEY:
            log.warning("pynput未インストール: ホットキー無効")
            return

        # 既存のリスナーを停止
        if self._hotkey_listener:
            self._hotkey_listener.stop()
            self._hotkey_listener = None

        hotkey = self.config.get("hotkey_record", DEFAULT_RECORD_HOTKEY)
        log.info(f"ホットキー登録: {hotkey}")

        try:
            self._hotkey_listener = keyboard.GlobalHotKeys({
                hotkey: self.toggle_recording,
            })
            self._hotkey_listener.daemon = True
            self._hotkey_listener.start()
        except Exception as e:
            log.error(f"ホットキー登録エラー: {e}")

    def toggle_recording(self, sender=None, target_pid=None):
        with self._control_lock:
            if self.is_recording:
                self.stop_recording()
            else:
                self.start_recording(target_pid=target_pid)

    def start_recording(self, target_pid=None):
        with self._control_lock:
            if self._shutting_down or self.is_recording or self._recorder.is_stopping:
                return
            self._audio_activity.reset()
            self._target_pid = target_pid if target_pid and target_pid > 0 else None
            self._target_app = None if self._target_pid else get_frontmost_application()
            pid = self._target_pid or app_target_pid(self._target_app)
            self._set_status(STARTING)
            journal = None
            recording_id = None
            try:
                recording_id = self._store.create(
                    target_pid=pid,
                    use_llm=self.use_llm,
                    asr_backend=self.config.get("asr_backend", "reazonspeech"),
                    correction_provider="openai" if self.use_llm else None,
                )
                journal = self._store.open_audio(recording_id)
                if not self._recorder.start(self.config.get("input_device_id", ""), audio_sink=journal):
                    raise RuntimeError("マイクを開始できませんでした")
                self._session = (recording_id, journal, self._target_app)
                log.info("録音開始: id=%s target_pid=%s", recording_id, pid)
            except Exception as error:
                log.exception("録音を開始できませんでした")
                if journal is not None:
                    try:
                        journal.finish()
                    except OSError:
                        log.exception("未開始の録音ファイルを閉じられませんでした")
                if recording_id is not None:
                    try:
                        self._store.update(recording_id, state="failed", error=str(error))
                    except OSError:
                        log.exception("録音開始エラーを保存できませんでした")
                self._restore_recording_status()
                notify_user("Voice Input", "録音を開始できませんでした", str(error))

    def stop_recording(self):
        with self._control_lock:
            if not self.is_recording:
                return
            self._set_status(PROCESSING)
            recording = self._recorder.stop()
            if recording is None:
                return
            recording_id, journal, target_app = self._session
            self._session = None
            self._audio_activity.reset()
            try:
                try:
                    journal.finish()
                except OSError:
                    # The in-memory copy is retained until a full durable save succeeds.
                    self._store.save_audio(recording_id, recording.chunks)
                self._store.update(recording_id, duration_seconds=recording.duration_seconds)
                self._processor.submit(recording_id, target_app)
                log.info("録音保存完了: id=%s duration=%.2fs", recording_id, recording.duration_seconds)
            except Exception:
                self._unsaved_recordings.append((recording_id, recording))
                log.exception("録音保存に失敗しました。メモリ内の音声を保持します: %s", recording_id)
                notify_user("Voice Input", "録音の保存に失敗しました", "空き容量を確認してください。音声は終了までメモリに保持します。")
            self._restore_recording_status()

    def _on_audio_started(self, elapsed):
        if not self.is_recording or self._recorder.is_stopping:
            return
        log.info("入力待機開始 (%.2fs)", elapsed)
        self._set_status(LISTENING)
        log.info("音声データ受信開始")

    def _update_live_audio_status(self, samples):
        if not self.is_recording or self._recorder.is_stopping:
            return
        new_status = self._audio_activity.status_for_samples(samples, self._current_status())
        if new_status is not None:
            self._set_status(new_status)

    def open_history(self, sender=None):
        import subprocess
        subprocess.Popen(["open", str(self._store.root)])

    def open_settings(self, sender=None):
        from voice_input_tool.settings_ui import SettingsWindowController
        from Cocoa import NSApp
        self._settings_ctrl = SettingsWindowController.alloc().initWithCallback_(self._on_settings_saved)
        self._settings_ctrl.show()
        NSApp.activateIgnoringOtherApps_(True)

    def toggle_llm(self, sender=None):
        """メニューから LLM 補正のON/OFFを切り替え、即時保存"""
        self.use_llm = not self.use_llm
        self.llm_status.title = "LLM補正: ON" if self.use_llm else "LLM補正: OFF"
        self.config["use_llm"] = self.use_llm
        try:
            save_config(self.config)
        except Exception as e:
            log.error(f"設定保存に失敗しました: {e}")
        self._set_status(self._current_status(), force=True)

    def _on_settings_saved(self, new_config):
        with self._control_lock:
            self._apply_settings(new_config)

    def _apply_settings(self, new_config):
        self.config = normalize_config(new_config)
        self.use_llm = self.config["use_llm"]
        self._transcription.configure(self.config)
        self.llm_status.title = "LLM補正: ON" if self.use_llm else "LLM補正: OFF"
        if not self.is_recording:
            self._close_audio_stream()
            self.prepare_audio_stream()
        # ホットキー再登録
        if not self._headless:
            self._register_hotkey()
        self._set_status(self._current_status(), force=True)
        log.info(f"設定更新: LLM={'ON' if self.use_llm else 'OFF'}, "
                 f"ホットキー={self.config.get('hotkey_record')}, "
                 f"入力マイク={self.config.get('input_device_id', '') or '自動選択'}")

    def shutdown(self):
        with self._control_lock:
            if self._shutting_down:
                return
            self._shutting_down = True
            self.stop_recording()
            for recording_id, recording in self._unsaved_recordings[:]:
                try:
                    self._store.save_audio(recording_id, recording.chunks)
                    self._store.update(recording_id, state="queued")
                    self._unsaved_recordings = [item for item in self._unsaved_recordings if item[0] != recording_id]
                except Exception:
                    log.exception("終了時の録音再保存に失敗しました: %s", recording_id)
        # Join outside the control lock: a monitor tick may be waiting for it.
        self._audio_monitor.close()
        self._processor.shutdown()
        self._audio_status.close()
        self._stop_status_icon_animation()
        self._close_audio_stream()
        if self._hotkey_listener:
            self._hotkey_listener.stop()

    def quit_app(self, sender=None):
        self.shutdown()
        rumps.quit_application()

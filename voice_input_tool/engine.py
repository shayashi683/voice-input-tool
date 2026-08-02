#!/usr/bin/env python3
"""
Voice Input Tool - ReazonSpeech ASR + Optional LLM Correction
Mac Mini (Apple Silicon) 向け ローカル音声入力ツール

メニューバーアイコンから操作:
  録音開始/停止、設定画面、終了
"""

import argparse
import collections
import os
import queue
import signal
import sys
import time
import wave
import numpy as np
import threading
import logging
from voice_input_tool.app_paths import LOG_DIR, MODEL_DIR
from voice_input_tool.audio_constants import BLOCK_SIZE, CHANNELS, SAMPLE_RATE
from voice_input_tool.audio_devices import resolve_input_device
from voice_input_tool.app_status import AppStatusController
from voice_input_tool.config import DEFAULTS, load_config, save_config
from voice_input_tool.llm_correction import BACKENDS, configure_llm, current_backend, llm_correct
from voice_input_tool.log_utils import truncate_for_log
from voice_input_tool.macos_text import (
    copy_to_clipboard,
    get_frontmost_application,
    insert_text_at_cursor,
    request_accessibility_permission,
    target_pid as app_target_pid,
)
from voice_input_tool.native_bridge import (
    NativeCommandReader,
    ensure_bridge_files,
    native_paste_bridge_ready,
    parse_command_line,
    write_output,
)
from voice_input_tool.notifications import notify_user
from voice_input_tool.speech_engine import (
    VAD_MAX_SPEECH_SECONDS,
    create_recognizer,
    create_vad,
    recognize_speech,
)
from voice_input_tool.status_bar_diagnostics import install_status_bar_diagnostics

# ファイルログ設定
_log_dir = str(LOG_DIR)
os.makedirs(_log_dir, exist_ok=True)
logging.basicConfig(
    filename=os.path.join(_log_dir, "voice-input.log"),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("voice_input")
_error_handler = logging.FileHandler(os.path.join(_log_dir, "voice-input-error.log"), encoding="utf-8")
_error_handler.setLevel(logging.ERROR)
_error_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
logging.getLogger().addHandler(_error_handler)

# 音声入力
import sounddevice as sd

# メニューバーアプリ
import rumps

try:
    from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
    HAS_APPKIT_APPLICATION = True
except ImportError:
    HAS_APPKIT_APPLICATION = False

# キーボードホットキー
try:
    from pynput import keyboard
    HAS_HOTKEY = True
except ImportError:
    HAS_HOTKEY = False

try:
    from PyObjCTools import AppHelper
    HAS_APP_HELPER = True
except ImportError:
    HAS_APP_HELPER = False

# ============================================================
# 設定
# ============================================================

# 設定ファイルから読み込み
APP_CONFIG = load_config()

configure_llm(APP_CONFIG)


# ============================================================
# メニューバーアプリ
# ============================================================

# VAD（Silero VAD）が検出した区間がこの秒数未満ならノイズとみなして捨てる
MIN_SEGMENT_SECONDS = 0.3
# VADが返す区間の開始位置（front.start）は実測では実際の発話開始の±0.05秒程度に
# 収まるが、静かに話し始めた場合はさらに手前から声が出ていることがある
# （テスト音源で最大0.3秒強）。そのため区間の直前この秒数分を履歴バッファから
# 取り出して先頭に足す。直前の区間の末尾を越えては足さないので、
# 前の発話の音声が混入することはない
SEGMENT_HEAD_PAD_SECONDS = 0.35
HEAD_PAD_SAMPLES = int(SEGMENT_HEAD_PAD_SECONDS * SAMPLE_RATE)
# 履歴バッファの長さ。区間は最長 VAD_MAX_SPEECH_SECONDS 話し続けてから
# 確定することがあるため、確定時点から「区間開始のさらに手前」まで
# さかのぼって取り出せるだけの余裕を持たせる
HISTORY_SECONDS = VAD_MAX_SPEECH_SECONDS + 4.0
HISTORY_SAMPLES = int(HISTORY_SECONDS * SAMPLE_RATE)
# 区間の先頭・末尾が急に始まる/終わることでクリックノイズが乗り、
# ASRが余分な子音を誤認識することがあるため、短いフェードをかける
FADE_SECONDS = 0.01
FADE_SAMPLES = max(1, int(FADE_SECONDS * SAMPLE_RATE))


def _apply_fade(samples):
    if len(samples) < FADE_SAMPLES * 2:
        return samples
    samples = samples.copy()
    ramp = np.linspace(0.0, 1.0, FADE_SAMPLES, dtype=np.float32)
    samples[:FADE_SAMPLES] *= ramp
    samples[-FADE_SAMPLES:] *= ramp[::-1]
    return samples


class _HeadlessMenuItem:
    def __init__(self, title=""):
        self.title = title


class VoiceInputApp(rumps.App):
    def __init__(self, recognizer=None, use_llm=False, headless=False, native_output=False):
        self._headless = headless
        self._native_output = native_output
        if not headless:
            # 終了時に録音停止やステータスファイルの後始末を行うため、
            # rumps標準の終了ボタンではなく自前のメニュー項目（quit_app）を使う
            super().__init__(
                "Voice Input",
                icon=None,
                title="🎙",
                quit_button=None,
            )
        else:
            self.title = "VI"
            self.icon = None
        self.recognizer = recognizer
        self.vad = None
        self.use_llm = use_llm
        self.is_recording = False
        self._stream = None
        self._settings_ctrl = None
        self._target_app = None
        self._target_pid = None
        self._status_controller = None
        self._has_audio_started = False
        self._had_any_segment = False
        self._is_speech_active = False
        self._start_requested_at = None
        self._recognizer_lock = threading.Lock()
        self._vad_init_lock = threading.Lock()
        # 録音の開始/停止はホットキー（pynputスレッド）・メニュー（メインスレッド）・
        # ヘッドレスコマンドの複数経路から呼ばれるため、状態遷移を排他制御する。
        # toggle -> start/stop と入れ子で取得するので再入可能ロックにする
        self._recording_lock = threading.RLock()
        # VAD（発話区間の検出）へのアクセスは録音スレッドと停止操作のスレッドの
        # 両方から行われるため、内部状態の破壊を避けるために排他制御する
        self._vad_lock = threading.Lock()
        # 発話区間の頭欠けを防ぐため、VADへ渡した音声の履歴を
        # (開始サンプル位置, サンプル列) の組で保持する（_vad_lockで保護）
        self._history = collections.deque()
        self._vad_samples_fed = 0
        self._last_segment_end = 0
        self._segment_queue = queue.Queue()
        self._segment_worker_thread = threading.Thread(
            target=self._segment_worker_loop, daemon=True
        )
        self._segment_worker_thread.start()

        # pynputのmacOSリスナーはスレッド起動のたびにTSM（テキスト入力管理）へ
        # アクセスしており、繰り返し起動し直すとネイティブクラッシュ
        # （dispatch_assert_queue_fail）を引き起こすため、リスナースレッド自体は
        # アプリ起動時に一度だけ作成し、以後はホットキーの組み合わせ判定
        # （HotKeyオブジェクト、スレッドを伴わない）だけを差し替える
        self._hotkey_listener = None
        self._hotkey = None
        self._hotkey_lock = threading.Lock()

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
                None,
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

        # ホットキー登録
        self._register_hotkey()

        # 初回録音時にモデル読み込みでマイク起動が遅れ、話し始めが丸ごと
        # 失われるのを防ぐため、モデルはバックグラウンドで先に読み込んでおく
        threading.Thread(target=self._preload_models, daemon=True).start()

    def _preload_models(self):
        try:
            self._ensure_vad()
            self._ensure_recognizer()
        except BaseException:
            # モデルファイル欠如時に create_* が SystemExit を投げるため、それも拾う
            log.exception("モデルの事前読み込みに失敗しました")

    def _set_status(self, status, force=False):
        self._status_controller.set(status, force=force)

    def _stop_status_icon_animation(self):
        self._status_controller.stop_icon_animation()

    def _current_status(self):
        return self._status_controller.current()

    def _restore_recording_status(self):
        self._status_controller.restore_recording_status(
            self.is_recording, self._is_speech_active
        )

    def _get_hotkey_display(self):
        """設定からホットキーの表示文字列を取得"""
        from voice_input_tool.settings_ui import hotkey_to_display
        hotkey = APP_CONFIG.get("hotkey_record", "<ctrl>+<shift>+<space>")
        return hotkey_to_display(hotkey)

    def _ensure_vad(self):
        if self.vad is not None:
            return self.vad

        with self._vad_init_lock:
            if self.vad is not None:
                return self.vad

            log.info("VADモデル読み込み開始")
            start = time.time()
            self.vad = create_vad()
            log.info("VADモデル読み込み完了 (%.1fs)", time.time() - start)
            return self.vad

    def _ensure_audio_stream(self):
        if self._stream is not None:
            return True

        try:
            input_device = resolve_input_device(APP_CONFIG.get("input_device_id", ""))
            if input_device is None:
                log.error("利用可能な入力マイクが見つかりません")
                return False

            self._stream = sd.InputStream(
                device=input_device,
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                blocksize=BLOCK_SIZE,
                dtype="float32",
                callback=self._audio_callback,
            )
            self._stream.start()
            device_label = sd.query_devices(input_device).get("name", str(input_device))
            log.info("オーディオストリーム準備完了: %s", device_label)
            return True
        except Exception as e:
            log.error(f"オーディオストリーム準備エラー: {e}")
            self._stream = None
            return False

    def _close_audio_stream(self):
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None

    def _register_hotkey(self):
        """設定されたホットキーの組み合わせを反映する。

        listenerスレッド自体は最初に一度だけ起動し、以後は使い回す。
        ホットキーの変更時は、判定に使う HotKey オブジェクト（スレッドを
        伴わない軽量な状態機械）だけを差し替える。
        """
        if not HAS_HOTKEY:
            log.warning("pynput未インストール: ホットキー無効")
            return

        hotkey = APP_CONFIG.get("hotkey_record", "<ctrl>+<shift>+<space>")
        log.info(f"ホットキー登録: {hotkey}")

        try:
            new_hotkey = keyboard.HotKey(keyboard.HotKey.parse(hotkey), self.toggle_recording)
        except Exception as e:
            # 設定に解析できないホットキーが残っていてもアプリを操作不能に
            # しないよう、デフォルトのホットキーへフォールバックする
            log.error(f"ホットキー登録エラー: {e}")
            fallback = DEFAULTS["hotkey_record"]
            if hotkey == fallback:
                return
            try:
                new_hotkey = keyboard.HotKey(keyboard.HotKey.parse(fallback), self.toggle_recording)
                log.warning(f"デフォルトのホットキーを使用します: {fallback}")
            except Exception as fallback_error:
                log.error(f"デフォルトホットキーの登録にも失敗しました: {fallback_error}")
                return

        with self._hotkey_lock:
            self._hotkey = new_hotkey

        if self._hotkey_listener is None:
            try:
                self._hotkey_listener = keyboard.Listener(
                    on_press=self._on_hotkey_press,
                    on_release=self._on_hotkey_release,
                )
                self._hotkey_listener.daemon = True
                self._hotkey_listener.start()
            except Exception as e:
                log.error(f"ホットキーリスナー起動エラー: {e}")

    def _on_hotkey_press(self, key, injected=False):
        if injected:
            return
        with self._hotkey_lock:
            hotkey = self._hotkey
        if hotkey is not None:
            hotkey.press(self._hotkey_listener.canonical(key))

    def _on_hotkey_release(self, key, injected=False):
        if injected:
            return
        with self._hotkey_lock:
            hotkey = self._hotkey
        if hotkey is not None:
            hotkey.release(self._hotkey_listener.canonical(key))

    def toggle_recording(self, sender=None, target_pid=None):
        with self._recording_lock:
            if self.is_recording:
                self.stop_recording()
            else:
                self.start_recording(target_pid=target_pid)

    def start_recording(self, target_pid=None):
        with self._recording_lock:
            if self.is_recording:
                return
            vad = self._ensure_vad()
            with self._vad_lock:
                vad.reset()
                self._history.clear()
                self._vad_samples_fed = 0
                self._last_segment_end = 0
            self._is_speech_active = False
            self._had_any_segment = False
            self._target_pid = target_pid if target_pid and target_pid > 0 else None
            self._target_app = None if self._target_pid else get_frontmost_application()
            self._has_audio_started = False
            self._start_requested_at = time.time()
            self._set_status("starting")
            log.info("録音開始要求: target_pid=%s", self._target_pid or app_target_pid(self._target_app))

            self.is_recording = True
            if not self._ensure_audio_stream():
                self.is_recording = False
                self._has_audio_started = False
                self._start_requested_at = None
                self._set_status("idle")
                return

    def stop_recording(self):
        with self._recording_lock:
            if not self.is_recording:
                return
            self.is_recording = False
            self._has_audio_started = False
            self._start_requested_at = None

            target_app = self._target_app
            target_pid = self._target_pid
            with self._vad_lock:
                self.vad.flush()
                self._drain_vad_segments_locked(target_app, target_pid, is_final=True)

            if not self._had_any_segment:
                log.info("録音停止: 音声データなし")
            self._set_status("idle")

    def _audio_callback(self, indata, frames, time_info, status):
        if status:
            log.warning(f"オーディオステータス: {status}")
        if not self.is_recording:
            return
        samples = indata[:, 0].astype(np.float32)

        if not self._has_audio_started:
            self._has_audio_started = True
            if self._start_requested_at is not None:
                elapsed = time.time() - self._start_requested_at
                log.info(f"入力待機開始 ({elapsed:.2f}s)")
            self._set_status("listening")
            log.info("音声データ受信開始")

        with self._vad_lock:
            self.vad.accept_waveform(samples)
            self._history.append((self._vad_samples_fed, samples))
            self._vad_samples_fed += len(samples)
            while (
                self._history
                and self._history[0][0] + len(self._history[0][1])
                < self._vad_samples_fed - HISTORY_SAMPLES
            ):
                self._history.popleft()
            is_speech = self.vad.is_speech_detected()
            if is_speech != self._is_speech_active:
                self._is_speech_active = is_speech
                self._set_status("hearing" if is_speech else "listening")
            self._drain_vad_segments_locked(self._target_app, self._target_pid)

    def _drain_vad_segments_locked(self, target_app, target_pid, is_final=False):
        """VAD が確定させた発話区間をすべて取り出してキューへ渡す。

        呼び出し元で self._vad_lock を保持していること。
        """
        while not self.vad.empty():
            # front は参照であり、pop() を呼ぶと無効になるため、
            # pop() より前に start と samples を取り出しておく必要がある
            seg_start = int(self.vad.front.start)
            samples = np.asarray(self.vad.front.samples, dtype=np.float32)
            self.vad.pop()
            # VADは静かな話し始めを取りこぼすことがあるため、区間開始の直前を
            # 履歴から前置きする（直前の区間の末尾は越えない）
            pad_start = max(self._last_segment_end, seg_start - HEAD_PAD_SAMPLES, 0)
            prefix = self._history_slice_locked(pad_start, seg_start)
            self._last_segment_end = seg_start + len(samples)
            if len(prefix):
                samples = np.concatenate([prefix, samples])
            duration = len(samples) / SAMPLE_RATE
            if duration < MIN_SEGMENT_SECONDS:
                continue
            self._had_any_segment = True
            label = "録音停止: 最終区間" if is_final else "発話区切りを検出"
            log.info("%s: %.1f秒 target_pid=%s", label, duration, target_pid or app_target_pid(target_app))
            self._segment_queue.put((_apply_fade(samples), target_app, target_pid))

    def _history_slice_locked(self, start, end):
        """履歴バッファから [start, end) のサンプルを取り出す。

        呼び出し元で self._vad_lock を保持していること。
        """
        if end <= start:
            return np.array([], dtype=np.float32)
        parts = []
        for block_start, block in self._history:
            block_end = block_start + len(block)
            if block_end <= start:
                continue
            if block_start >= end:
                break
            parts.append(block[max(0, start - block_start):end - block_start])
        if not parts:
            return np.array([], dtype=np.float32)
        return np.concatenate(parts)

    def _segment_worker_loop(self):
        while True:
            samples, target_app, target_pid = self._segment_queue.get()
            self._handle_speech_segment(samples, target_app, target_pid)

    def _ensure_recognizer(self):
        if self.recognizer is not None:
            return self.recognizer

        with self._recognizer_lock:
            if self.recognizer is not None:
                return self.recognizer

            log.info("ASRモデル読み込み開始")
            start = time.time()
            self.recognizer = create_recognizer()
            log.info("ASRモデル読み込み完了 (%.1fs)", time.time() - start)
            return self.recognizer

    def _handle_speech_segment(self, speech_samples, target_app, target_pid_value=None):
        try:
            self._set_status("processing")
            start = time.time()
            recognizer = self._ensure_recognizer()
            text = recognize_speech(recognizer, speech_samples)
            elapsed = time.time() - start

            if not text:
                log.info(f"ASR ({elapsed:.2f}s): 認識結果なし")
                return

            log.info(f"ASR ({elapsed:.2f}s): {text}")
            text = self._text_for_insert(text)
            if not text:
                return

            if self._native_output:
                self._set_status("inserting")
                self._send_text_to_native_app(text, target_app, target_pid_value)
                if not native_paste_bridge_ready():
                    log.warning("ネイティブ貼り付け受信側が未起動のためPython側で貼り付けます")
                    inserted = insert_text_at_cursor(text, target_app)
                    if inserted:
                        log.info("Python経由でカーソル位置に入力しました")
                    else:
                        notify_user(
                            "Voice Input",
                            "アクセシビリティ許可が必要です",
                            "Python または VoiceInputTool を許可してください",
                        )
                return

            self._set_status("inserting")
            inserted = insert_text_at_cursor(text, target_app)
            if inserted:
                log.info("カーソル位置に入力しました")
            notify_user(
                "Voice Input",
                "カーソル位置に入力しました" if inserted else "貼り付け不可のためコピーしました",
                text[:100],
            )
        except Exception:
            log.exception("発話セグメント処理で予期しないエラーが発生しました")
            notify_user(
                "Voice Input",
                "音声入力処理でエラーが発生しました",
                "詳細はログを確認してください",
            )
        finally:
            self._restore_recording_status()

    def _send_text_to_native_app(self, text, target_app=None, target_pid_value=None):
        pid = target_pid_value if target_pid_value and target_pid_value > 0 else app_target_pid(target_app)

        try:
            write_output(text, pid)
            log.info("ネイティブ貼り付けへ送信: pid=%s text=%r", pid, truncate_for_log(text, 300))
        except Exception:
            log.exception("ネイティブ貼り付けへの送信に失敗しました")
            copy_to_clipboard(text)
            notify_user("Voice Input", "貼り付けに失敗したためコピーしました", text[:100])

    def _text_for_insert(self, text):
        if not self.use_llm:
            return text

        self._set_status("correcting")
        try:
            corrected = llm_correct(text)
        except Exception as e:
            log.exception("LLM補正に失敗しました: %s", e)
            notify_user(
                "Voice Input",
                "LLM補正に失敗しました",
                str(e)[:100],
            )
            return ""

        log.info(f"LLM補正: {corrected}")
        return corrected

    def open_settings(self, sender=None):
        from Cocoa import NSApp
        # ウィンドウの作成・破棄を繰り返すとPyObjC側の参照管理と重なって
        # まれにネイティブクラッシュを起こすことがあるため、コントローラーは
        # 一度作成したらアプリ終了まで使い回す（閉じるときは非表示にするだけ）
        if self._settings_ctrl is None:
            from voice_input_tool.settings_ui import SettingsWindowController
            self._settings_ctrl = SettingsWindowController.alloc().initWithCallback_(self._on_settings_saved)
        self._settings_ctrl.show()
        NSApp.activateIgnoringOtherApps_(True)

    def toggle_llm(self, sender=None):
        """メニューから LLM 補正のON/OFFを切り替え、即時保存"""
        global APP_CONFIG
        self.use_llm = not self.use_llm
        self.llm_status.title = "LLM補正: ON" if self.use_llm else "LLM補正: OFF"
        APP_CONFIG["use_llm"] = self.use_llm
        try:
            save_config(APP_CONFIG)
        except Exception as e:
            log.error(f"設定保存に失敗しました: {e}")
        self._set_status(self._current_status(), force=True)

    def _on_settings_saved(self, new_config):
        global APP_CONFIG
        APP_CONFIG = new_config
        self.use_llm = new_config["use_llm"]
        configure_llm(new_config)
        self.llm_status.title = "LLM補正: ON" if self.use_llm else "LLM補正: OFF"
        if not self.is_recording:
            self._close_audio_stream()
        # ホットキー反映（リスナースレッドは再起動せず、判定用オブジェクトのみ差し替え）
        self._register_hotkey()
        self._set_status(self._current_status(), force=True)
        log.info(f"設定更新: LLM={'ON' if self.use_llm else 'OFF'}, "
                 f"バックエンド={current_backend()}, "
                 f"ホットキー={new_config.get('hotkey_record')}, "
                 f"入力マイク={new_config.get('input_device_id', '') or '自動選択'}")

    def quit_app(self, sender=None):
        self.stop_recording()
        self._stop_status_icon_animation()
        self._close_audio_stream()
        rumps.quit_application()


# ============================================================
# テストモード
# ============================================================

def run_test(recognizer, use_llm=False):
    """テストWAVファイルでASRを検証"""
    test_dir = os.path.join(MODEL_DIR, "test_wavs")
    transcript_path = os.path.join(test_dir, "transcript.txt")

    transcripts = {}
    if os.path.exists(transcript_path):
        with open(transcript_path, "r") as f:
            for line in f:
                parts = line.strip().split(maxsplit=1)
                if len(parts) == 2:
                    transcripts[parts[0]] = parts[1]

    print("=== テストモード ===\n")

    for i in range(1, 6):
        wav_path = os.path.join(test_dir, f"{i}.wav")
        if not os.path.exists(wav_path):
            continue

        with wave.open(wav_path, "rb") as wf:
            assert wf.getframerate() == SAMPLE_RATE
            assert wf.getnchannels() == 1
            raw = wf.readframes(wf.getnframes())
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

        start = time.time()
        text = recognize_speech(recognizer, samples)
        elapsed = time.time() - start

        expected = transcripts.get(f"{i}.wav", "")

        print(f"--- テスト {i} ({len(samples)/SAMPLE_RATE:.1f}s) ---")
        print(f"正解: {expected}")
        print(f"認識: {text}")
        print(f"時間: {elapsed:.2f}s")

        if use_llm:
            try:
                corrected = llm_correct(text)
                print(f"補正: {corrected}")
            except Exception as e:
                print(f"補正エラー: {e}")
        print()

    print("=== テスト完了 ===")


def run_settings_window():
    if not HAS_APP_HELPER:
        print("PyObjCTools が利用できないため設定画面を開けません。", file=sys.stderr)
        return 1

    from Cocoa import NSApplication
    from voice_input_tool.settings_ui import SettingsWindowController

    app = NSApplication.sharedApplication()
    ctrl = SettingsWindowController.alloc().initWithCallback_(lambda _config: AppHelper.stopEventLoop())
    ctrl.show()
    app.activateIgnoringOtherApps_(True)
    AppHelper.runEventLoop()
    return 0


def run_headless_app(use_llm=False):
    app = VoiceInputApp(use_llm=use_llm, headless=True, native_output=True)
    app._set_status("idle", force=True)

    try:
        ensure_bridge_files()
        command_reader = NativeCommandReader()
    except Exception:
        log.exception("コマンドファイルの準備に失敗しました")
        raise

    should_quit = threading.Event()

    def handle_signal(_signum, _frame):
        should_quit.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    log.info("ヘッドレス音声入力エンジン起動: command_file=%s", command_reader.path)
    if not native_paste_bridge_ready():
        request_accessibility_permission(prompt=True)

    try:
        while not should_quit.is_set():
            time.sleep(0.2)
            try:
                commands = command_reader.read_new_commands()
                if not commands:
                    continue
            except Exception:
                log.exception("コマンドファイルの読み込みに失敗しました")
                continue

            for command_line in commands:
                parsed_command = parse_command_line(command_line)
                if not parsed_command:
                    continue
                command = parsed_command["command"]
                command_target_pid = parsed_command["target_pid"]
                log.info("コマンド受信: %s target_pid=%s", command, command_target_pid)
                if command == "toggle":
                    app.toggle_recording(target_pid=command_target_pid)
                elif command == "start":
                    app.start_recording(target_pid=command_target_pid)
                elif command == "stop":
                    app.stop_recording()
                elif command == "toggle_llm":
                    app.toggle_llm()
                elif command == "quit":
                    should_quit.set()
                else:
                    log.warning("未知のコマンド: %s", command)
    finally:
        app.stop_recording()
        app._stop_status_icon_animation()
        app._close_audio_stream()
        log.info("ヘッドレス音声入力エンジン終了")


# ============================================================
# メイン
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Voice Input Tool - ReazonSpeech ASR")
    parser.add_argument("--llm", action="store_true", help="LLM句読点補正を有効化")
    parser.add_argument("--no-llm", action="store_true", help="LLM句読点補正を無効化")
    parser.add_argument("--llm-backend", choices=list(BACKENDS), help="整形バックエンドを一時的に切り替え")
    parser.add_argument("--test", action="store_true", help="テストWAVファイルで動作確認")
    parser.add_argument("--headless", action="store_true", help="メニューバーなしで音声入力エンジンのみ起動")
    parser.add_argument("--settings", action="store_true", help="設定画面だけを開く")
    args = parser.parse_args()

    use_llm = (args.llm or APP_CONFIG.get("use_llm", False)) and not args.no_llm

    if args.settings:
        sys.exit(run_settings_window())

    if args.llm_backend:
        APP_CONFIG["llm_backend"] = args.llm_backend
        configure_llm(APP_CONFIG)

    log.info(f"LLM補正: {'ON' if use_llm else 'OFF'} (バックエンド: {current_backend()})")

    if args.test:
        log.info("ASRモデル読み込み開始")
        start = time.time()
        recognizer = create_recognizer()
        log.info(f"ASRモデル読み込み完了 ({time.time()-start:.1f}s)")
        run_test(recognizer, use_llm=use_llm)
    elif args.headless:
        run_headless_app(use_llm=use_llm)
    else:
        install_status_bar_diagnostics()
        if HAS_APPKIT_APPLICATION:
            try:
                NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
                log.info("アプリ表示モード: accessory")
            except Exception:
                log.exception("アプリ表示モードの設定に失敗しました")
        if HAS_APP_HELPER:
            AppHelper.callLater(2.0, lambda: log.info("メニューバーイベントループ稼働中"))
        app = VoiceInputApp(use_llm=use_llm)
        log.info("メニューバーイベントループ開始")
        app.run()
        log.error("メニューバーイベントループが終了しました")


if __name__ == "__main__":
    main()

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
from voice_input_tool.caret_locator import locate_input_anchor
from voice_input_tool.config import DEFAULTS, load_config, save_config
from voice_input_tool.llm_correction import (
    BACKENDS,
    configure_llm,
    current_backend,
    final_polish_prompt,
    llm_correct,
)
from voice_input_tool.log_utils import truncate_for_log
from voice_input_tool.macos_text import (
    activate_application,
    copy_to_clipboard,
    get_frontmost_application,
    insert_text_at_cursor,
    request_accessibility_permission,
    running_application_for_pid,
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
from voice_input_tool.preview_panel import (
    STATE_LABELS as PANEL_STATES,
    PreviewPanelController,
    call_on_main,
)
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

# 録音停止を発話キューに積むための番兵。これより前に積まれた区間の処理が
# すべて終わってから全文整形（と確認ステップ）に入るために使う
SESSION_FINALIZE = object()

# --demo で流すテスト音源。区間の区切りが見えるよう、1 ファイルごとに無音を挟む
DEMO_WAV_NAMES = tuple(f"{i}.wav" for i in range(1, 6))
DEMO_START_DELAY_SECONDS = 3.0
# VAD の最小無音（0.5s）より長い無音を足して区間を確定させる
DEMO_SILENCE_SECONDS = 0.8
DEMO_SEGMENT_INTERVAL_SECONDS = 0.5


def _one_line(text):
    """LLM の整形結果を1行に畳む。

    プロンプトで改行を禁止しているが、モデルが従わないこともあるため
    パネルへ置く前にも落としておく（ユーザーが Shift+Enter で入れた改行は
    ここを通らないので潰れない）。
    """
    return " ".join(line.strip() for line in text.splitlines() if line.strip())


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
    def __init__(self, recognizer=None, use_llm=False, headless=False, native_output=False,
                 final_polish=False, demo=False):
        self._headless = headless
        self._native_output = native_output
        # --demo: ホットキーもマイクも使わず、テスト音源で入力フローを再現する
        self._demo = demo
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
        self.final_polish = final_polish
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
        # 1回の録音（開始〜確定/取消）を「セッション」と呼ぶ。
        # 発話ワーカー・録音操作スレッド・main thread（パネルのコールバック）の
        # 三者から触るため _session_lock で守り、世代番号で古い処理を捨てる
        self._session_lock = threading.Lock()
        self._session_parts = []
        self._session_target_app = None
        self._session_target_pid = None
        # 録音のたびに繰り上がる。整形中や入力位置探索中に次の録音が始まったことを
        # 検出するために使う
        self._session_generation = 0
        # 録音開始からパネルの確定/取消（または本文が空で閉じる）まで True
        self._session_active = False
        # 録音停止後、パネルで確認待ち（Enter/Esc 待ち）に入ったら True
        self._session_confirming = False
        # 全文整形が終わった（または不要だった）ら True。停止直後に区間処理の
        # 後始末が走ってもメニューバー表示が「整形中」から戻らないようにする
        self._session_polish_done = False
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
        llm_label = self._llm_menu_title()
        polish_label = self._final_polish_menu_title()
        if headless:
            self.record_button = _HeadlessMenuItem(f"録音開始 ({hotkey_display})")
            self.llm_status = _HeadlessMenuItem(llm_label)
            self.final_polish_status = _HeadlessMenuItem(polish_label)
            self.settings_button = _HeadlessMenuItem("設定...")
        else:
            self.record_button = rumps.MenuItem(
                f"録音開始 ({hotkey_display})", callback=self.toggle_recording
            )
            self.llm_status = rumps.MenuItem(llm_label, callback=self.toggle_llm)
            self.final_polish_status = rumps.MenuItem(
                polish_label, callback=self.toggle_final_polish
            )
            self.settings_button = rumps.MenuItem("設定...", callback=self.open_settings)

            self.menu = [
                self.record_button,
                None,
                self.llm_status,
                self.final_polish_status,
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

        # 認識結果のプレビューパネル。ウィンドウは一度だけ作って使い回す
        # （main thread で作る。呼び出し元は NSApplication を初期化済みであること）
        self._panel = None
        try:
            self._panel = PreviewPanelController.create(self._on_panel_confirm, self._on_panel_cancel)
        except Exception:
            log.exception("プレビューパネルの作成に失敗しました")

        # ホットキー登録（--demo では別インスタンスと衝突するので登録しない。
        # ヘッドレスはネイティブアプリ側が Carbon で登録するため、二重トグルを避けて登録しない）
        if not demo and not headless:
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

    # ------------------------------------------------------------
    # ステータス表示（メニューバー／ネイティブ／プレビューパネル）
    # ------------------------------------------------------------

    def _set_status(self, status, force=False):
        self._status_controller.set(status, force=force)
        # パネルにも同じ状態を映す。idle/inserting はパネルの状態ではないので触らない
        # （AppStatusController は同じ状態を間引くが、パネルは独立に更新する）
        if status in PANEL_STATES:
            call_on_main(self._panel_set_state, status)

    def _stop_status_icon_animation(self):
        self._status_controller.stop_icon_animation()

    def _current_status(self):
        return self._status_controller.current()

    def _restore_recording_status(self):
        """区間処理などの一時的な状態表示から、いまの本来の状態へ戻す"""
        if self.is_recording:
            status = "hearing" if self._is_speech_active else "listening"
        else:
            with self._session_lock:
                confirming = self._session_active and self._session_confirming
                polishing = self.final_polish and not self._session_polish_done
            if confirming:
                status = "polishing" if polishing else "confirm"
            else:
                status = "idle"
        self._set_status(status)

    def _llm_menu_title(self):
        return "LLM補正（発話ごと）: ON" if self.use_llm else "LLM補正（発話ごと）: OFF"

    def _final_polish_menu_title(self):
        return "文章整形（停止後）: ON" if self.final_polish else "文章整形（停止後）: OFF"

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
        if self._demo:
            # デモは音源を _feed_audio へ直接流すのでマイクを開かない
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

    # ------------------------------------------------------------
    # 録音の開始/停止
    # ------------------------------------------------------------

    def toggle_recording(self, sender=None, target_pid=None):
        with self._recording_lock:
            if self.is_recording:
                self.stop_recording()
            else:
                self.start_recording(target_pid=target_pid)

    def _resolve_target(self, target_pid):
        """入力先アプリを決める。

        ネイティブ経由なら渡された pid。それ以外は前面アプリだが、確認待ちの
        パネルがキーになった影響で自プロセスが前面のときは、直前のセッションの
        入力先を引き継ぐ（自分自身に入力しても意味がないため）。
        """
        if target_pid and target_pid > 0:
            return None, target_pid
        front = get_frontmost_application()
        if app_target_pid(front) == os.getpid():
            with self._session_lock:
                previous_app = self._session_target_app
                previous_pid = self._session_target_pid
            if previous_app is not None or previous_pid:
                log.info("前面が自プロセスのため直前の入力先を引き継ぎます: pid=%s",
                         previous_pid or app_target_pid(previous_app))
                return previous_app, previous_pid
            return None, None
        return front, None

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
            self._target_app, self._target_pid = self._resolve_target(target_pid)
            with self._session_lock:
                # 確認待ちのまま次の録音が始まった場合、前の本文を失わないよう
                # クリップボードへ退避してからパネルを使い回す
                stash_previous = self._session_active and self._session_confirming
                previous_generation = self._session_generation
                self._session_parts = []
                self._session_target_app = self._target_app
                self._session_target_pid = self._target_pid
                self._session_generation += 1
                self._session_active = True
                self._session_confirming = False
                self._session_polish_done = False
                generation = self._session_generation
            if stash_previous:
                call_on_main(self._stash_pending_text, previous_generation)
            self._has_audio_started = False
            self._start_requested_at = time.time()
            self._set_status("starting")
            resolved_pid = self._target_pid or app_target_pid(self._target_app)
            log.info("録音開始要求: target_pid=%s generation=%d", resolved_pid, generation)

            self.is_recording = True
            if not self._ensure_audio_stream():
                self.is_recording = False
                self._has_audio_started = False
                self._start_requested_at = None
                with self._session_lock:
                    self._session_active = False
                self._set_status("idle")
                return

            # 入力位置の取得（AX API）は最大 1 秒ブロックし得るので、ホットキー・
            # メニュー・ヘッドレスのどの経路から来ても別スレッドで行う
            threading.Thread(
                target=self._locate_and_show_panel,
                args=(resolved_pid, generation),
                daemon=True,
            ).start()

    def stop_recording(self):
        with self._recording_lock:
            if not self.is_recording:
                return
            self.is_recording = False
            self._has_audio_started = False
            self._start_requested_at = None

            with self._vad_lock:
                self.vad.flush()
                self._drain_vad_segments_locked(is_final=True)

            with self._session_lock:
                generation = self._session_generation
                if self._had_any_segment:
                    self._session_confirming = True
                    self._session_polish_done = not self.final_polish

            if not self._had_any_segment:
                log.info("録音停止: 音声データなし")
                call_on_main(self._end_session, generation)
                return

            # 最終区間より後ろに積むことで、その認識が終わってから整形・確認に進む
            self._segment_queue.put(SESSION_FINALIZE)
            # 区間の認識を待たずにパネルを編集可にし、Enter/Esc を受け付ける
            call_on_main(self._panel_begin_confirm, generation)

    def _audio_callback(self, indata, frames, time_info, status):
        if status:
            log.warning(f"オーディオステータス: {status}")
        if not self.is_recording:
            return
        self._feed_audio(indata[:, 0].astype(np.float32))

    def _feed_audio(self, samples):
        """録音中の音声ブロックを VAD へ流す（マイクのコールバックと --demo が共用）"""
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
            self._drain_vad_segments_locked()

    def _drain_vad_segments_locked(self, is_final=False):
        """VAD が確定させた発話区間をすべて取り出してキューへ渡す。

        呼び出し元で self._vad_lock を保持していること。
        """
        with self._session_lock:
            generation = self._session_generation
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
            log.info("%s: %.1f秒 generation=%d", label, duration, generation)
            self._segment_queue.put((_apply_fade(samples), generation))

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
            item = self._segment_queue.get()
            if item is SESSION_FINALIZE:
                self._finalize_session()
                continue
            samples, generation = item
            self._handle_speech_segment(samples, generation)

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

    # ------------------------------------------------------------
    # 発話区間の認識 → パネルへ追記
    # ------------------------------------------------------------

    def _handle_speech_segment(self, speech_samples, generation):
        try:
            if not self._session_is_current(generation):
                log.info("次の録音が始まっているため古い区間を破棄しました")
                return
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

            # 入力欄へは打ち込まず、パネルに積む。実際の入力は確定（Enter）時にまとめて行う
            if self._record_session_text(text, generation):
                call_on_main(self._panel_append, text, generation)
        except Exception:
            log.exception("発話セグメント処理で予期しないエラーが発生しました")
            notify_user(
                "Voice Input",
                "音声入力処理でエラーが発生しました",
                "詳細はログを確認してください",
            )
        finally:
            self._restore_recording_status()

    def _session_is_current(self, generation):
        with self._session_lock:
            return self._session_active and self._session_generation == generation

    def _record_session_text(self, text, generation):
        """この回の録音で認識した内容を覚えておく。区間同士は単純連結する。

        次の録音が始まっていたら（世代が違えば）捨てて False を返す。
        """
        if not text:
            return False
        with self._session_lock:
            if not self._session_active or self._session_generation != generation:
                return False
            self._session_parts.append(text)
            return True

    def _finalize_session(self):
        """録音停止後、全区間の認識が終わった時点で全文を整形し、確認待ちへ進める"""
        with self._session_lock:
            text = "".join(self._session_parts)
            generation = self._session_generation
            active = self._session_active

        if not active:
            return
        if not text:
            log.info("録音停止: 認識結果が空のためパネルを閉じます")
            call_on_main(self._end_session, generation)
            return

        if not self.final_polish:
            self._set_status("confirm")
            return

        try:
            self._set_status("polishing")
            start = time.time()
            polished = _one_line(
                llm_correct(text, prompt=final_polish_prompt(), long_form=True)
            )
            log.info(
                "全文整形 (%.2fs): %r -> %r",
                time.time() - start,
                truncate_for_log(text, 300),
                truncate_for_log(polished, 300),
            )
            if polished and polished != text:
                call_on_main(self._panel_apply_polish, text, polished, generation)
            else:
                log.info("全文整形: 変更なし")
        except Exception as e:
            log.exception("全文整形に失敗しました")
            notify_user("Voice Input", "文章の整形に失敗しました", str(e)[:100])
        finally:
            with self._session_lock:
                if self._session_generation == generation:
                    self._session_polish_done = True
            self._restore_recording_status()

    # ------------------------------------------------------------
    # プレビューパネル操作（すべて main thread で実行される）
    # ------------------------------------------------------------

    def _locate_and_show_panel(self, target_pid, generation):
        """入力位置を調べてパネルを表示する（ワーカースレッド）"""
        try:
            anchor = locate_input_anchor(target_pid)
        except Exception:
            log.exception("入力位置の取得に失敗しました")
            return
        call_on_main(self._panel_show, anchor, generation)

    def _panel_show(self, anchor, generation):
        if self._panel is None or not self._session_is_current(generation):
            return
        try:
            self._panel.show_near(anchor)
            # 入力位置の探索中に届いた区間や状態変化を反映する（show_near は本文を空にする）
            with self._session_lock:
                text = "".join(self._session_parts)
                confirming = self._session_confirming
            if text:
                self._panel.set_text(text)
            status = self._current_status()
            if confirming:
                # 短い録音では表示前に停止まで済んでいることがある
                self._panel.activate_for_input()
                if status not in ("polishing", "confirm"):
                    status = "polishing" if self.final_polish else "confirm"
            if status in PANEL_STATES:
                self._panel.set_state(status)
        except Exception:
            log.exception("プレビューパネルの表示に失敗しました")

    def _panel_set_state(self, status):
        if self._panel is None or not self._panel.is_visible():
            return
        try:
            self._panel.set_state(status)
        except Exception:
            log.exception("プレビューパネルの状態更新に失敗しました")

    def _panel_append(self, text, generation):
        if self._panel is None or not self._session_is_current(generation):
            return
        if not self._panel.is_visible():
            # まだ入力位置の探索中。表示時に _session_parts からまとめて反映する
            return
        try:
            self._panel.append_text(text)
        except Exception:
            log.exception("プレビューパネルへの追記に失敗しました")

    def _panel_begin_confirm(self, generation):
        """録音停止直後: パネルを編集可・キーにし、Enter/Esc を受け付ける"""
        if self._panel is None or not self._session_is_current(generation):
            return
        if not self._panel.is_visible():
            # 表示前に停止した。_panel_show 側で activate_for_input する
            return
        try:
            self._panel.activate_for_input()
        except Exception:
            log.exception("プレビューパネルの編集開始に失敗しました")
        self._set_status("polishing" if self.final_polish else "confirm")

    def _panel_apply_polish(self, original, polished, generation):
        """整形結果をパネルへ反映する。ユーザーが既に編集していたら捨てる"""
        if self._panel is None or not self._session_is_current(generation):
            log.info("全文整形: セッションが終わっているため結果を捨てました")
            return
        current = self._panel.current_text()
        if current != original:
            log.info("全文整形: パネルの本文が編集されているため結果を捨てました")
            return
        try:
            self._panel.set_text(polished)
        except Exception:
            log.exception("整形結果の反映に失敗しました")

    def _stash_pending_text(self, generation):
        """確認待ちのまま次の録音が始まったとき、前の本文をクリップボードへ退避する"""
        if self._panel is None:
            return
        text = self._panel.current_text()
        if text:
            copy_to_clipboard(text)
            log.info("確認待ちの本文をクリップボードへ退避しました (generation=%d, %d 文字)",
                     generation, len(text))

    def _end_session(self, generation):
        """本文が空のまま終わったセッションを閉じる"""
        with self._session_lock:
            if self._session_generation != generation or not self._session_active:
                return
            self._session_active = False
            self._session_confirming = False
        self._hide_panel_and_refocus()
        self._set_status("idle")

    def _hide_panel_and_refocus(self):
        if self._panel is None:
            return
        try:
            reactivate = self._panel.did_activate_app()
            self._panel.hide()
        except Exception:
            log.exception("プレビューパネルを閉じられませんでした")
            return
        if reactivate:
            # キーになるために自プロセスをアクティブ化した環境では、対象アプリへ戻す
            with self._session_lock:
                target_app = self._session_target_app
                target_pid = self._session_target_pid
            activate_application(target_app or running_application_for_pid(target_pid))

    def _on_panel_confirm(self, text):
        """Enter: パネルの本文を入力先へ入力する（main thread）"""
        # 遅れると次の Return が入力欄への改行になるため、まず隠す
        if self._panel is not None:
            self._panel.hide()
        with self._session_lock:
            if not self._session_active:
                return
            self._session_active = False
            self._session_confirming = False
            target_app = self._session_target_app
            target_pid = self._session_target_pid
        if not text:
            log.info("確定: 本文が空のため入力しません")
            self._hide_panel_and_refocus()
            self._set_status("idle")
            return
        self._set_status("inserting")
        # 文字入力は 1 文字ごとに眠るので main thread では回さない
        threading.Thread(
            target=self._insert_confirmed_text,
            args=(text, target_app, target_pid),
            daemon=True,
        ).start()

    def _insert_confirmed_text(self, text, target_app, target_pid):
        try:
            if self._native_output:
                self._send_text_to_native_app(text, target_app, target_pid)
                if not native_paste_bridge_ready():
                    log.warning("ネイティブ貼り付け受信側が未起動のためPython側で貼り付けます")
                    app = target_app or running_application_for_pid(target_pid)
                    if insert_text_at_cursor(text, app):
                        log.info("Python経由でカーソル位置に入力しました")
                    else:
                        notify_user(
                            "Voice Input",
                            "アクセシビリティ許可が必要です",
                            "Python または VoiceInputTool を許可してください",
                        )
                return

            # pid だけ分かっている場合（--demo や引き継ぎ）も対象アプリを前面へ戻してから打つ
            app = target_app or running_application_for_pid(target_pid)
            if insert_text_at_cursor(text, app):
                log.info("カーソル位置に入力しました (%d 文字)", len(text))
            else:
                # insert_text_at_cursor は先にクリップボードへ入れているので ⌘V で貼れる
                notify_user("Voice Input", "貼り付け不可のためコピーしました", text[:100])
        except Exception:
            log.exception("確定テキストの入力で予期しないエラーが発生しました")
            copy_to_clipboard(text)
            notify_user("Voice Input", "入力に失敗したためコピーしました", text[:100])
        finally:
            self._restore_recording_status()

    def _on_panel_cancel(self):
        """Esc: 入力せずに閉じる。本文は失わないようクリップボードへ残す（main thread）"""
        text = self._panel.current_text() if self._panel is not None else ""
        with self._session_lock:
            self._session_active = False
            self._session_confirming = False
        self._hide_panel_and_refocus()
        if text:
            copy_to_clipboard(text)
            log.info("取消: 本文をクリップボードへコピーしました (%d 文字)", len(text))
        self._set_status("idle")

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

    # ------------------------------------------------------------
    # --demo: テスト音源で入力フローを再現する
    # ------------------------------------------------------------

    def run_demo_session(self, target_pid=None):
        """前面アプリを入力先にして録音を開始し、テスト音源を流して停止まで進める（ワーカースレッド）

        target_pid は起動時点の前面アプリ。rumps が run() で自プロセスをアクティブ化するため、
        開始時点の前面アプリ（自分自身）ではなく起動前に見えていたアプリを入力先にする。
        """
        test_dir = os.path.join(MODEL_DIR, "test_wavs")
        self.start_recording(target_pid=target_pid)
        if not self.is_recording:
            log.error("デモ: 録音を開始できませんでした")
            return
        silence = np.zeros(int(DEMO_SILENCE_SECONDS * SAMPLE_RATE), dtype=np.float32)
        for name in DEMO_WAV_NAMES:
            wav_path = os.path.join(test_dir, name)
            if not os.path.exists(wav_path):
                log.warning("デモ: 音源が見つかりません: %s", wav_path)
                continue
            with wave.open(wav_path, "rb") as wf:
                raw = wf.readframes(wf.getnframes())
                samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            log.info("デモ: %s (%.1f秒) を入力", name, len(samples) / SAMPLE_RATE)
            for block in (samples, silence):
                for offset in range(0, len(block), BLOCK_SIZE):
                    if not self.is_recording:
                        return
                    self._feed_audio(block[offset:offset + BLOCK_SIZE])
            time.sleep(DEMO_SEGMENT_INTERVAL_SECONDS)
        self.stop_recording()
        log.info("デモ: 録音停止。パネルで Enter / Esc を押してください")

    # ------------------------------------------------------------
    # メニュー操作
    # ------------------------------------------------------------

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
        """メニューから発話ごとの LLM 補正のON/OFFを切り替え、即時保存"""
        global APP_CONFIG
        self.use_llm = not self.use_llm
        self.llm_status.title = self._llm_menu_title()
        APP_CONFIG["use_llm"] = self.use_llm
        try:
            save_config(APP_CONFIG)
        except Exception as e:
            log.error(f"設定保存に失敗しました: {e}")
        self._set_status(self._current_status(), force=True)

    def toggle_final_polish(self, sender=None):
        """メニューから録音停止後の全文整形のON/OFFを切り替え、即時保存"""
        global APP_CONFIG
        self.final_polish = not self.final_polish
        self.final_polish_status.title = self._final_polish_menu_title()
        APP_CONFIG["final_polish"] = self.final_polish
        try:
            save_config(APP_CONFIG)
        except Exception as e:
            log.error(f"設定保存に失敗しました: {e}")

    def _on_settings_saved(self, new_config):
        global APP_CONFIG
        APP_CONFIG = new_config
        self.use_llm = new_config["use_llm"]
        self.final_polish = new_config.get("final_polish", False)
        configure_llm(new_config)
        self.llm_status.title = self._llm_menu_title()
        self.final_polish_status.title = self._final_polish_menu_title()
        if not self.is_recording:
            self._close_audio_stream()
        # ホットキー反映（リスナースレッドは再起動せず、判定用オブジェクトのみ差し替え）
        if not self._demo and not self._headless:
            self._register_hotkey()
        self._set_status(self._current_status(), force=True)
        log.info(f"設定更新: LLM={'ON' if self.use_llm else 'OFF'}, "
                 f"全文整形={'ON' if self.final_polish else 'OFF'}, "
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


def _configure_accessory_app():
    """メニューバー常駐アプリとして NSApplication を初期化する（Dock に出さない）。

    プレビューパネル（NSPanel）を作る前に呼ぶこと。
    """
    if not HAS_APPKIT_APPLICATION:
        return
    try:
        NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        log.info("アプリ表示モード: accessory")
    except Exception:
        log.exception("アプリ表示モードの設定に失敗しました")


# ネイティブアプリからのコマンドファイルをポーリングする間隔（秒）
HEADLESS_POLL_INTERVAL = 0.2


def run_headless_app(use_llm=False, final_polish=False):
    """ネイティブアプリの子プロセスとして動くモード。

    プレビューパネルを描くために AppKit のランループが必要なので、
    while/sleep ではなく NSApplication + NSTimer でコマンドファイルを監視する。
    """
    from Foundation import NSTimer

    _configure_accessory_app()
    app = VoiceInputApp(
        use_llm=use_llm, headless=True, native_output=True, final_polish=final_polish
    )
    app._set_status("idle", force=True)

    try:
        ensure_bridge_files()
        command_reader = NativeCommandReader()
    except Exception:
        log.exception("コマンドファイルの準備に失敗しました")
        raise

    should_quit = threading.Event()

    def handle_signal(_signum, _frame):
        # Python のシグナルハンドラは main thread が Python コードを実行するときに
        # 走る。NSTimer が定期的に Python へ戻ってくるので、ここでは印だけ付ける
        should_quit.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    log.info("ヘッドレス音声入力エンジン起動: command_file=%s", command_reader.path)
    if not native_paste_bridge_ready():
        request_accessibility_permission(prompt=True)

    def handle_commands():
        commands = command_reader.read_new_commands()
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
            elif command == "toggle_final_polish":
                app.toggle_final_polish()
            elif command == "quit":
                should_quit.set()
            else:
                log.warning("未知のコマンド: %s", command)

    shutdown_done = threading.Event()

    def shutdown():
        # AppHelper.stopEventLoop() は NSApp.terminate_ で即プロセスを終えるため
        # （runEventLoop から戻ってこない。実測で finally が走らなかった）、
        # 後始末はイベントループを止める前にここで済ませる
        if shutdown_done.is_set():
            return
        shutdown_done.set()
        poll_timer.invalidate()
        try:
            app.stop_recording()
            app._stop_status_icon_animation()
            app._close_audio_stream()
        except Exception:
            log.exception("ヘッドレス音声入力エンジンの後始末に失敗しました")
        log.info("ヘッドレス音声入力エンジン終了")

    def tick(_timer):
        # 例外を外へ出すと AppHelper.runEventLoop がエラーダイアログを出して
        # 子プロセスが止まってしまうので、ここで必ず握りつぶす
        try:
            if not should_quit.is_set():
                handle_commands()
        except Exception:
            log.exception("コマンドファイルの処理に失敗しました")
        if should_quit.is_set():
            shutdown()
            AppHelper.stopEventLoop()

    poll_timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
        HEADLESS_POLL_INTERVAL, True, tick
    )
    try:
        AppHelper.runEventLoop()
    finally:
        shutdown()


def run_menu_bar_app(use_llm=False, final_polish=False, demo=False):
    install_status_bar_diagnostics()
    _configure_accessory_app()
    if HAS_APP_HELPER:
        AppHelper.callLater(2.0, lambda: log.info("メニューバーイベントループ稼働中"))
    app = VoiceInputApp(use_llm=use_llm, final_polish=final_polish, demo=demo)
    if demo:
        # rumps.App.run() は自プロセスをアクティブ化するので、起動時点の前面アプリを先に控える
        demo_target_pid = app_target_pid(get_frontmost_application())
        if demo_target_pid == os.getpid():
            demo_target_pid = None
        log.info("デモモード: %.0f 秒後に前面アプリ (pid=%s) を入力先としてテスト音源を流します",
                 DEMO_START_DELAY_SECONDS, demo_target_pid)
        AppHelper.callLater(
            DEMO_START_DELAY_SECONDS,
            lambda: threading.Thread(
                target=app.run_demo_session, args=(demo_target_pid,), daemon=True
            ).start(),
        )
    log.info("メニューバーイベントループ開始")
    app.run()
    log.error("メニューバーイベントループが終了しました")


# ============================================================
# メイン
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Voice Input Tool - ReazonSpeech ASR")
    parser.add_argument("--llm", action="store_true", help="発話ごとのLLM句読点補正を有効化")
    parser.add_argument("--no-llm", action="store_true", help="発話ごとのLLM句読点補正を無効化")
    parser.add_argument("--final-polish", action="store_true", help="録音停止後の全文整形を有効化")
    parser.add_argument("--no-final-polish", action="store_true", help="録音停止後の全文整形を無効化")
    parser.add_argument("--llm-backend", choices=list(BACKENDS), help="整形バックエンドを一時的に切り替え")
    parser.add_argument("--test", action="store_true", help="テストWAVファイルで動作確認")
    parser.add_argument("--headless", action="store_true", help="メニューバーなしで音声入力エンジンのみ起動")
    parser.add_argument("--settings", action="store_true", help="設定画面だけを開く")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="ホットキー・マイクを使わず、テスト音源でプレビューパネルの入力フローを再現",
    )
    args = parser.parse_args()

    use_llm = (args.llm or APP_CONFIG.get("use_llm", False)) and not args.no_llm
    final_polish = (
        args.final_polish or APP_CONFIG.get("final_polish", False)
    ) and not args.no_final_polish

    if args.settings:
        sys.exit(run_settings_window())

    if args.llm_backend:
        APP_CONFIG["llm_backend"] = args.llm_backend
        configure_llm(APP_CONFIG)

    log.info(
        f"LLM補正: {'ON' if use_llm else 'OFF'}, "
        f"全文整形: {'ON' if final_polish else 'OFF'} (バックエンド: {current_backend()})"
    )

    if args.test:
        log.info("ASRモデル読み込み開始")
        start = time.time()
        recognizer = create_recognizer()
        log.info(f"ASRモデル読み込み完了 ({time.time()-start:.1f}s)")
        run_test(recognizer, use_llm=use_llm)
    elif args.headless:
        run_headless_app(use_llm=use_llm, final_polish=final_polish)
    else:
        run_menu_bar_app(use_llm=use_llm, final_polish=final_polish, demo=args.demo)


if __name__ == "__main__":
    main()

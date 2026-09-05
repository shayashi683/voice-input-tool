"""Status label, native status, and menu-bar indicator handling."""

import logging
import os
import threading

from voice_input_tool.app_paths import TYPING_INDICATOR_ICON_FRAMES
from voice_input_tool.native_bridge import write_status

log = logging.getLogger("voice_input")

TYPING_INDICATOR_ANIMATION_INTERVAL = 0.12


class HeadlessTimer:
    def is_alive(self):
        return False

    def start(self):
        pass

    def stop(self):
        pass


class AppStatusController:
    def __init__(
        self,
        app,
        record_button,
        get_hotkey_display,
        use_llm,
        headless=False,
        call_after=None,
        timer_factory=None,
    ):
        self.app = app
        self.record_button = record_button
        self.get_hotkey_display = get_hotkey_display
        self.use_llm = use_llm
        self.headless = headless
        self.call_after = call_after
        self.status = "idle"
        self.lock = threading.Lock()
        self.version = 0
        self.icon_frames = None
        self.icon_frame_index = 0
        self.icon_timer = (
            HeadlessTimer()
            if headless
            else timer_factory(self.advance_icon, TYPING_INDICATOR_ANIMATION_INTERVAL)
        )

    def set(self, status, force=False):
        with self.lock:
            if not force and self.status == status:
                return
            self.status = status
            self.version += 1
            version = self.version

        if self.headless or threading.current_thread() is threading.main_thread() or self.call_after is None:
            self.apply(version)
        else:
            self.call_after(self.apply, version)

    def apply(self, version=None):
        with self.lock:
            if version is not None and version != self.version:
                return
            status = self.status

        title, record_title, icon_frames = self.labels(status)
        write_status(status, title, record_title, self.use_llm())
        self.set_menu_bar_indicator(title, icon_frames)
        self.record_button.title = record_title

    def current(self):
        with self.lock:
            return self.status

    def restore_recording_status(self, is_recording, is_speech_active=False):
        if not is_recording:
            self.set("idle")
        else:
            # セグメント処理中も発話が続いている場合は「入力中」表示を維持する
            self.set("hearing" if is_speech_active else "listening")

    def set_menu_bar_indicator(self, title, icon_frames=None):
        if self.headless:
            self.app._headless_title = title
            return

        if icon_frames:
            missing_icons = [icon for icon in icon_frames if not os.path.exists(icon)]
            if not missing_icons:
                if self.icon_frames != icon_frames:
                    self.icon_frames = icon_frames
                    self.icon_frame_index = 0
                    self.app.icon = icon_frames[0]
                self.app.title = title
                if not self.icon_timer.is_alive():
                    self.icon_timer.start()
                return

            log.error("入力中アイコンが見つかりません: %s", ", ".join(missing_icons))
            title = title or "•••"

        self.stop_icon_animation()
        self.app.title = title
        self.app.icon = None

    def advance_icon(self, _sender=None):
        icon_frames = self.icon_frames
        if not icon_frames or self.current() != "hearing":
            self.stop_icon_animation()
            return

        self.icon_frame_index = (self.icon_frame_index + 1) % len(icon_frames)
        self.app.icon = icon_frames[self.icon_frame_index]

    def stop_icon_animation(self):
        if self.icon_timer.is_alive():
            self.icon_timer.stop()
        self.icon_frames = None
        self.icon_frame_index = 0

    def labels(self, status):
        hotkey_display = self.get_hotkey_display()
        states = {
            "idle": ("🎙", f"録音開始 ({hotkey_display})", None),
            "starting": ("⏳", f"マイク起動中… ({hotkey_display})", None),
            "listening": ("🟢", f"録音停止・入力待機中 ({hotkey_display})", None),
            "hearing": ("", f"録音停止・音声入力中… ({hotkey_display})", TYPING_INDICATOR_ICON_FRAMES),
            "processing": ("📝", "音声認識中…", None),
            "correcting": ("🧠", "LLM補正中…", None),
            "polishing": ("✨", "文章を整形中…", None),
            # パネルで Enter/Esc を待っている。この間にホットキーを押すと新しい録音が始まる
            "confirm": ("✅", f"確認待ち・録音開始 ({hotkey_display})", None),
            "inserting": ("⌨️", "カーソル位置へ入力中…", None),
        }
        return states.get(status, states["idle"])

"""入力欄近くに出す認識結果プレビューのフローティングパネル (PyObjC)

録音中は対象アプリのフォーカスを奪わずに認識結果を表示し、録音停止後に
キー入力（Enter/Esc）を受け取って確定・取消を呼び出し側へ通知する。
全メソッドは main thread から呼ぶこと（別スレッドからは call_on_main を使う）。
"""
import logging
import math
import threading

import objc
from Cocoa import (
    NSApp,
    NSAttributedString,
    NSBackingStoreBuffered,
    NSColor,
    NSEventModifierFlagOption,
    NSEventModifierFlagShift,
    NSFloatingWindowLevel,
    NSFont,
    NSFontWeightSemibold,
    NSForegroundColorAttributeName,
    NSMakeRect,
    NSMakeSize,
    NSObject,
    NSPanel,
    NSScreen,
    NSScrollView,
    NSTextField,
    NSTextView,
    NSTimer,
    NSView,
    NSViewHeightSizable,
    NSViewWidthSizable,
    NSVisualEffectBlendingModeBehindWindow,
    NSVisualEffectMaterialPopover,
    NSVisualEffectView,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
)
from PyObjCTools import AppHelper

log = logging.getLogger("voice_input")

# ------------------------------------------------------------
# 見た目の定数
# ------------------------------------------------------------
PANEL_WIDTH = 460
CORNER_RADIUS = 12
PADDING = 12            # パネル外周の余白
HEADER_HEIGHT = 20
FOOTER_HEIGHT = 16
ROW_GAP = 6             # ヘッダー/本文/フッターの間隔
BODY_FONT_SIZE = 14
MIN_LINES = 1
MAX_LINES = 8
# anchor とパネルの隙間。TextEdit はキャレット矢印が実際の行より約 18pt 上に返るため、
# 8pt だとパネル上端がカレント行に重なる。少し広げて行を隠さないようにする
ANCHOR_GAP = 14
PULSE_INTERVAL = 0.12   # hearing のドット脈動間隔（秒）

# 「対象アプリをアクティブにせず（NonactivatingPanel）パネルだけキーにする」方式。
# 実測（probe_panel5.py）で、Chrome を前面に残したまま HID キーが
# textView:doCommandBySelector: に 5/5 回届いたのでこれを主方式にする。
# 環境によりキーになれない場合だけ NSApp.activateIgnoringOtherApps_ に落とす。
ACTIVATE_APP_FOR_INPUT = False

# 状態 → ヘッダー表示。hearing のドットは pulse で描き替える
STATE_LABELS = {
    "starting": "⏳ マイク起動中…",
    "listening": "🎙 話してください",
    "hearing": "● 聞き取り中…",
    "processing": "📝 認識中…",
    "correcting": "🧠 補正中…",
    "polishing": "✨ 文章を整形中…",
    "confirm": "✅ 確認して Enter",
}
# 状態 → フッターのキー操作ヒント（無い状態ではフッター行を畳む）
FOOTER_HINTS = {
    "polishing": "Enter でそのまま入力 ／ Esc で取消",
    "confirm": "Enter で入力 ／ Shift+Enter で改行 ／ Esc で取消",
}


def call_on_main(fn, *args):
    """main thread なら即時実行、そうでなければ AppKit ランループへ回す"""
    if threading.current_thread() is threading.main_thread():
        fn(*args)
    else:
        AppHelper.callAfter(fn, *args)


class _PreviewPanel(NSPanel):
    """Borderless パネルは既定でキーになれないため、サブクラスで許可する"""

    def canBecomeKeyWindow(self):
        return True

    def canBecomeMainWindow(self):
        # メインウィンドウになると対象アプリ側の見え方に影響し得るので避ける
        return False


class _PreviewTextView(NSTextView):
    """Return/Esc は delegate で処理するので、ここでは特別なことをしない。
    将来キー処理を足す場所を明示するためのサブクラス。"""

    def acceptsFirstResponder(self):
        return True


def _make_label(font, color):
    label = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 10))
    label.setBezeled_(False)
    label.setDrawsBackground_(False)
    label.setEditable_(False)
    label.setSelectable_(False)
    label.setFont_(font)
    label.setTextColor_(color)
    label.setLineBreakMode_(4)  # NSLineBreakByTruncatingTail
    return label


class PreviewPanelController(NSObject):
    """プレビューパネル。settings_ui と同じくウィンドウは一度だけ作って使い回す"""

    panel = objc.ivar()
    effect_view = objc.ivar()
    header_label = objc.ivar()
    footer_label = objc.ivar()
    scroll_view = objc.ivar()
    text_view = objc.ivar()
    pulse_timer = objc.ivar()
    on_confirm = objc.ivar()
    on_cancel = objc.ivar()
    state = objc.ivar()
    input_active = objc.ivar()
    pulse_phase = objc.ivar()
    # 配置の基準。伸縮時に上端（反転時は下端）を固定するために覚えておく
    anchor_edge_y = objc.ivar()
    flipped = objc.ivar()
    screen_visible_frame = objc.ivar()  # (x, y, w, h) のタプル
    activated_app = objc.ivar()

    @classmethod
    @objc.python_method
    def create(cls, on_confirm, on_cancel):
        """on_confirm(text) / on_cancel() は main thread で呼ばれる"""
        return cls.alloc().initWithConfirm_cancel_(on_confirm, on_cancel)

    def initWithConfirm_cancel_(self, on_confirm, on_cancel):
        self = objc.super(PreviewPanelController, self).init()
        if self is None:
            return None
        self.on_confirm = on_confirm
        self.on_cancel = on_cancel
        self.state = ""
        self.input_active = False
        self.pulse_phase = 0
        self.anchor_edge_y = 0.0
        self.flipped = False
        self.screen_visible_frame = None
        self.activated_app = False
        self.pulse_timer = None
        self._build_panel()
        return self

    # ------------------------------------------------------------
    # 構築
    # ------------------------------------------------------------
    @objc.python_method
    def _build_panel(self):
        height = self._panel_height_for_text_height(self._line_height() * MIN_LINES)
        self.panel = _PreviewPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, PANEL_WIDTH, height),
            NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
            NSBackingStoreBuffered,
            False,
        )
        self.panel.setLevel_(NSFloatingWindowLevel)
        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(NSColor.clearColor())
        self.panel.setHasShadow_(True)
        self.panel.setHidesOnDeactivate_(False)
        self.panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorFullScreenAuxiliary
        )
        # 対象アプリの操作を邪魔しないよう、Cmd+W 等の標準操作対象にならない
        self.panel.setExcludedFromWindowsMenu_(True)

        # 背景: 角丸のぼかし素材（ライト/ダークは素材が自動追従）
        self.effect_view = NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, PANEL_WIDTH, height))
        self.effect_view.setMaterial_(NSVisualEffectMaterialPopover)
        self.effect_view.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        self.effect_view.setState_(1)  # NSVisualEffectStateActive: 非アクティブでも薄くならない
        self.effect_view.setWantsLayer_(True)
        self.effect_view.layer().setCornerRadius_(CORNER_RADIUS)
        self.effect_view.layer().setMasksToBounds_(True)
        self.effect_view.layer().setBorderWidth_(1.0)
        self.effect_view.layer().setBorderColor_(NSColor.separatorColor().CGColor())
        self.panel.setContentView_(self.effect_view)
        # 背後がカラフルな画面（グラフ等）だとぼかしが強く色を拾って文字が読みにくいので、
        # 半透明のウィンドウ背景色を一枚重ねて色移りを抑える（ライト/ダークは色が自動追従）
        backing = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, PANEL_WIDTH, height))
        backing.setWantsLayer_(True)
        backing.layer().setBackgroundColor_(NSColor.windowBackgroundColor().colorWithAlphaComponent_(0.6).CGColor())
        backing.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        self.effect_view.addSubview_(backing)

        self.header_label = _make_label(
            NSFont.systemFontOfSize_weight_(13, NSFontWeightSemibold), NSColor.labelColor()
        )
        self.effect_view.addSubview_(self.header_label)

        self.footer_label = _make_label(NSFont.systemFontOfSize_(10.5), NSColor.secondaryLabelColor())
        self.effect_view.addSubview_(self.footer_label)

        body_width = PANEL_WIDTH - PADDING * 2
        self.scroll_view = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, body_width, 10))
        self.scroll_view.setHasVerticalScroller_(True)
        self.scroll_view.setAutohidesScrollers_(True)
        self.scroll_view.setDrawsBackground_(False)
        self.scroll_view.setBorderType_(0)  # NSNoBorder

        self.text_view = _PreviewTextView.alloc().initWithFrame_(NSMakeRect(0, 0, body_width, 10))
        self.text_view.setFont_(NSFont.systemFontOfSize_(BODY_FONT_SIZE))
        self.text_view.setTextColor_(NSColor.labelColor())
        self.text_view.setInsertionPointColor_(NSColor.labelColor())
        self.text_view.setDrawsBackground_(False)
        self.text_view.setRichText_(False)
        self.text_view.setAllowsUndo_(True)
        self.text_view.setTextContainerInset_(NSMakeSize(0, 2))
        # 幅固定・縦だけ伸びる。高さの計算は layoutManager の usedRect で行う
        self.text_view.setMinSize_(NSMakeSize(body_width, 10))
        self.text_view.setMaxSize_(NSMakeSize(body_width, 100000))
        self.text_view.setVerticallyResizable_(True)
        self.text_view.setHorizontallyResizable_(False)
        self.text_view.textContainer().setWidthTracksTextView_(True)
        self.text_view.textContainer().setContainerSize_(NSMakeSize(body_width, 100000))
        self.text_view.setEditable_(False)
        self.text_view.setSelectable_(True)
        self.text_view.setDelegate_(self)
        self.scroll_view.setDocumentView_(self.text_view)
        self.effect_view.addSubview_(self.scroll_view)

        self._layout_rows(height)

    # ------------------------------------------------------------
    # レイアウト
    # ------------------------------------------------------------
    @objc.python_method
    def _line_height(self):
        font = self.text_view.font() if self.text_view is not None else NSFont.systemFontOfSize_(BODY_FONT_SIZE)
        lm = self.text_view.layoutManager() if self.text_view is not None else None
        if lm is not None:
            return float(lm.defaultLineHeightForFont_(font))
        return float(font.ascender() - font.descender() + font.leading())

    @objc.python_method
    def _footer_visible(self):
        return bool(FOOTER_HINTS.get(self.state))

    @objc.python_method
    def _panel_height_for_text_height(self, text_height):
        height = PADDING + HEADER_HEIGHT + ROW_GAP + text_height + PADDING
        if self._footer_visible():
            height += ROW_GAP + FOOTER_HEIGHT
        return math.ceil(height)

    @objc.python_method
    def _measure_text_height(self):
        """本文の行数（1〜8 行にクランプ）ぶんの高さ。超過分はスクロールになる"""
        lm = self.text_view.layoutManager()
        container = self.text_view.textContainer()
        lm.ensureLayoutForTextContainer_(container)
        used = lm.usedRectForTextContainer_(container)
        line_height = self._line_height()
        content = max(float(used.size.height), line_height * MIN_LINES)
        content = min(content, line_height * MAX_LINES)
        inset = float(self.text_view.textContainerInset().height) * 2
        return content + inset

    @objc.python_method
    def _layout_rows(self, height):
        """パネル高さに合わせて各行を配置（Cocoa 座標なので上から順に y を減らす）"""
        body_width = PANEL_WIDTH - PADDING * 2
        y = height - PADDING - HEADER_HEIGHT
        self.header_label.setFrame_(NSMakeRect(PADDING, y, body_width, HEADER_HEIGHT))
        footer_h = FOOTER_HEIGHT if self._footer_visible() else 0
        bottom = PADDING + (footer_h + ROW_GAP if footer_h else 0)
        body_height = max(y - ROW_GAP - bottom, 1)
        self.scroll_view.setFrame_(NSMakeRect(PADDING, bottom, body_width, body_height))
        self.footer_label.setFrame_(NSMakeRect(PADDING, PADDING, body_width, FOOTER_HEIGHT))
        self.footer_label.setHidden_(footer_h == 0)
        self.effect_view.setFrame_(NSMakeRect(0, 0, PANEL_WIDTH, height))

    @objc.python_method
    def _resize_to_fit(self):
        """本文量に応じて高さを変える。上端（反転時は下端）は固定"""
        height = self._panel_height_for_text_height(self._measure_text_height())
        frame = self.panel.frame()
        x = frame.origin.x
        if self.flipped:
            y = self.anchor_edge_y
        else:
            y = self.anchor_edge_y - height
        # スクリーン外へはみ出さないよう最終的にクランプ
        visible = self.screen_visible_frame
        if visible is not None:
            min_y = visible[1]
            max_y = visible[1] + visible[3]
            y = max(min(y, max_y - height), min_y)
        self._layout_rows(height)
        self.panel.setFrame_display_(NSMakeRect(x, y, PANEL_WIDTH, height), True)

    @objc.python_method
    def _screen_for_point(self, x, y):
        for screen in NSScreen.screens():
            f = screen.frame()
            if f.origin.x <= x <= f.origin.x + f.size.width and f.origin.y <= y <= f.origin.y + f.size.height:
                return screen
        main = NSScreen.mainScreen()
        if main is not None:
            return main
        screens = NSScreen.screens()
        return screens[0] if len(screens) else None

    @objc.python_method
    def _place(self, anchor):
        """anchor の直下（左端揃え、anchor.y − 8pt を上端）。収まらなければ anchor の上へ反転"""
        ax = float(getattr(anchor, "x", 0.0))
        ay = float(getattr(anchor, "y", 0.0))
        ah = float(getattr(anchor, "height", 0.0))
        screen = self._screen_for_point(ax, ay)
        visible = screen.visibleFrame() if screen is not None else NSMakeRect(0, 0, 1440, 900)
        # NSRect 構造体を objc.ivar に入れるのは避け、数値タプルで保持する
        self.screen_visible_frame = (
            float(visible.origin.x), float(visible.origin.y), float(visible.size.width), float(visible.size.height),
        )
        height = self._panel_height_for_text_height(self._measure_text_height())

        top = ay - ANCHOR_GAP
        if top - height >= visible.origin.y:
            self.flipped = False
            self.anchor_edge_y = top
            y = top - height
        else:
            self.flipped = True
            self.anchor_edge_y = ay + ah + ANCHOR_GAP
            y = self.anchor_edge_y
            max_y = visible.origin.y + visible.size.height
            if y + height > max_y:
                # 上にも収まらない場合はスクリーン上端に寄せる
                self.anchor_edge_y = max_y - height
                y = self.anchor_edge_y
        min_x = visible.origin.x
        max_x = visible.origin.x + visible.size.width - PANEL_WIDTH
        x = max(min(ax, max_x), min_x)
        self._layout_rows(height)
        self.panel.setFrame_display_(NSMakeRect(x, y, PANEL_WIDTH, height), False)
        log.info(
            "プレビューパネル配置: anchor=%s kind=%s flipped=%s frame=(%.0f, %.0f, %d, %d)",
            (round(ax), round(ay)), getattr(anchor, "kind", "?"), self.flipped, x, y, PANEL_WIDTH, height,
        )

    # ------------------------------------------------------------
    # 公開 API（main thread から呼ぶ）
    # ------------------------------------------------------------
    @objc.python_method
    def show_near(self, anchor):
        """テキストを空にし、読み取り専用・非キーで表示（対象アプリのフォーカスを奪わない）"""
        self._stop_pulse()
        # 前回の確認待ちでキーになっていた場合、いったん隠してキー状態を手放す
        if self.panel.isVisible():
            self.panel.orderOut_(None)
        self.input_active = False
        self.activated_app = False
        self.text_view.setEditable_(False)
        self.text_view.setString_("")
        undo = self.text_view.undoManager()
        if undo is not None:
            undo.removeAllActions()
        self.state = ""
        self.header_label.setStringValue_("")
        self.footer_label.setStringValue_("")
        self._place(anchor)
        # 録音中にパネルをクリックされるとキーになって対象アプリのキーボードを奪ってしまうので、
        # 確認ステップまではマウスを透過させる
        self.panel.setIgnoresMouseEvents_(True)
        # makeKey… ではなく orderFrontRegardless で「表示だけ」する
        self.panel.orderFrontRegardless()

    @objc.python_method
    def set_state(self, state):
        state = str(state or "")
        if state not in STATE_LABELS:
            log.warning("プレビューパネル: 未知の状態 %r", state)
        footer_changed = bool(FOOTER_HINTS.get(self.state)) != bool(FOOTER_HINTS.get(state))
        self.state = state
        self.header_label.setStringValue_(STATE_LABELS.get(state, state))
        self.footer_label.setStringValue_(FOOTER_HINTS.get(state, ""))
        if state == "hearing":
            self._start_pulse()
        else:
            self._stop_pulse()
        if footer_changed and self.panel.isVisible():
            self._resize_to_fit()
        elif footer_changed:
            self._layout_rows(self.panel.frame().size.height)

    @objc.python_method
    def set_text(self, text):
        self.text_view.setString_(str(text or ""))
        self._after_text_changed()

    @objc.python_method
    def append_text(self, text):
        """単純連結。区間間のスペース等は呼び出し側が決める"""
        if not text:
            return
        length = self.text_view.textStorage().length()
        # setEditable_(False) 中でもプログラムからの置換は通る。
        # textStorage を直接触らず NSTextView 経由にして、フォント等の typing attributes を引き継ぐ
        self.text_view.replaceCharactersInRange_withString_((length, 0), str(text))
        self._after_text_changed()

    @objc.python_method
    def current_text(self):
        return str(self.text_view.string())

    @objc.python_method
    def activate_for_input(self):
        """本文を編集可にし、パネルをキーにして Enter/Esc を受け取る"""
        self.text_view.setEditable_(True)
        self.input_active = True
        self.panel.setIgnoresMouseEvents_(False)
        if not self.panel.isVisible():
            self.panel.orderFrontRegardless()
        if ACTIVATE_APP_FOR_INPUT:
            NSApp.activateIgnoringOtherApps_(True)
            self.activated_app = True
        self.panel.makeKeyAndOrderFront_(None)
        self.panel.makeFirstResponder_(self.text_view)
        if not self.panel.isKeyWindow():
            # 非アクティブ化のままではキーになれなかった環境向けフォールバック。
            # この場合は対象アプリが非アクティブになるので、取消時の再アクティブ化は呼び出し側で行う
            log.warning("プレビューパネルがキーになれないため自プロセスをアクティブ化します")
            NSApp.activateIgnoringOtherApps_(True)
            self.activated_app = True
            self.panel.makeKeyAndOrderFront_(None)
            self.panel.makeFirstResponder_(self.text_view)
        # キャレットを末尾へ（編集はたいてい末尾から）
        end = self.text_view.textStorage().length()
        self.text_view.setSelectedRange_((end, 0))
        self.text_view.scrollRangeToVisible_((end, 0))

    @objc.python_method
    def did_activate_app(self):
        """activate_for_input が自プロセスをアクティブ化した（対象アプリが非アクティブになった）か"""
        return bool(self.activated_app)

    @objc.python_method
    def hide(self):
        self._stop_pulse()
        self.input_active = False
        self.text_view.setEditable_(False)
        if self.panel.isKeyWindow():
            self.panel.makeFirstResponder_(None)
        self.panel.orderOut_(None)

    @objc.python_method
    def is_visible(self):
        return bool(self.panel.isVisible())

    # ------------------------------------------------------------
    # 内部処理
    # ------------------------------------------------------------
    @objc.python_method
    def _after_text_changed(self):
        if self.panel.isVisible():
            self._resize_to_fit()
        else:
            self._layout_rows(self._panel_height_for_text_height(self._measure_text_height()))
        if not self.input_active:
            # 録音中は追記された末尾が見えるように追従する
            end = self.text_view.textStorage().length()
            self.text_view.scrollRangeToVisible_((end, 0))

    @objc.python_method
    def _start_pulse(self):
        if self.pulse_timer is not None:
            return
        self.pulse_phase = 0
        self.pulse_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            PULSE_INTERVAL, self, b"pulseTick:", None, True
        )

    @objc.python_method
    def _stop_pulse(self):
        if self.pulse_timer is not None:
            self.pulse_timer.invalidate()
            self.pulse_timer = None

    @objc.typedSelector(b"v@:@")
    def pulseTick_(self, timer):
        if self.state != "hearing":
            self._stop_pulse()
            return
        self.pulse_phase = (self.pulse_phase + 1) % 16
        # ドットの不透明度を正弦波で往復させて「聞いている」感を出す
        alpha = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(self.pulse_phase / 16.0 * 2 * math.pi))
        dot = NSAttributedString.alloc().initWithString_attributes_(
            "●", {NSForegroundColorAttributeName: NSColor.systemRedColor().colorWithAlphaComponent_(alpha)}
        )
        rest = NSAttributedString.alloc().initWithString_attributes_(
            " 聞き取り中…", {NSForegroundColorAttributeName: NSColor.labelColor()}
        )
        combined = dot.mutableCopy()
        combined.appendAttributedString_(rest)
        self.header_label.setAttributedStringValue_(combined)

    # --- NSTextViewDelegate ---
    def textDidChange_(self, notification):
        # ユーザー編集で行数が変わったら高さを追従させる
        if self.panel.isVisible():
            self._resize_to_fit()

    def textView_doCommandBySelector_(self, text_view, selector):
        if not self.input_active:
            # 録音中（非キー）は何もしない。通常の処理に任せる
            return False
        name = str(selector)
        if name == "insertNewline:":
            event = NSApp.currentEvent()
            mods = int(event.modifierFlags()) if event is not None else 0
            # Return には NumericPad/Function フラグが付くので Shift/Option だけを見る
            if mods & (NSEventModifierFlagShift | NSEventModifierFlagOption):
                text_view.insertNewlineIgnoringFieldEditor_(None)
                return True
            self._fire_confirm()
            return True
        if name == "cancelOperation:":
            self._fire_cancel()
            return True
        return False

    @objc.python_method
    def _fire_confirm(self):
        text = self.current_text()
        # 二重発火防止（Enter 連打）
        self.input_active = False
        log.info("プレビューパネル: 確定 (%d 文字)", len(text))
        if self.on_confirm is not None:
            try:
                self.on_confirm(text)
            except Exception as e:
                log.error("on_confirm でエラー: %s", e)

    @objc.python_method
    def _fire_cancel(self):
        self.input_active = False
        log.info("プレビューパネル: 取消")
        if self.on_cancel is not None:
            try:
                self.on_cancel()
            except Exception as e:
                log.error("on_cancel でエラー: %s", e)

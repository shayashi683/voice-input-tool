"""ネイティブ macOS 設定画面 (PyObjC)"""
import objc
from Cocoa import (
    NSWindow,
    NSWindowStyleMaskTitled,
    NSWindowStyleMaskClosable,
    NSBackingStoreBuffered,
    NSTextField,
    NSSecureTextField,
    NSButton,
    NSButtonTypeSwitch,
    NSPopUpButton,
    NSTextView,
    NSScrollView,
    NSFont,
    NSColor,
    NSBezelStyleRounded,
    NSMakeRect,
    NSObject,
    NSApp,
    NSControlStateValueOn,
    NSControlStateValueOff,
    NSEventModifierFlagControl,
    NSEventModifierFlagShift,
    NSEventModifierFlagOption,
    NSEventModifierFlagCommand,
    NSEventTypeFlagsChanged,
    NSEventTypeKeyDown,
    NSAlert,
    NSPasteboard,
    NSPasteboardTypeString,
)
from voice_input_tool.audio_devices import list_input_devices
from voice_input_tool.config import (
    ASR_BACKEND_OPTIONS,
    LLM_MODEL_OPTIONS,
    load_config,
    save_config,
)
from voice_input_tool.hotkeys import DEFAULT_RECORD_HOTKEY, MAC_KEYCODE_MAP, hotkey_to_display


def _label(text, x, y, width=140):
    label = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, width, 20))
    label.setStringValue_(text)
    label.setBezeled_(False)
    label.setDrawsBackground_(False)
    label.setEditable_(False)
    label.setSelectable_(False)
    label.setFont_(NSFont.systemFontOfSize_(13))
    return label


def _value_label(text, x, y, width=60):
    label = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, width, 20))
    label.setStringValue_(text)
    label.setBezeled_(False)
    label.setDrawsBackground_(False)
    label.setEditable_(False)
    label.setSelectable_(False)
    label.setFont_(NSFont.monospacedDigitSystemFontOfSize_weight_(12, 0.0))
    label.setAlignment_(2)  # right
    return label


def _hint_label(text, x, y, width=300):
    label = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, width, 14))
    label.setStringValue_(text)
    label.setBezeled_(False)
    label.setDrawsBackground_(False)
    label.setEditable_(False)
    label.setSelectable_(False)
    label.setFont_(NSFont.systemFontOfSize_(10))
    label.setTextColor_(NSColor.secondaryLabelColor())
    return label


def _parse_asr_keywords(text):
    if any(char in text for char in "<>\r\n"):
        raise ValueError("認識する用語に < > や改行は使用できません。用語はカンマで区切ってください。")
    return [word.strip() for word in text.replace("、", ",").replace("，", ",").split(",") if word.strip()]


class HotkeyField(NSTextField):
    """キー入力をキャプチャするカスタムテキストフィールド"""
    _hotkey_value = objc.ivar()
    _previous_display = objc.ivar()
    _is_recording = objc.ivar()
    _pending_modifier_keycode = objc.ivar()

    def initWithFrame_(self, frame):
        self = objc.super(HotkeyField, self).initWithFrame_(frame)
        if self is None:
            return None
        self._hotkey_value = None
        self._previous_display = ""
        self._is_recording = False
        self._pending_modifier_keycode = None
        self.setEditable_(False)
        self.setSelectable_(False)
        self.setBezeled_(True)
        self.setDrawsBackground_(True)
        self.setBackgroundColor_(NSColor.textBackgroundColor())
        self.setFont_(NSFont.systemFontOfSize_(13))
        self.setAlignment_(1)  # center
        return self

    def acceptsFirstResponder(self):
        return True

    def mouseDown_(self, event):
        window = self.window()
        if window is not None:
            window.makeFirstResponder_(self)

    def becomeFirstResponder(self):
        self._is_recording = True
        self._pending_modifier_keycode = None
        self._previous_display = str(self.stringValue())
        self.setStringValue_("キーを押してください...")
        self.setTextColor_(NSColor.systemBlueColor())
        return True

    def resignFirstResponder(self):
        if self._is_recording:
            self.setStringValue_(self._previous_display)
        self._is_recording = False
        self._pending_modifier_keycode = None
        self.setTextColor_(NSColor.labelColor())
        return True

    def keyDown_(self, event):
        hotkey = self._hotkey_from_event(event)
        if hotkey is None:
            return
        self.setHotkeyValue_(hotkey)
        self._is_recording = False
        window = self.window()
        if window is not None:
            window.makeFirstResponder_(None)

    def performKeyEquivalent_(self, event):
        if not self._is_recording:
            return False
        self.keyDown_(event)
        return True

    def flagsChanged_(self, event):
        if not self._is_recording:
            return
        keycode = int(event.keyCode())
        if keycode not in (56, 60):  # Left Shift / Right Shift
            return

        # Do not commit on key-down: the user may still type Shift+A. If the
        # same physical Shift is released without another key, save it alone.
        if self._pending_modifier_keycode is None:
            self._pending_modifier_keycode = keycode
            side = "Left Shift" if keycode == 56 else "Right Shift"
            self.setStringValue_(f"{side} …")
            return
        if int(self._pending_modifier_keycode) != keycode:
            return

        self.setHotkeyValue_("<left_shift>" if keycode == 56 else "<right_shift>")
        self._is_recording = False
        window = self.window()
        if window is not None:
            window.makeFirstResponder_(None)

    def getHotkeyValue(self):
        return self._hotkey_value

    def setHotkeyValue_(self, value):
        self._pending_modifier_keycode = None
        self._hotkey_value = value
        display = hotkey_to_display(value)
        self._previous_display = display
        self.setStringValue_(display)
        self.setTextColor_(NSColor.labelColor())

    def _hotkey_from_event(self, event):
        mods = int(event.modifierFlags())
        parts = []
        if mods & NSEventModifierFlagControl:
            parts.append("<ctrl>")
        if mods & NSEventModifierFlagShift:
            parts.append("<shift>")
        if mods & NSEventModifierFlagOption:
            parts.append("<alt>")
        if mods & NSEventModifierFlagCommand:
            parts.append("<cmd>")

        key_name = self._key_name_from_event(event)
        if key_name is None:
            return None

        return "+".join(parts + [key_name])

    def _key_name_from_event(self, event):
        keycode = int(event.keyCode())
        if keycode in MAC_KEYCODE_MAP:
            return MAC_KEYCODE_MAP[keycode]

        chars = event.charactersIgnoringModifiers()
        if chars and len(chars) == 1 and chars.isprintable():
            return str(chars).lower()
        return f"key{keycode}"


class HotkeyCaptureWindow(NSWindow):
    """Route key events at the window boundary while the hotkey field is armed."""

    hotkey_field = objc.ivar()

    def sendEvent_(self, event):
        field = self.hotkey_field
        if field is not None and bool(field._is_recording):
            event_type = int(event.type())
            if event_type == NSEventTypeKeyDown:
                field.keyDown_(event)
                return
            if event_type == NSEventTypeFlagsChanged:
                field.flagsChanged_(event)
                return
        objc.super(HotkeyCaptureWindow, self).sendEvent_(event)


class SettingsWindowController(NSObject):
    """設定ウィンドウ"""

    window = objc.ivar()
    asr_backend_popup = objc.ivar()
    openai_api_key_field = objc.ivar()
    asr_prompt_field = objc.ivar()
    asr_keywords_field = objc.ivar()
    asr_languages = objc.ivar()
    llm_checkbox = objc.ivar()
    model_popup = objc.ivar()
    hotkey_record_field = objc.ivar()
    input_device_popup = objc.ivar()
    prompt_textview = objc.ivar()
    on_save_callback = objc.ivar()
    on_close_callback = objc.ivar()

    def initWithCallback_(self, callback):
        self = objc.super(SettingsWindowController, self).init()
        if self is None:
            return None
        self.on_save_callback = callback
        self.on_close_callback = None
        self._build_window()
        return self

    def _build_window(self):
        config = load_config()
        self.asr_languages = list(config["asr_languages"])
        W, H = 560, 660

        self.window = HotkeyCaptureWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(200, 200, W, H),
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable,
            NSBackingStoreBuffered,
            False,
        )
        self.window.setTitle_("Voice Input Tool - 設定")
        self.window.setLevel_(3)  # floating
        self.window.setReleasedWhenClosed_(False)
        self.window.setDelegate_(self)
        content = self.window.contentView()
        y = H - 40

        # --- 音声認識 ---
        content.addSubview_(_label("音声認識モデル", 20, y))
        self.asr_backend_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(170, y - 3, 360, 26), False,
        )
        self._populate_asr_backends(config["asr_backend"])
        content.addSubview_(self.asr_backend_popup)
        content.addSubview_(_hint_label(
            "GPT-Transcribeでは録音音声をOpenAIに送信します。", 170, y - 21, 370,
        ))
        y -= 58

        content.addSubview_(_label("OpenAI API Key", 20, y))
        self.openai_api_key_field = NSSecureTextField.alloc().initWithFrame_(NSMakeRect(170, y - 2, 270, 24))
        self.openai_api_key_field.setStringValue_(config["openai_api_key"])
        self.openai_api_key_field.setPlaceholderString_("sk-...")
        self.openai_api_key_field.setFont_(NSFont.systemFontOfSize_(12))
        content.addSubview_(self.openai_api_key_field)
        openai_paste_btn = NSButton.alloc().initWithFrame_(NSMakeRect(450, y - 4, 80, 28))
        openai_paste_btn.setTitle_("貼り付け")
        openai_paste_btn.setBezelStyle_(NSBezelStyleRounded)
        openai_paste_btn.setTarget_(self)
        openai_paste_btn.setAction_(b"pasteOpenaiApiKeyClicked:")
        content.addSubview_(openai_paste_btn)
        content.addSubview_(_hint_label(
            "音声認識と追加の句読点補正で同じキーを使います。", 170, y - 21, 380,
        ))
        y -= 52

        content.addSubview_(_label("話題のヒント", 20, y))
        self.asr_prompt_field = NSTextField.alloc().initWithFrame_(NSMakeRect(170, y - 2, 360, 24))
        self.asr_prompt_field.setStringValue_(config["asr_prompt"])
        self.asr_prompt_field.setPlaceholderString_("例：営業指標や開発についての会話")
        self.asr_prompt_field.setFont_(NSFont.systemFontOfSize_(12))
        content.addSubview_(self.asr_prompt_field)
        y -= 34

        content.addSubview_(_label("認識する用語", 20, y))
        self.asr_keywords_field = NSTextField.alloc().initWithFrame_(NSMakeRect(170, y - 2, 360, 24))
        self.asr_keywords_field.setStringValue_(", ".join(config["asr_keywords"]))
        self.asr_keywords_field.setPlaceholderString_("例：SQL, MQL, 商品名, 人名")
        self.asr_keywords_field.setFont_(NSFont.systemFontOfSize_(12))
        content.addSubview_(self.asr_keywords_field)
        content.addSubview_(_hint_label(
            "任意・カンマ区切り。GPT-Transcribeの認識時に使用します。", 170, y - 21, 380,
        ))
        y -= 56

        # --- LLM 補正 ---
        content.addSubview_(_label("追加の句読点補正", 20, y))
        self.llm_checkbox = NSButton.alloc().initWithFrame_(NSMakeRect(170, y - 2, 360, 24))
        self.llm_checkbox.setButtonType_(NSButtonTypeSwitch)
        self.llm_checkbox.setTitle_("OpenAIで補正する（任意）")
        self.llm_checkbox.setState_(NSControlStateValueOn if config["use_llm"] else NSControlStateValueOff)
        content.addSubview_(self.llm_checkbox)
        content.addSubview_(_hint_label(
            "補正時は認識テキストをOpenAIへ送信。通常はオフで使用。", 170, y - 21, 380,
        ))
        y -= 54

        # --- LLM model ---
        content.addSubview_(_label("句読点補正モデル", 20, y))
        self.model_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(170, y - 3, 360, 26),
            False,
        )
        self._populate_models(config.get("llm_model", ""))
        content.addSubview_(self.model_popup)
        y -= 39

        # --- ホットキー: 録音開始/停止 ---
        content.addSubview_(_label("録音 開始/停止", 20, y))
        self.hotkey_record_field = HotkeyField.alloc().initWithFrame_(NSMakeRect(170, y - 2, 200, 24))
        self.hotkey_record_field.setHotkeyValue_(config.get("hotkey_record", "<ctrl>+<shift>+<space>"))
        self.window.hotkey_field = self.hotkey_record_field
        content.addSubview_(self.hotkey_record_field)
        content.addSubview_(_hint_label("クリック後、Left/Right Shiftまたはキーの組み合わせを押す", 170, y - 21, 360))
        y -= 49

        # --- 入力マイク ---
        content.addSubview_(_label("入力マイク", 20, y))
        self.input_device_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(170, y - 3, 360, 26),
            False,
        )
        self._populate_input_devices(config.get("input_device_id", ""))
        content.addSubview_(self.input_device_popup)
        y -= 37

        # --- LLM Prompt ---
        content.addSubview_(_label("句読点補正プロンプト", 20, y, 200))
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(20, 62, 520, y - 68))
        scroll.setHasVerticalScroller_(True)
        scroll.setBorderType_(3)  # NSBezelBorder
        self.prompt_textview = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, 500, y - 68))
        self.prompt_textview.setString_(config.get("llm_prompt", ""))
        self.prompt_textview.setFont_(NSFont.systemFontOfSize_(12))
        self.prompt_textview.setMinSize_((500, y - 68))
        self.prompt_textview.setMaxSize_((500, 10000))
        self.prompt_textview.setVerticallyResizable_(True)
        self.prompt_textview.textContainer().setWidthTracksTextView_(True)
        scroll.setDocumentView_(self.prompt_textview)
        content.addSubview_(scroll)

        # --- Buttons ---
        save_btn = NSButton.alloc().initWithFrame_(NSMakeRect(W - 230, 15, 80, 32))
        save_btn.setTitle_("保存")
        save_btn.setBezelStyle_(NSBezelStyleRounded)
        save_btn.setTarget_(self)
        save_btn.setAction_(b"saveClicked:")
        save_btn.setKeyEquivalent_("\r")
        content.addSubview_(save_btn)

        cancel_btn = NSButton.alloc().initWithFrame_(NSMakeRect(W - 140, 15, 110, 32))
        cancel_btn.setTitle_("キャンセル")
        cancel_btn.setBezelStyle_(NSBezelStyleRounded)
        cancel_btn.setTarget_(self)
        cancel_btn.setAction_(b"cancelClicked:")
        cancel_btn.setKeyEquivalent_("\x1b")  # Escape
        content.addSubview_(cancel_btn)

        reset_btn = NSButton.alloc().initWithFrame_(NSMakeRect(20, 15, 150, 32))
        reset_btn.setTitle_("デフォルトに戻す")
        reset_btn.setBezelStyle_(NSBezelStyleRounded)
        reset_btn.setTarget_(self)
        reset_btn.setAction_(b"resetClicked:")
        content.addSubview_(reset_btn)

    def _populate_input_devices(self, selected_device_id):
        self.input_device_popup.removeAllItems()
        self.input_device_popup.addItemWithTitle_("自動選択")
        self.input_device_popup.itemAtIndex_(0).setRepresentedObject_("")

        selected_index = 0
        for device in list_input_devices():
            self.input_device_popup.addItemWithTitle_(device["label"])
            item_index = self.input_device_popup.numberOfItems() - 1
            item = self.input_device_popup.itemAtIndex_(item_index)
            item.setRepresentedObject_(device["id"])
            if str(selected_device_id) in (device["id"], device.get("legacy_id")):
                selected_index = item_index

        if selected_device_id and selected_index == 0:
            # Saving unrelated settings while unplugged must retain the preferred mic.
            label = str(selected_device_id).removeprefix("name:")
            self.input_device_popup.addItemWithTitle_(f"{label}（未接続・再接続時に使用）")
            selected_index = self.input_device_popup.numberOfItems() - 1
            self.input_device_popup.itemAtIndex_(selected_index).setRepresentedObject_(str(selected_device_id))

        self.input_device_popup.setEnabled_(True)
        if self.input_device_popup.numberOfItems() == 1:
            self.input_device_popup.addItemWithTitle_("入力デバイスが見つかりません")
            self.input_device_popup.itemAtIndex_(1).setRepresentedObject_("")
            self.input_device_popup.setEnabled_(False)

        self.input_device_popup.selectItemAtIndex_(selected_index)

    def _populate_models(self, selected_model):
        self.model_popup.removeAllItems()
        selected_index = 0
        known_models = set()

        for option in LLM_MODEL_OPTIONS:
            model = option["model"]
            known_models.add(model)
            self.model_popup.addItemWithTitle_(option["label"])
            item_index = self.model_popup.numberOfItems() - 1
            self.model_popup.itemAtIndex_(item_index).setRepresentedObject_(model)
            if model == str(selected_model):
                selected_index = item_index

        if selected_model and str(selected_model) not in known_models:
            self.model_popup.addItemWithTitle_(f"カスタム: {selected_model}")
            selected_index = self.model_popup.numberOfItems() - 1
            self.model_popup.itemAtIndex_(selected_index).setRepresentedObject_(
                str(selected_model)
            )

        self.model_popup.selectItemAtIndex_(selected_index)

    def _populate_asr_backends(self, selected_backend):
        self.asr_backend_popup.removeAllItems()
        selected_index = 0
        for index, option in enumerate(ASR_BACKEND_OPTIONS):
            self.asr_backend_popup.addItemWithTitle_(option["label"])
            self.asr_backend_popup.itemAtIndex_(index).setRepresentedObject_(option["backend"])
            if option["backend"] == selected_backend:
                selected_index = index
        self.asr_backend_popup.selectItemAtIndex_(selected_index)

    @objc.typedSelector(b"v@:@")
    def pasteOpenaiApiKeyClicked_(self, sender):
        self._paste_api_key(self.openai_api_key_field)

    def _paste_api_key(self, field):
        pasteboard = NSPasteboard.generalPasteboard()
        value = pasteboard.stringForType_(NSPasteboardTypeString)
        if value is None:
            alert = NSAlert.alloc().init()
            alert.setMessageText_("クリップボードにテキストがありません")
            alert.setInformativeText_("APIキーをコピーしてから、もう一度貼り付けを押してください。")
            alert.runModal()
            return
        field.setStringValue_(str(value).strip())

    @objc.typedSelector(b"v@:@")
    def saveClicked_(self, sender):
        self.window.makeFirstResponder_(None)
        config = load_config()
        hotkey_val = self.hotkey_record_field.getHotkeyValue()
        input_device_id = ""
        selected_device = self.input_device_popup.selectedItem()
        if selected_device is not None and selected_device.representedObject() is not None:
            input_device_id = str(selected_device.representedObject())
        selected_model = self.model_popup.selectedItem()
        llm_model = (
            str(selected_model.representedObject())
            if selected_model is not None and selected_model.representedObject() is not None
            else config["llm_model"]
        )
        selected_backend = self.asr_backend_popup.selectedItem()
        asr_backend = (
            str(selected_backend.representedObject())
            if selected_backend is not None and selected_backend.representedObject() is not None
            else config["asr_backend"]
        )
        config.update({
            "asr_backend": asr_backend,
            "openai_api_key": str(self.openai_api_key_field.stringValue()).strip(),
            "asr_prompt": str(self.asr_prompt_field.stringValue()).strip(),
            "asr_languages": list(self.asr_languages),
            "use_llm": self.llm_checkbox.state() == NSControlStateValueOn,
            "llm_model": llm_model,
            "hotkey_record": hotkey_val if hotkey_val else DEFAULT_RECORD_HOTKEY,
            "input_device_id": input_device_id,
            "llm_prompt": str(self.prompt_textview.string()),
        })
        try:
            config["asr_keywords"] = _parse_asr_keywords(str(self.asr_keywords_field.stringValue()))
            if asr_backend == "gpt-transcribe" and not config["openai_api_key"]:
                raise ValueError("GPT-Transcribeを使うには、OpenAI APIキーを入力してください。")
            if config["use_llm"] and not config["openai_api_key"]:
                raise ValueError("追加の句読点補正を使うには、OpenAI APIキーを入力してください。")
            save_config(config)
        except Exception as e:
            alert = NSAlert.alloc().init()
            alert.setMessageText_("設定を保存できませんでした")
            alert.setInformativeText_(str(e))
            alert.runModal()
            return
        if self.on_save_callback:
            self.on_save_callback(config)
        self.window.close()

    @objc.typedSelector(b"v@:@")
    def cancelClicked_(self, sender):
        self.window.close()

    @objc.typedSelector(b"v@:@")
    def windowWillClose_(self, notification):
        # Standalone settings must stop their own loop on every close path.
        # In-process settings leave this callback unset and keep the app running.
        callback = self.on_close_callback
        self.on_close_callback = None
        if callback:
            callback()

    @objc.typedSelector(b"v@:@")
    def resetClicked_(self, sender):
        from voice_input_tool.config import DEFAULTS
        self._populate_asr_backends(DEFAULTS["asr_backend"])
        self.openai_api_key_field.setStringValue_("")
        self.asr_prompt_field.setStringValue_(DEFAULTS["asr_prompt"])
        self.asr_keywords_field.setStringValue_(", ".join(DEFAULTS["asr_keywords"]))
        self.asr_languages = list(DEFAULTS["asr_languages"])
        self.llm_checkbox.setState_(NSControlStateValueOn if DEFAULTS["use_llm"] else NSControlStateValueOff)
        self._populate_models(DEFAULTS["llm_model"])
        self.hotkey_record_field.setHotkeyValue_(DEFAULTS["hotkey_record"])
        self._populate_input_devices(DEFAULTS["input_device_id"])
        self.prompt_textview.setString_(DEFAULTS["llm_prompt"])

    def show(self):
        self.window.center()
        self.window.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)

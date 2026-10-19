from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from voice_input_tool.config import normalize_config
from Cocoa import (
    NSBackingStoreBuffered,
    NSMakeRect,
    NSEventModifierFlagShift,
    NSEventTypeKeyDown,
    NSWindowStyleMaskTitled,
)

from voice_input_tool.settings_ui import (
    HotkeyCaptureWindow,
    HotkeyField,
    SettingsWindowController,
    _parse_asr_keywords,
)


class KeyEvent:
    def __init__(self, keycode, modifiers=0, characters="", event_type=NSEventTypeKeyDown):
        self.keycode = keycode
        self.modifiers = modifiers
        self.characters = characters
        self.event_type = event_type

    def type(self):
        return self.event_type

    def keyCode(self):
        return self.keycode

    def modifierFlags(self):
        return self.modifiers

    def charactersIgnoringModifiers(self):
        return self.characters


class HotkeyFieldTests(unittest.TestCase):
    def field(self):
        field = HotkeyField.alloc().initWithFrame_(NSMakeRect(0, 0, 200, 24))
        field.setHotkeyValue_("<ctrl>+<space>")
        field.becomeFirstResponder()
        return field

    def test_shift_plus_key_is_not_committed_as_shift_alone(self):
        field = self.field()
        field.flagsChanged_(KeyEvent(56, NSEventModifierFlagShift))
        self.assertEqual(field.getHotkeyValue(), "<ctrl>+<space>")
        field.keyDown_(KeyEvent(0, NSEventModifierFlagShift, "a"))
        self.assertEqual(field.getHotkeyValue(), "<shift>+a")

    def test_shift_alone_commits_on_release(self):
        field = self.field()
        field.flagsChanged_(KeyEvent(60, NSEventModifierFlagShift))
        field.flagsChanged_(KeyEvent(60, 0))
        self.assertEqual(field.getHotkeyValue(), "<right_shift>")

    def test_plain_key_can_be_captured(self):
        field = self.field()
        field.keyDown_(KeyEvent(0, 0, "a"))
        self.assertEqual(field.getHotkeyValue(), "a")

    def test_window_routes_key_events_to_armed_field(self):
        field = self.field()
        window = HotkeyCaptureWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, 300, 100), NSWindowStyleMaskTitled, NSBackingStoreBuffered, False,
        )
        window.hotkey_field = field
        window.sendEvent_(KeyEvent(0, NSEventModifierFlagShift, "a"))
        self.assertEqual(field.getHotkeyValue(), "<shift>+a")


class MenuItem:
    def __init__(self, title):
        self.title = title
        self.value = None

    def setRepresentedObject_(self, value):
        self.value = value


class DevicePopup:
    def removeAllItems(self):
        self.items = []

    def addItemWithTitle_(self, title):
        self.items.append(MenuItem(title))

    def itemAtIndex_(self, index):
        return self.items[index]

    def numberOfItems(self):
        return len(self.items)

    def setEnabled_(self, enabled):
        self.enabled = enabled

    def selectItemAtIndex_(self, index):
        self.selected = self.items[index]


class SettingsDeviceTests(unittest.TestCase):
    def populate(self, selected, devices):
        popup = DevicePopup()
        controller = SimpleNamespace(input_device_popup=popup)
        with patch('voice_input_tool.settings_ui.list_input_devices', return_value=devices):
            SettingsWindowController._populate_input_devices(controller, selected)
        return popup

    def test_offline_preference_is_retained_when_other_mics_are_present(self):
        popup = self.populate('name:Shure MV7+', [
            {'id': 'name:Internal', 'legacy_id': '0', 'label': 'Internal (1ch)'}])
        self.assertEqual(popup.selected.value, 'name:Shure MV7+')
        self.assertIn('未接続', popup.selected.title)
        self.assertTrue(popup.enabled)

    def test_offline_preference_is_retained_without_any_inputs(self):
        popup = self.populate('name:Shure MV7+', [])
        self.assertEqual(popup.selected.value, 'name:Shure MV7+')
        self.assertTrue(popup.enabled)

    def test_old_index_selects_named_device_for_next_save(self):
        popup = self.populate('2', [
            {'id': 'name:Shure MV7+', 'legacy_id': '2', 'label': 'Shure MV7+ (1ch)'}])
        self.assertEqual(popup.selected.value, 'name:Shure MV7+')
        self.assertNotIn('未接続', popup.selected.title)


class SettingsTranscriptionTests(unittest.TestCase):
    def controller(self, backend="gpt-transcribe", key="openai-key"):
        fields = dict(
            window=Mock(), on_save_callback=Mock(), on_close_callback=None,
            hotkey_record_field=Mock(), input_device_popup=Mock(),
            model_popup=Mock(), asr_backend_popup=Mock(),
            openai_api_key_field=Mock(), asr_prompt_field=Mock(),
            asr_keywords_field=Mock(), asr_languages=["ja", "en"],
            llm_checkbox=Mock(), prompt_textview=Mock(),
        )
        controller = SettingsWindowController.alloc().init()
        for name, value in fields.items():
            setattr(controller, name, value)
        controller.hotkey_record_field.getHotkeyValue.return_value = "<ctrl>+<space>"
        controller.input_device_popup.selectedItem.return_value = None
        controller.model_popup.selectedItem.return_value = None
        controller.asr_backend_popup.selectedItem.return_value.representedObject.return_value = backend
        controller.openai_api_key_field.stringValue.return_value = key
        controller.asr_prompt_field.stringValue.return_value = "営業指標の会話"
        controller.asr_keywords_field.stringValue.return_value = "SQL, MQL、製品名"
        controller.llm_checkbox.state.return_value = 0
        controller.prompt_textview.string.return_value = "句読点のみ"
        controller.window.close.side_effect = lambda: controller.windowWillClose_(None)
        return controller

    def test_keyword_input_accepts_japanese_delimiters_without_changing_terms(self):
        self.assertEqual(_parse_asr_keywords("SQL, MQL、製品名，Test"), ["SQL", "MQL", "製品名", "Test"])
        for text in ("<term>", "SQL\nMQL", "SQL\rMQL"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                _parse_asr_keywords(text)

    def test_save_keeps_shared_openai_key_and_recognition_context(self):
        controller = self.controller()
        with patch("voice_input_tool.settings_ui.load_config", return_value=normalize_config()), \
             patch("voice_input_tool.settings_ui.save_config") as save:
            SettingsWindowController.saveClicked_(controller, None)
        saved = save.call_args.args[0]
        self.assertEqual(saved["asr_backend"], "gpt-transcribe")
        self.assertEqual(saved["openai_api_key"], "openai-key")
        self.assertNotIn("openrouter_api_key", saved)
        self.assertNotIn("llm_provider_order", saved)
        self.assertEqual(saved["asr_prompt"], "営業指標の会話")
        self.assertEqual(saved["asr_keywords"], ["SQL", "MQL", "製品名"])
        self.assertEqual(saved["asr_languages"], ["ja", "en"])
        controller.window.close.assert_called_once()
        controller.on_save_callback.assert_called_once_with(saved)

    def test_cloud_without_key_displays_error_and_does_not_save(self):
        controller = self.controller(key=" ")
        with patch("voice_input_tool.settings_ui.load_config", return_value=normalize_config()), \
             patch("voice_input_tool.settings_ui.save_config") as save, \
             patch("voice_input_tool.settings_ui.NSAlert") as alert:
            SettingsWindowController.saveClicked_(controller, None)
        save.assert_not_called()
        controller.window.close.assert_not_called()
        controller.on_save_callback.assert_not_called()
        self.assertIn("OpenAI APIキー", alert.alloc.return_value.init.return_value.setInformativeText_.call_args.args[0])

    def test_local_without_openai_key_can_save(self):
        controller = self.controller(backend="reazonspeech", key="")
        with patch("voice_input_tool.settings_ui.load_config", return_value=normalize_config()), \
             patch("voice_input_tool.settings_ui.save_config") as save:
            SettingsWindowController.saveClicked_(controller, None)
        self.assertEqual(save.call_args.args[0]["asr_backend"], "reazonspeech")

    def test_local_correction_without_openai_key_does_not_save(self):
        controller = self.controller(backend="reazonspeech", key="")
        controller.llm_checkbox.state.return_value = 1
        with patch("voice_input_tool.settings_ui.load_config", return_value=normalize_config()), \
             patch("voice_input_tool.settings_ui.save_config") as save, \
             patch("voice_input_tool.settings_ui.NSAlert") as alert:
            SettingsWindowController.saveClicked_(controller, None)
        save.assert_not_called()
        self.assertIn("OpenAI APIキー", alert.alloc.return_value.init.return_value.setInformativeText_.call_args.args[0])

    def test_shared_key_enables_local_correction(self):
        controller = self.controller(backend="reazonspeech")
        controller.llm_checkbox.state.return_value = 1
        with patch("voice_input_tool.settings_ui.load_config", return_value=normalize_config()), \
             patch("voice_input_tool.settings_ui.save_config") as save:
            SettingsWindowController.saveClicked_(controller, None)
        saved = save.call_args.args[0]
        self.assertTrue(saved["use_llm"])
        self.assertEqual(saved["openai_api_key"], "openai-key")

    def test_invalid_keyword_input_displays_error_without_saving(self):
        controller = self.controller()
        controller.asr_keywords_field.stringValue.return_value = "SQL\nMQL"
        with patch("voice_input_tool.settings_ui.load_config", return_value=normalize_config()), \
             patch("voice_input_tool.settings_ui.save_config") as save, \
             patch("voice_input_tool.settings_ui.NSAlert"):
            SettingsWindowController.saveClicked_(controller, None)
        save.assert_not_called()
        controller.window.close.assert_not_called()

    def test_cancel_closes_standalone_settings_without_saving(self):
        controller = self.controller()
        stop_loop = Mock()
        controller.on_close_callback = stop_loop
        with patch("voice_input_tool.settings_ui.save_config") as save:
            controller.cancelClicked_(None)
        controller.window.close.assert_called_once()
        save.assert_not_called()
        controller.on_save_callback.assert_not_called()
        stop_loop.assert_called_once_with()

    def test_window_close_stops_standalone_loop_once_without_saving(self):
        controller = self.controller()
        stop_loop = Mock()
        controller.on_close_callback = stop_loop
        with patch("voice_input_tool.settings_ui.save_config") as save:
            controller.windowWillClose_(None)
            controller.windowWillClose_(None)
        save.assert_not_called()
        controller.on_save_callback.assert_not_called()
        stop_loop.assert_called_once_with()

    def test_save_calls_regular_callback_before_closing_standalone_loop(self):
        controller = self.controller()
        events = Mock()
        controller.on_save_callback = events.saved
        controller.on_close_callback = events.closed
        with patch("voice_input_tool.settings_ui.load_config", return_value=normalize_config()), \
             patch("voice_input_tool.settings_ui.save_config") as save:
            controller.saveClicked_(None)
        save.assert_called_once()
        events.saved.assert_called_once_with(save.call_args.args[0])
        events.closed.assert_called_once_with()
        self.assertEqual([call[0] for call in events.mock_calls], ["saved", "closed"])

    def test_in_process_window_close_does_not_stop_main_event_loop(self):
        controller = self.controller()
        with patch("PyObjCTools.AppHelper.stopEventLoop") as stop_loop, \
             patch("voice_input_tool.settings_ui.save_config") as save:
            controller.cancelClicked_(None)
        stop_loop.assert_not_called()
        save.assert_not_called()
        controller.on_save_callback.assert_not_called()

import unittest

from voice_input_tool.hotkeys import display_to_hotkey, hotkey_to_display


class HotkeyTests(unittest.TestCase):
    def test_round_trip(self):
        value = "<ctrl>+<shift>+<space>"
        self.assertEqual(display_to_hotkey(hotkey_to_display(value)), value)

    def test_plain_key_is_normalized(self):
        self.assertEqual(display_to_hotkey("Cmd + K"), "<cmd>+k")

    def test_letter_keys_are_displayed_uppercase(self):
        self.assertEqual(hotkey_to_display("<shift>+a"), "Shift + A")

    def test_right_shift_modifier_only_hotkey(self):
        self.assertEqual(hotkey_to_display("<right_shift>"), "Right Shift")
        self.assertEqual(display_to_hotkey("Right Shift"), "<right_shift>")

    def test_left_shift_modifier_only_hotkey(self):
        self.assertEqual(hotkey_to_display("<left_shift>"), "Left Shift")
        self.assertEqual(display_to_hotkey("Left Shift"), "<left_shift>")


if __name__ == "__main__":
    unittest.main()

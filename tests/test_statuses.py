import unittest

from voice_input_tool.statuses import HEARING, IDLE, labels_for


class StatusLabelsTests(unittest.TestCase):
    def test_idle_includes_hotkey(self):
        labels = labels_for(IDLE, "Ctrl + Shift + Space")
        self.assertIn("Ctrl + Shift + Space", labels.record_title)
        self.assertFalse(labels.animate_activity)

    def test_hearing_enables_activity_animation(self):
        self.assertTrue(labels_for(HEARING).animate_activity)


if __name__ == "__main__":
    unittest.main()

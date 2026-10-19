import unittest
from unittest.mock import Mock, patch

from voice_input_tool.settings_launcher import run_settings_window


class SettingsLauncherTests(unittest.TestCase):
    def test_standalone_loop_exits_on_close_instead_of_requiring_a_save(self):
        controller = Mock()
        with patch("Cocoa.NSApplication") as application, \
             patch("voice_input_tool.settings_ui.SettingsWindowController") as controller_class, \
             patch("PyObjCTools.AppHelper.stopEventLoop") as stop_loop, \
             patch("PyObjCTools.AppHelper.runEventLoop") as run_loop:
            controller_class.alloc.return_value.initWithCallback_.return_value = controller
            run_loop.side_effect = lambda: controller.on_close_callback()
            status = run_settings_window()
        self.assertEqual(status, 0)
        from Cocoa import NSApplicationActivationPolicyAccessory
        application.sharedApplication.return_value.setActivationPolicy_.assert_called_once_with(
            NSApplicationActivationPolicyAccessory
        )
        controller_class.alloc.return_value.initWithCallback_.assert_called_once_with(None)
        controller.show.assert_called_once_with()
        application.sharedApplication.return_value.activateIgnoringOtherApps_.assert_called_once_with(True)
        run_loop.assert_called_once_with()
        stop_loop.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()

"""Standalone settings-window launcher."""

import sys
import logging


def run_settings_window():
    try:
        from Cocoa import NSApplication, NSApplicationActivationPolicyAccessory
        from PyObjCTools import AppHelper
    except ImportError:
        print("PyObjCTools が利用できないため設定画面を開けません。", file=sys.stderr)
        return 1

    from voice_input_tool.settings_ui import SettingsWindowController

    app = NSApplication.sharedApplication()
    # A Python process starts as Prohibited (2): its floating window can receive
    # mouse clicks but the app cannot activate to receive keyboard input.
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    controller = SettingsWindowController.alloc().initWithCallback_(None)
    controller.on_close_callback = AppHelper.stopEventLoop
    controller.show()
    app.activateIgnoringOtherApps_(True)
    def report_input_state():
        logging.getLogger("voice_input").info(
            "Settings input state: policy=%s active=%s key_window=%s",
            app.activationPolicy(), app.isActive(), controller.window.isKeyWindow(),
        )
    AppHelper.callLater(0.5, report_input_state)
    AppHelper.runEventLoop()
    return 0

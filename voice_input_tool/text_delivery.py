"""Deliver saved text and distinguish a paste request from a confirmed receipt."""

from dataclasses import dataclass
import logging

from voice_input_tool.macos_text import copy_to_clipboard, insert_text_at_cursor, target_pid, application_for_pid
from voice_input_tool.native_bridge import native_paste_bridge_ready, write_output, wait_for_delivery

log = logging.getLogger("voice_input")


@dataclass(frozen=True)
class DeliveryResult:
    # paste_sent confirms the key event, not the contents of another app's editor.
    status: str

    def __bool__(self):
        return self.status == "paste_sent"


class TextDeliveryService:
    def __init__(self, native_output=False):
        self.native_output = native_output

    def deliver(self, text, target_app=None, target_pid_value=None):
        if not text or not text.strip():
            return DeliveryResult("failed")
        pid = target_pid_value if target_pid_value and target_pid_value > 0 else target_pid(target_app)
        if self.native_output and native_paste_bridge_ready():
            try:
                request_id = write_output(text, pid)
                status = wait_for_delivery(request_id)
                log.info("ネイティブ貼り付け結果: request=%s status=%s", request_id, status)
                # A timeout may occur after a paste. Re-pasting would duplicate text.
                return DeliveryResult(status)
            except Exception:
                log.exception("ネイティブ貼り付けの結果を確認できませんでした")
                return DeliveryResult("delivery_unknown")

        # Select exactly one transport; never queue a native paste and then paste
        # through Python too when the heartbeat happens to be absent.
        if pid and target_app is None:
            target_app = application_for_pid(pid)
            if target_app is None:
                return DeliveryResult("copied" if copy_to_clipboard(text) else "failed")
        if insert_text_at_cursor(text, target_app):
            return DeliveryResult("paste_sent")
        return DeliveryResult("copied" if copy_to_clipboard(text) else "failed")

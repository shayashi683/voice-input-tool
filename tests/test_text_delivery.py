import unittest
from unittest.mock import patch

from voice_input_tool.text_delivery import TextDeliveryService


class TextDeliveryServiceTests(unittest.TestCase):
    @patch('voice_input_tool.text_delivery.wait_for_delivery', return_value='paste_sent')
    @patch('voice_input_tool.text_delivery.insert_text_at_cursor')
    @patch('voice_input_tool.text_delivery.native_paste_bridge_ready', return_value=True)
    @patch('voice_input_tool.text_delivery.write_output', return_value='request-id')
    def test_native_delivery_requires_receipt(self, write, ready, insert, wait):
        service = TextDeliveryService(native_output=True)
        self.assertTrue(service.deliver('テスト', target_pid_value=42))
        write.assert_called_once_with('テスト', 42)
        wait.assert_called_once_with('request-id')
        insert.assert_not_called()

    @patch('voice_input_tool.text_delivery.wait_for_delivery', return_value='delivery_unknown')
    @patch('voice_input_tool.text_delivery.insert_text_at_cursor')
    @patch('voice_input_tool.text_delivery.native_paste_bridge_ready', return_value=True)
    @patch('voice_input_tool.text_delivery.write_output', return_value='request-id')
    def test_missing_receipt_does_not_cause_duplicate_paste(self, write, ready, insert, wait):
        result = TextDeliveryService(True).deliver('テスト', target_pid_value=42)
        self.assertFalse(result)
        self.assertEqual(result.status, 'delivery_unknown')
        insert.assert_not_called()

    @patch('voice_input_tool.text_delivery.target_pid', return_value=None)
    @patch('voice_input_tool.text_delivery.insert_text_at_cursor', return_value=True)
    @patch('voice_input_tool.text_delivery.native_paste_bridge_ready', return_value=False)
    @patch('voice_input_tool.text_delivery.write_output')
    def test_missing_bridge_uses_one_transport(self, write, ready, insert, pid):
        self.assertTrue(TextDeliveryService(True).deliver('テスト', target_app='target'))
        write.assert_not_called()
        insert.assert_called_once_with('テスト', 'target')

    @patch('voice_input_tool.text_delivery.copy_to_clipboard', return_value=False)
    @patch('voice_input_tool.text_delivery.insert_text_at_cursor', return_value=False)
    def test_copy_failure_is_not_reported_as_copied(self, insert, copy):
        self.assertEqual(TextDeliveryService().deliver('テスト').status, 'failed')

    @patch('voice_input_tool.text_delivery.copy_to_clipboard', return_value=True)
    @patch('voice_input_tool.text_delivery.insert_text_at_cursor', return_value=False)
    def test_successful_clipboard_fallback(self, insert, copy):
        self.assertEqual(TextDeliveryService().deliver('テスト').status, 'copied')

    @patch('voice_input_tool.text_delivery.application_for_pid', return_value=None)
    @patch('voice_input_tool.text_delivery.copy_to_clipboard', return_value=True)
    @patch('voice_input_tool.text_delivery.insert_text_at_cursor')
    def test_exited_target_never_pastes_into_another_app(self, insert, copy, app):
        self.assertEqual(TextDeliveryService().deliver('テスト', target_pid_value=42).status, 'copied')
        insert.assert_not_called()

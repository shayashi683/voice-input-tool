import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from voice_input_tool.native_bridge import NativeCommandReader, parse_command_line, _append_line, wait_for_delivery


class NativeBridgeTests(unittest.TestCase):
    def test_commands_arriving_during_startup_are_not_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'commands.jsonl'
            path.write_bytes(b'old\n')
            launch_offset = path.stat().st_size
            with path.open('ab') as handle:
                handle.write(b'start\nstop\n')
            reader = NativeCommandReader(str(path), start_offset=launch_offset)
            self.assertEqual(reader.read_new_commands(), ['start', 'stop'])

    def test_append_handles_short_writes_without_losing_utf8_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'output.jsonl')
            write = os.write
            with patch('voice_input_tool.native_bridge.os.write', side_effect=lambda fd, data: write(fd, data[:3])):
                _append_line(path, {'text': '日本語の長い文字起こし'})
            self.assertEqual(json.loads(Path(path).read_text())['text'], '日本語の長い文字起こし')

    def test_receipt_timeout_is_unknown(self):
        self.assertEqual(wait_for_delivery('11111111-1111-1111-1111-111111111111', timeout=0), 'delivery_unknown')

    def test_receipt_is_matched_to_request(self):
        with tempfile.TemporaryDirectory() as directory:
            request_id = '11111111-1111-1111-1111-111111111111'
            (Path(directory) / f'{request_id}.json').write_text(json.dumps({'request_id': request_id, 'status': 'copied'}))
            with patch('voice_input_tool.native_bridge.DELIVERY_RECEIPTS_DIR', Path(directory)):
                self.assertEqual(wait_for_delivery(request_id), 'copied')

    def test_parse_json_command_and_pid(self):
        parsed = parse_command_line('{"command":"start","target_pid":321}')
        self.assertEqual(parsed, {"command": "start", "target_pid": 321})

    def test_invalid_pid_is_discarded(self):
        parsed = parse_command_line('{"command":"toggle","target_pid":-1}')
        self.assertEqual(parsed, {"command": "toggle", "target_pid": None})

    def test_reader_waits_for_complete_line(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "commands.jsonl")
            reader = NativeCommandReader(path)
            with open(path, "ab") as file_handle:
                file_handle.write(b'{"command":"sta')
            self.assertEqual(reader.read_new_commands(), [])

            with open(path, "ab") as file_handle:
                file_handle.write(b'rt","target_pid":42}\n')
            commands = reader.read_new_commands()
            self.assertEqual(len(commands), 1)
            self.assertEqual(json.loads(commands[0])["target_pid"], 42)

    def test_reader_recovers_after_file_truncation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "commands.jsonl")
            reader = NativeCommandReader(path)
            with open(path, "ab") as file_handle:
                file_handle.write(b"partial")
            reader.read_new_commands()

            with open(path, "wb") as file_handle:
                file_handle.write(b"stop\n")
            self.assertEqual(reader.read_new_commands(), ["stop"])


if __name__ == "__main__":
    unittest.main()

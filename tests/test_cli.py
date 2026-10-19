import unittest
from unittest.mock import patch

from voice_input_tool.cli import build_parser


class CLITests(unittest.TestCase):
    def test_headless_and_llm_flags(self):
        args = build_parser().parse_args(["--headless", "--no-llm"])
        self.assertTrue(args.headless)
        self.assertTrue(args.no_llm)
        self.assertFalse(args.settings)

    def test_cloud_model_can_be_selected_for_retry(self):
        args = build_parser().parse_args(["--asr", "gpt-transcribe", "--retry-recording", "saved-id"])
        self.assertEqual(args.asr, "gpt-transcribe")
        self.assertEqual(args.retry_recording, "saved-id")

    def test_test_mode_receives_selected_backend(self):
        from voice_input_tool.cli import main
        with patch("voice_input_tool.cli.load_config", return_value={}), \
             patch("voice_input_tool.test_mode.run_test") as run_test:
            self.assertEqual(main(["--test", "--asr", "gpt-transcribe", "--no-llm"]), 0)
        run_test.assert_called_once_with(use_llm=False, config={"asr_backend": "gpt-transcribe"})


if __name__ == "__main__":
    unittest.main()

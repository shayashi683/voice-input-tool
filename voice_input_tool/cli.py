"""Command-line entrypoint and launch-mode selection."""

import argparse
import logging

from voice_input_tool.config import load_config
from voice_input_tool.logging_setup import configure_logging

configure_logging()
log = logging.getLogger("voice_input")


def build_parser():
    parser = argparse.ArgumentParser(description="Voice Input Tool - GPT-Transcribe / ReazonSpeech")
    parser.add_argument("--asr", choices=("gpt-transcribe", "reazonspeech"),
                        help="この実行で使う音声認識モデル")
    parser.add_argument("--llm", action="store_true", help="LLM句読点補正を有効化")
    parser.add_argument("--no-llm", action="store_true", help="LLM句読点補正を無効化")
    parser.add_argument("--test", action="store_true", help="テストWAVファイルで動作確認")
    parser.add_argument("--headless", action="store_true", help="メニューバーなしで音声入力エンジンのみ起動")
    parser.add_argument("--settings", action="store_true", help="設定画面だけを開く")
    history = parser.add_mutually_exclusive_group()
    history.add_argument("--history", action="store_true", help="保存した録音のID・状態を一覧表示")
    history.add_argument("--show-recording", metavar="ID", help="保存した文字起こしを表示")
    history.add_argument("--retry-recording", metavar="ID", help="保存音声を再文字起こし（自動貼り付けなし）")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    config = load_config()
    if args.asr:
        config["asr_backend"] = args.asr
    use_llm = (args.llm or config.get("use_llm", False)) and not args.no_llm

    if args.history or args.show_recording or args.retry_recording:
        from voice_input_tool.recording_history import run_history_command
        return run_history_command(args, config, use_llm)

    if args.settings:
        from voice_input_tool.settings_launcher import run_settings_window

        return run_settings_window()

    log.info("LLM補正: %s", "ON" if use_llm else "OFF")
    if args.test:
        from voice_input_tool.test_mode import run_test

        run_test(use_llm=use_llm, config=config)
        return 0

    if args.headless:
        from voice_input_tool.headless import run_headless_app

        run_headless_app(use_llm=use_llm, config=config)
        return 0

    _run_menu_bar_app(use_llm, config)
    return 0


def _run_menu_bar_app(use_llm, config):
    from voice_input_tool.engine import (
        AppHelper,
        HAS_APPKIT_APPLICATION,
        HAS_APP_HELPER,
        NSApplication,
        NSApplicationActivationPolicyAccessory,
        VoiceInputApp,
    )
    from voice_input_tool.status_bar_diagnostics import install_status_bar_diagnostics

    install_status_bar_diagnostics()
    if HAS_APPKIT_APPLICATION:
        try:
            NSApplication.sharedApplication().setActivationPolicy_(
                NSApplicationActivationPolicyAccessory
            )
            log.info("アプリ表示モード: accessory")
        except Exception:
            log.exception("アプリ表示モードの設定に失敗しました")
    if HAS_APP_HELPER:
        AppHelper.callLater(2.0, lambda: log.info("メニューバーイベントループ稼働中"))

    app = VoiceInputApp(use_llm=use_llm, config=config)
    app.prepare_audio_stream()
    log.info("メニューバーイベントループ開始")
    try:
        app.run()
    finally:
        app.shutdown()
    log.error("メニューバーイベントループが終了しました")

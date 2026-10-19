"""Shared filesystem paths for the voice input app."""

import os
from pathlib import Path


def _resolve_app_dir() -> Path:
    configured_dir = os.environ.get("VOICE_INPUT_TOOL_DIR")
    if configured_dir:
        return Path(configured_dir).expanduser().resolve()
    return Path(__file__).resolve().parent.parent


APP_DIR = _resolve_app_dir()

LOG_DIR = APP_DIR / "logs"
MODEL_DIR = str(APP_DIR / "models" / "sherpa-onnx-zipformer-ja-reazonspeech-2024-08-01")

RUNTIME_DIR = APP_DIR / "logs"
COMMAND_FILE_PATH = str(RUNTIME_DIR / "voice-input-command.txt")
OUTPUT_FILE_PATH = str(RUNTIME_DIR / "voice-input-output.jsonl")
STATUS_FILE_PATH = str(RUNTIME_DIR / "voice-input-status.json")
NATIVE_PASTE_READY_PATH = str(RUNTIME_DIR / "native-paste-ready.txt")
ENGINE_LOCK_PATH = str(RUNTIME_DIR / "voice-input-engine.lock")
RECORDINGS_DIR = APP_DIR / "recordings"
DELIVERY_RECEIPTS_DIR = RUNTIME_DIR / "delivery-receipts"

TYPING_INDICATOR_ICON_FRAMES = tuple(
    str(APP_DIR / "assets" / f"typing-indicator-{index}.svg")
    for index in range(6)
)

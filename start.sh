#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
export VOICE_INPUT_TOOL_DIR="$SCRIPT_DIR"

APP_LAUNCHER="/Applications/VoiceInputTool.app/Contents/MacOS/VoiceInputTool"
if [ ! -x "$APP_LAUNCHER" ]; then
    APP_LAUNCHER="$HOME/Applications/VoiceInputTool.app/Contents/MacOS/VoiceInputTool"
fi
if [ -x "$APP_LAUNCHER" ] && [ $# -eq 0 ]; then
    exec "$APP_LAUNCHER"
fi

source .venv-framework/bin/activate
exec python3 voice_input.py "$@"

#!/bin/bash

# Required parameters:
# @raycast.schemaVersion 1
# @raycast.title Voice Input Tool を終了
# @raycast.mode compact

# Optional parameters:
# @raycast.icon 🛑
# @raycast.packageName Voice Input Tool
# @raycast.description メニューバーの音声入力ツールを終了します

set -u

NATIVE_PATTERN="VoiceInputTool.app/Contents/MacOS/VoiceInputTool"
stopped=0

# SIGTERM で終了させる（ヘッドレスエンジンは SIGTERM を受けて後片付けする）
for pattern in "$NATIVE_PATTERN" "voice_input.py"; do
    if pgrep -f "$pattern" >/dev/null 2>&1; then
        pkill -f "$pattern" && stopped=1
    fi
done

if [ "$stopped" -eq 0 ]; then
    echo "🛑 起動していません"
    exit 0
fi

for _ in $(seq 1 10); do
    sleep 0.5
    if ! pgrep -f "$NATIVE_PATTERN" >/dev/null 2>&1 && ! pgrep -f "voice_input.py" >/dev/null 2>&1; then
        echo "🛑 終了しました"
        exit 0
    fi
done

echo "⚠️ 終了しきれていないプロセスがあります"
exit 1

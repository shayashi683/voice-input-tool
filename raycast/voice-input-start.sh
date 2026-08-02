#!/bin/bash

# Required parameters:
# @raycast.schemaVersion 1
# @raycast.title Voice Input Tool を起動
# @raycast.mode compact

# Optional parameters:
# @raycast.icon 🎙
# @raycast.packageName Voice Input Tool
# @raycast.description メニューバーの音声入力ツールを起動します（起動済みなら何もしません）

set -u

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_BUNDLE="$HOME/Applications/VoiceInputTool.app"
NATIVE_PATTERN="VoiceInputTool.app/Contents/MacOS/VoiceInputTool"
LOG_FILE="$APP_DIR/logs/raycast-start.log"

if pgrep -f "$NATIVE_PATTERN" >/dev/null 2>&1 || pgrep -f "voice_input.py" >/dev/null 2>&1; then
    echo "🎙 すでに起動しています"
    exit 0
fi

mkdir -p "$APP_DIR/logs"

# .app があればそちらを優先する。LaunchServices 経由で起動すると
# マイク/アクセシビリティ権限がアプリ自身に紐づくため
if [ -x "$APP_BUNDLE/Contents/MacOS/VoiceInputTool" ]; then
    if open -a "$APP_BUNDLE"; then
        echo "🎙 起動しました（VoiceInputTool.app）"
        exit 0
    fi
    echo "⚠️ VoiceInputTool.app の起動に失敗しました"
    exit 1
fi

# .app が無い場合は start.sh を Raycast のプロセスから切り離して起動する
nohup /bin/bash "$APP_DIR/start.sh" >>"$LOG_FILE" 2>&1 &
disown

# メニューバーに出るまで数秒かかるため、プロセスの生存だけ確認する
for _ in $(seq 1 20); do
    sleep 0.5
    if pgrep -f "voice_input.py" >/dev/null 2>&1; then
        echo "🎙 起動しました（start.sh）"
        exit 0
    fi
done

echo "⚠️ 起動を確認できませんでした: $LOG_FILE を確認してください"
exit 1

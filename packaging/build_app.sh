#!/bin/bash
# VoiceInputTool.app をビルドする。
#
#   ./packaging/build_app.sh              # ~/Applications へ配置
#   ./packaging/build_app.sh /path/to/dir # 配置先を指定
#
# リポジトリの場所は .app の中からは辿れないため、このスクリプトが
# ビルド時に -DVIT_WORK_DIR で埋め込む。リポジトリを移動した場合は
# 再ビルドするか、環境変数 VOICE_INPUT_TOOL_DIR で上書きする。

set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST_DIR="${1:-$HOME/Applications}"
BUNDLE="$DEST_DIR/VoiceInputTool.app"
MACOS_DIR="$BUNDLE/Contents/MacOS"

if [ ! -f "$APP_DIR/voice_input.py" ]; then
    echo "リポジトリのルートが見つかりません: $APP_DIR" >&2
    exit 1
fi

if [ ! -x "$APP_DIR/.venv-framework/bin/python3" ]; then
    echo "⚠️ .venv-framework が見つかりません。先にセットアップを済ませてください: $APP_DIR/.venv-framework" >&2
fi

# 既存の .app は権限（マイク/アクセシビリティ）が実行ファイルに紐づくため、
# バンドルごと消さずに実行ファイルだけ差し替える
mkdir -p "$MACOS_DIR"
cp "$APP_DIR/packaging/VoiceInputTool-Info.plist" "$BUNDLE/Contents/Info.plist"

clang \
    -DVIT_WORK_DIR="\"$APP_DIR\"" \
    -fobjc-arc \
    -mmacosx-version-min=13.0 \
    -framework Cocoa \
    -framework ApplicationServices \
    -framework Carbon \
    -o "$MACOS_DIR/VoiceInputTool" \
    "$APP_DIR/native/native_status_app.m"

# 未署名のままだと、macOS が実行ファイルの変更を検知して権限を失わせることがある
codesign --force --sign - "$BUNDLE" 2>/dev/null || \
    echo "⚠️ ad-hoc 署名に失敗しました（動作はしますが、権限を再許可する必要があるかもしれません）" >&2

echo "✅ ビルドしました: $BUNDLE"
echo "   参照するリポジトリ: $APP_DIR"

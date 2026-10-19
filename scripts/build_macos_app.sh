#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="VoiceInputTool"
APP_BUNDLE_PATH="${APP_BUNDLE_PATH:-$HOME/Applications/$APP_NAME.app}"
APP_WORKDIR="${APP_WORKDIR:-$ROOT_DIR}"
STAGING_DIR="$(mktemp -d "${TMPDIR:-/tmp}/voice-input-app.XXXXXX")"
STAGING_APP="$STAGING_DIR/$APP_NAME.app"
NATIVE_SOURCES=(
    "$ROOT_DIR/native/native_status_app.m"
    "$ROOT_DIR/native/VITCursorMicIndicator.m"
    "$ROOT_DIR/native/VITGlobalHotKey.m"
    "$ROOT_DIR/native/VITNativeSupport.m"
    "$ROOT_DIR/native/VITSetupGuideController.m"
    "$ROOT_DIR/native/VITTextPasteController.m"
    "$ROOT_DIR/native/VITUpdateController.m"
)

case "$APP_BUNDLE_PATH" in
    *.app)
        ;;
    *)
        echo "APP_BUNDLE_PATH must end with .app: $APP_BUNDLE_PATH" >&2
        exit 2
        ;;
esac

cleanup() {
    rm -rf "$STAGING_DIR"
}
trap cleanup EXIT

mkdir -p "$STAGING_APP/Contents/MacOS" "$STAGING_APP/Contents/Resources"

cp "$ROOT_DIR/packaging/VoiceInputTool-Info.plist" "$STAGING_APP/Contents/Info.plist"
printf 'APPL????' > "$STAGING_APP/Contents/PkgInfo"
printf '%s\n' "$APP_WORKDIR" > "$STAGING_APP/Contents/Resources/workdir.txt"

if [ -f "$ROOT_DIR/packaging/AppIcon.icns" ]; then
    cp "$ROOT_DIR/packaging/AppIcon.icns" "$STAGING_APP/Contents/Resources/AppIcon.icns"
elif [ -f "$APP_BUNDLE_PATH/Contents/Resources/AppIcon.icns" ]; then
    cp "$APP_BUNDLE_PATH/Contents/Resources/AppIcon.icns" "$STAGING_APP/Contents/Resources/AppIcon.icns"
fi

if [ -f "$ROOT_DIR/packaging/StatusIconTemplate.png" ]; then
    cp "$ROOT_DIR/packaging/StatusIconTemplate.png" "$STAGING_APP/Contents/Resources/StatusIconTemplate.png"
fi

clang \
    -fobjc-arc \
    -mmacosx-version-min=13.0 \
    -I"$ROOT_DIR/native" \
    "${NATIVE_SOURCES[@]}" \
    -o "$STAGING_APP/Contents/MacOS/$APP_NAME" \
    -framework Cocoa \
    -framework ApplicationServices \
    -framework Carbon

codesign --force --sign - "$STAGING_APP" >/dev/null

mkdir -p "$(dirname "$APP_BUNDLE_PATH")"
rm -rf "$APP_BUNDLE_PATH"
mv "$STAGING_APP" "$APP_BUNDLE_PATH"

echo "Built $APP_BUNDLE_PATH"

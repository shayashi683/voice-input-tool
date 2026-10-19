#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$ROOT_DIR/packaging/VoiceInputTool-Info.plist")"
# The Python/model runtime is unchanged; keep the version used by existing PKGs.
RUNTIME_VERSION="${VOICE_INPUT_RUNTIME_VERSION:-1.0.2}"
RUNTIME_BASE_URL="https://voice-input-tool.pages.dev/downloads/runtime"
RUNTIME_ARCHIVE="voiceinput-runtime-${RUNTIME_VERSION}-mac-arm64.tar.gz"
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/voice-input-pkg.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
PAYLOAD="$STAGE/root/Library/Application Support/VoiceInputToolInstaller/payload"
OUT="$ROOT_DIR/landing/downloads"
mkdir -p "$PAYLOAD" "$STAGE/scripts" "$OUT"
APP_BUNDLE_PATH="$STAGE/root/Applications/VoiceInputTool.app" \
  APP_WORKDIR='~/Library/Application Support/VoiceInputTool' \
  "$ROOT_DIR/scripts/build_macos_app.sh"
# Explicit source allowlist: never package local configuration, keys, or recordings.
COPYFILE_DISABLE=1 tar -C "$ROOT_DIR" --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='.DS_Store' -cf - README.md requirements.txt start.sh voice_input.py \
  native packaging scripts voice_input_tool tests assets | tar -C "$PAYLOAD" -xf -
sed -e "s|@RUNTIME_BASE_URL@|$RUNTIME_BASE_URL|g" \
  -e "s|@RUNTIME_ARCHIVE_NAME@|$RUNTIME_ARCHIVE|g" \
  -e "s|@RUNTIME_MANIFEST_NAME@|$RUNTIME_ARCHIVE.manifest|g" \
  "$ROOT_DIR/packaging/postinstall.sh.in" > "$STAGE/scripts/postinstall"
chmod 755 "$STAGE/scripts/postinstall"
bash -n "$STAGE/scripts/postinstall"
if grep -q '@RUNTIME_' "$STAGE/scripts/postinstall"; then
  echo 'Unexpanded runtime placeholder' >&2
  exit 1
fi
pkgbuild --analyze --root "$STAGE/root" "$STAGE/components.plist"
/usr/libexec/PlistBuddy -c 'Set :0:BundleIsRelocatable false' "$STAGE/components.plist"
pkgbuild --root "$STAGE/root" --component-plist "$STAGE/components.plist" --scripts "$STAGE/scripts" \
  --identifier com.nocall.voiceinputtool.pkg --version "$VERSION" \
  --install-location / "$STAGE/VoiceInputTool.pkg"
cp "$STAGE/VoiceInputTool.pkg" "$OUT/VoiceInputTool-$VERSION-mac-arm64.pkg"
(cd "$OUT" && shasum -a 256 "VoiceInputTool-$VERSION-mac-arm64.pkg" > "VoiceInputTool-$VERSION-mac-arm64.pkg.sha256")
python3 "$ROOT_DIR/scripts/write_update_manifest.py"
echo "Built $OUT/VoiceInputTool-$VERSION-mac-arm64.pkg"

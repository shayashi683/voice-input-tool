#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$ROOT_DIR/packaging/VoiceInputTool-Info.plist")"
RUNTIME_SOURCE="${VOICE_INPUT_RUNTIME_SOURCE:-$ROOT_DIR/.runtime/VoiceInputTool}"
OUT_DIR="$ROOT_DIR/landing/downloads/runtime"
ARCHIVE_NAME="voiceinput-runtime-${APP_VERSION}-mac-arm64.tar.gz"
ARCHIVE_PATH="$OUT_DIR/$ARCHIVE_NAME"
MANIFEST_PATH="$OUT_DIR/$ARCHIVE_NAME.manifest"
CHUNK_SIZE="${VOICE_INPUT_RUNTIME_CHUNK_SIZE:-20m}"

if [ ! -d "$RUNTIME_SOURCE/.python" ] ||
   [ ! -d "$RUNTIME_SOURCE/.venv-framework" ] ||
   [ ! -d "$RUNTIME_SOURCE/models" ]; then
    echo "Bundled runtime is missing. Run scripts/build_runtime_payload.sh first." >&2
    exit 1
fi

mkdir -p "$OUT_DIR"
find "$OUT_DIR" -type f -name "voiceinput-runtime-*-mac-arm64.tar.gz*" -delete

export COPYFILE_DISABLE=1
tar -C "$RUNTIME_SOURCE" -czf "$ARCHIVE_PATH" .python .venv-framework models

archive_sha="$(shasum -a 256 "$ARCHIVE_PATH" | awk '{print $1}')"
archive_size="$(stat -f %z "$ARCHIVE_PATH")"
split -b "$CHUNK_SIZE" -d -a 3 "$ARCHIVE_PATH" "$ARCHIVE_PATH.part-"

{
    printf 'archive %s %s %s\n' "$archive_sha" "$ARCHIVE_NAME" "$archive_size"
    for part in "$ARCHIVE_PATH".part-*; do
        part_name="$(basename "$part")"
        part_sha="$(shasum -a 256 "$part" | awk '{print $1}')"
        part_size="$(stat -f %z "$part")"
        printf 'part %s %s %s\n' "$part_sha" "$part_name" "$part_size"
    done
} > "$MANIFEST_PATH"

rm -f "$ARCHIVE_PATH"
echo "Created runtime chunks in $OUT_DIR"

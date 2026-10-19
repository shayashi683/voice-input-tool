#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_DIR="$(mktemp -d "${TMPDIR:-/tmp}/voice-input-native-test.XXXXXX")"
trap 'rm -rf "$TEST_DIR"' EXIT
clang -fobjc-arc -mmacosx-version-min=13.0 -I"$ROOT_DIR/native" \
  "$ROOT_DIR/tests/native_output_test.m" \
  "$ROOT_DIR/native/VITCursorMicIndicator.m" \
  "$ROOT_DIR/native/VITGlobalHotKey.m" \
  "$ROOT_DIR/native/VITNativeSupport.m" \
  "$ROOT_DIR/native/VITSetupGuideController.m" \
  "$ROOT_DIR/native/VITTextPasteController.m" \
  -o "$TEST_DIR/native-output-test" \
  -framework Cocoa -framework ApplicationServices -framework Carbon
VOICE_INPUT_TOOL_DIR="$TEST_DIR" "$TEST_DIR/native-output-test"
clang -fobjc-arc -mmacosx-version-min=13.0 -I"$ROOT_DIR/native" \
  "$ROOT_DIR/tests/native_setup_guide_test.m" \
  "$ROOT_DIR/native/VITCursorMicIndicator.m" \
  "$ROOT_DIR/native/VITGlobalHotKey.m" \
  "$ROOT_DIR/native/VITNativeSupport.m" \
  "$ROOT_DIR/native/VITSetupGuideController.m" \
  "$ROOT_DIR/native/VITTextPasteController.m" \
  -o "$TEST_DIR/native-setup-test" \
  -framework Cocoa -framework ApplicationServices -framework Carbon
VOICE_INPUT_TOOL_DIR="$TEST_DIR/setup" "$TEST_DIR/native-setup-test" "$@"

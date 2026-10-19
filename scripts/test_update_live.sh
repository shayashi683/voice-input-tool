#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_DIR="$(mktemp -d "${TMPDIR:-/tmp}/voice-input-update-live.XXXXXX")"
trap 'rm -rf "$TEST_DIR"' EXIT
clang -fobjc-arc -mmacosx-version-min=13.0 -I"$ROOT_DIR/native" \
  "$ROOT_DIR/tests/native_update_live_test.m" -o "$TEST_DIR/update-live-test" -framework Cocoa
"$TEST_DIR/update-live-test"

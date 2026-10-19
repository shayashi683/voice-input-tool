#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
python3 scripts/verify_release.py
npx --yes wrangler@4.151.1 pages deploy landing \
  --project-name voice-input-tool --branch main

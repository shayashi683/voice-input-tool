#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_BUNDLE_PATH="${APP_BUNDLE_PATH:-$HOME/Applications/VoiceInputTool.app}"
VENV_DIR="$ROOT_DIR/.venv-framework"
MODEL_NAME="sherpa-onnx-zipformer-ja-reazonspeech-2024-08-01"
MODEL_DIR="$ROOT_DIR/models/$MODEL_NAME"
MODEL_ARCHIVE="$ROOT_DIR/models/$MODEL_NAME.tar.bz2"
MODEL_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/$MODEL_NAME.tar.bz2"
INSTALL_DEPS=1
INSTALL_MODEL=1
BUILD_APP=1
OPEN_APP=0

usage() {
    cat <<'EOF'
Usage: scripts/install_mac_app.sh [options]

Options:
  --skip-deps       Python dependencies are already installed.
  --skip-model      Do not download the speech recognition model.
  --skip-app        Do not rebuild VoiceInputTool.app.
  --open            Open VoiceInputTool.app after installation.
  -h, --help        Show this help.

Set OPENAI_API_KEY in the environment to save it to .env.
You can also enter it in the app's Settings after installation.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --skip-deps)
            INSTALL_DEPS=0
            ;;
        --skip-model)
            INSTALL_MODEL=0
            ;;
        --skip-app)
            BUILD_APP=0
            ;;
        --open)
            OPEN_APP=1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
    shift
done

choose_python() {
    if command -v python3.11 >/dev/null 2>&1; then
        command -v python3.11
        return
    fi
    if command -v python3 >/dev/null 2>&1; then
        command -v python3
        return
    fi
    echo "Python 3.11 or python3 is required." >&2
    exit 1
}

save_openai_key() {
    local key_python
    key_python="$(choose_python)"
    VOICE_INPUT_ENV_FILE="$ROOT_DIR/.env" "$key_python" - <<'PY'
import os
from pathlib import Path
import re
import tempfile

api_key = os.environ["OPENAI_API_KEY"]
if any(ord(char) < 32 or ord(char) == 127 for char in api_key):
    raise SystemExit("OpenAI API key must not contain control characters.")
api_key = api_key.strip()
if not api_key:
    raise SystemExit("OpenAI API key must not be blank.")

env_file = Path(os.environ["VOICE_INPUT_ENV_FILE"])
lines = env_file.read_text().splitlines() if env_file.exists() else []
lines = [line for line in lines if not re.match(r"^\s*(?:export\s+)?OPENAI_API_KEY\s*=", line)]
lines.append("OPENAI_API_KEY=" + api_key)
fd, temp_name = tempfile.mkstemp(prefix=".env-", dir=str(env_file.parent))
try:
    with os.fdopen(fd, "w") as handle:
        handle.write("\n".join(lines) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_name, env_file)
finally:
    if os.path.exists(temp_name):
        os.unlink(temp_name)
PY
}

ensure_model() {
    if [ -f "$MODEL_DIR/encoder-epoch-99-avg-1.int8.onnx" ] &&
       [ -f "$MODEL_DIR/decoder-epoch-99-avg-1.int8.onnx" ] &&
       [ -f "$MODEL_DIR/joiner-epoch-99-avg-1.int8.onnx" ] &&
       [ -f "$MODEL_DIR/tokens.txt" ]; then
        return
    fi

    if [ "$INSTALL_MODEL" -eq 0 ]; then
        echo "Speech model is missing. Re-run without --skip-model before recording." >&2
        return
    fi

    if ! command -v curl >/dev/null 2>&1; then
        echo "curl is required to download the speech model." >&2
        exit 1
    fi

    mkdir -p "$ROOT_DIR/models"
    if [ ! -f "$MODEL_ARCHIVE" ]; then
        curl -L "$MODEL_URL" -o "$MODEL_ARCHIVE"
    fi
    tar xjf "$MODEL_ARCHIVE" -C "$ROOT_DIR/models"
}

cd "$ROOT_DIR"

if [ "$INSTALL_DEPS" -eq 1 ]; then
    PYTHON_BIN="$(choose_python)"
    if [ ! -x "$VENV_DIR/bin/python3" ]; then
        "$PYTHON_BIN" -m venv "$VENV_DIR"
    fi
    "$VENV_DIR/bin/python3" -m pip install --upgrade pip
    "$VENV_DIR/bin/python3" -m pip install -r "$ROOT_DIR/requirements.txt"
fi

if [ "${OPENAI_API_KEY:-}" ]; then
    save_openai_key
fi

ensure_model
if [ "$BUILD_APP" -eq 1 ]; then
    APP_BUNDLE_PATH="$APP_BUNDLE_PATH" "$ROOT_DIR/scripts/build_macos_app.sh"
fi

if [ "$OPEN_APP" -eq 1 ]; then
    open "$APP_BUNDLE_PATH"
fi

echo "Done. App: $APP_BUNDLE_PATH"

#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="${VOICE_INPUT_RUNTIME_DIR:-$ROOT_DIR/.runtime/VoiceInputTool}"
PYTHON_INSTALL_DIR="$RUNTIME_DIR/.python"
VENV_DIR="$RUNTIME_DIR/.venv-framework"
MODEL_NAME="sherpa-onnx-zipformer-ja-reazonspeech-2024-08-01"
MODEL_DIR="$RUNTIME_DIR/models/$MODEL_NAME"
PYTHON_VERSION="${VOICE_INPUT_PYTHON_VERSION:-3.11}"
UV_CACHE_DIR="${UV_CACHE_DIR:-/private/tmp/voice-input-uv-cache}"

default_model_source() {
    local app_support="$HOME/Library/Application Support/VoiceInputTool/models/$MODEL_NAME"
    if [ -d "$ROOT_DIR/models/$MODEL_NAME" ]; then
        printf '%s\n' "$ROOT_DIR/models/$MODEL_NAME"
    elif [ -d "$app_support" ]; then
        printf '%s\n' "$app_support"
    else
        printf '%s\n' ""
    fi
}

MODEL_SOURCE="${VOICE_INPUT_MODEL_SOURCE:-$(default_model_source)}"

if ! command -v uv >/dev/null 2>&1; then
    echo "uv is required to build the bundled runtime." >&2
    exit 1
fi

rm -rf "$RUNTIME_DIR"
mkdir -p "$PYTHON_INSTALL_DIR" "$MODEL_DIR"

UV_CACHE_DIR="$UV_CACHE_DIR" uv python install "$PYTHON_VERSION" --install-dir "$PYTHON_INSTALL_DIR"

PYTHON_BIN="$(
    find "$PYTHON_INSTALL_DIR" -path "*/bin/python3" -perm -111 -print | head -n 1
)"
if [ -z "$PYTHON_BIN" ]; then
    echo "Could not find bundled python3 under $PYTHON_INSTALL_DIR" >&2
    exit 1
fi

"$PYTHON_BIN" -m venv "$VENV_DIR"
"$VENV_DIR/bin/python3" -m pip install --upgrade pip
"$VENV_DIR/bin/python3" -m pip install --no-cache-dir -r "$ROOT_DIR/requirements.txt"

if [ -z "$MODEL_SOURCE" ] || [ ! -d "$MODEL_SOURCE" ]; then
    echo "Speech model not found. Set VOICE_INPUT_MODEL_SOURCE to a prepared model directory." >&2
    exit 1
fi

for file in \
    encoder-epoch-99-avg-1.int8.onnx \
    decoder-epoch-99-avg-1.int8.onnx \
    joiner-epoch-99-avg-1.int8.onnx \
    tokens.txt
do
    if [ ! -f "$MODEL_SOURCE/$file" ]; then
        echo "Missing model file: $MODEL_SOURCE/$file" >&2
        exit 1
    fi
    cp "$MODEL_SOURCE/$file" "$MODEL_DIR/$file"
done

find "$VENV_DIR" -type d \( -name __pycache__ -o -name tests -o -name test \) -prune -exec rm -rf {} +
find "$VENV_DIR" -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete
rm -rf "$PYTHON_INSTALL_DIR/.temp" "$PYTHON_INSTALL_DIR/.lock" "$PYTHON_INSTALL_DIR/.gitignore"
find "$RUNTIME_DIR" -name "._*" -delete

echo "Created bundled runtime at $RUNTIME_DIR"

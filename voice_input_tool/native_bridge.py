"""File-based bridge shared with the native status app."""

import json
import logging
import os
import time

from voice_input_tool.app_paths import (
    COMMAND_FILE_PATH,
    NATIVE_PASTE_READY_PATH,
    OUTPUT_FILE_PATH,
    STATUS_FILE_PATH,
)

log = logging.getLogger("voice_input")


def native_paste_bridge_ready(max_age_seconds=5.0):
    try:
        age = time.time() - os.path.getmtime(NATIVE_PASTE_READY_PATH)
        return age <= max_age_seconds
    except Exception:
        return False


def ensure_bridge_files():
    os.makedirs(os.path.dirname(COMMAND_FILE_PATH), exist_ok=True)
    with open(COMMAND_FILE_PATH, "a", encoding="utf-8"):
        pass
    with open(OUTPUT_FILE_PATH, "a", encoding="utf-8"):
        pass


def write_status(status, title, record_title, use_llm):
    native_titles = {
        "idle": "🎙",
        "starting": "⏳",
        "listening": "🟢",
        "hearing": "•••",
        "processing": "📝",
        "correcting": "AI",
        "polishing": "✨",
        "inserting": "⌨",
    }
    payload = {
        "status": status,
        "title": title or native_titles.get(status, "VI"),
        "record_title": record_title,
        "llm_title": "LLM補正: ON" if use_llm else "LLM補正: OFF",
        "updated_at": time.time(),
    }
    try:
        os.makedirs(os.path.dirname(STATUS_FILE_PATH), exist_ok=True)
        tmp_path = f"{STATUS_FILE_PATH}.{os.getpid()}.{time.time_ns()}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp_path, STATUS_FILE_PATH)
    except Exception:
        log.exception("ステータスファイル更新に失敗しました")


def write_output(text, pid):
    payload = {
        "text": text,
        "pid": pid,
        "created_at": time.time(),
    }
    os.makedirs(os.path.dirname(OUTPUT_FILE_PATH), exist_ok=True)
    with open(OUTPUT_FILE_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def parse_command_line(line):
    line = line.strip()
    if not line:
        return None

    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return {"command": line, "target_pid": None}

    if not isinstance(payload, dict):
        return {"command": line, "target_pid": None}

    command = str(payload.get("command", "")).strip()
    if not command:
        return None

    target_pid = payload.get("target_pid")
    try:
        target_pid = int(target_pid)
    except (TypeError, ValueError):
        target_pid = None
    if target_pid is not None and target_pid <= 0:
        target_pid = None

    return {"command": command, "target_pid": target_pid}


class NativeCommandReader:
    def __init__(self, path=COMMAND_FILE_PATH):
        self.path = path
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "a", encoding="utf-8"):
            pass
        self.offset = os.path.getsize(self.path)

    def read_new_commands(self):
        current_size = os.path.getsize(self.path)
        if current_size < self.offset:
            self.offset = 0
        if current_size == self.offset:
            return []

        # バイト単位のオフセットで読み進めるためバイナリで開く。
        # 書き込み途中の行（末尾に改行がない部分）は消費せず次回に回す
        with open(self.path, "rb") as f:
            f.seek(self.offset)
            data = f.read()
        end = data.rfind(b"\n")
        if end < 0:
            return []
        self.offset += end + 1
        return data[:end].decode("utf-8", errors="replace").splitlines()

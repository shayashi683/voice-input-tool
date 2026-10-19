"""File-based bridge shared with the native status app."""

import json
import logging
import os
import time
import uuid
import fcntl

from voice_input_tool.app_paths import (
    COMMAND_FILE_PATH,
    NATIVE_PASTE_READY_PATH,
    OUTPUT_FILE_PATH,
    STATUS_FILE_PATH,
    DELIVERY_RECEIPTS_DIR,
)
from voice_input_tool.statuses import labels_for

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


def _append_line(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    encoded = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        remaining = memoryview(encoded)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("ブリッジファイルへの書き込みが中断されました")
            remaining = remaining[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_status(status, title, record_title, use_llm):
    payload = {
        "status": status,
        "title": title or labels_for(status).title,
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
    request_id = str(uuid.uuid4())
    payload = {
        "request_id": request_id,
        "text": text,
        "pid": pid,
        "created_at": time.time(),
        "expires_at": time.time() + 10.0,
    }
    _append_line(OUTPUT_FILE_PATH, payload)
    return request_id


def wait_for_delivery(request_id, timeout=3.0):
    path = DELIVERY_RECEIPTS_DIR / f"{uuid.UUID(request_id)}.json"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            payload = json.loads(path.read_text())
            if payload.get("request_id") == request_id and payload.get("status") in {"paste_sent", "copied", "failed"}:
                return payload["status"]
        except (OSError, ValueError):
            pass
        time.sleep(0.05)
    return "delivery_unknown"


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
    def __init__(self, path=COMMAND_FILE_PATH, start_offset=None):
        self.path = path
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "a", encoding="utf-8"):
            pass
        size = os.path.getsize(self.path)
        self.offset = min(size, max(0, int(start_offset))) if start_offset is not None else size
        self.pending = b""

    def read_new_commands(self):
        current_size = os.path.getsize(self.path)
        if current_size < self.offset:
            self.offset = 0
            self.pending = b""
        if current_size == self.offset:
            return []

        with open(self.path, "rb") as f:
            f.seek(self.offset)
            chunk = f.read()
            self.offset = f.tell()
        data = self.pending + chunk
        lines = data.split(b"\n")
        self.pending = lines.pop()
        commands = []
        for line in lines:
            try:
                commands.append(line.decode("utf-8"))
            except UnicodeDecodeError:
                log.exception("コマンド行をUTF-8として読み込めませんでした")
        return commands

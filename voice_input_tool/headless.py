"""Native status-app command loop for the Python engine."""

import fcntl
import logging
import os
import signal
import threading
import time

from voice_input_tool.app_paths import ENGINE_LOCK_PATH
from voice_input_tool.engine import VoiceInputApp
from voice_input_tool.macos_text import request_accessibility_permission
from voice_input_tool.native_bridge import (
    NativeCommandReader,
    ensure_bridge_files,
    native_paste_bridge_ready,
    parse_command_line,
)
from voice_input_tool.statuses import IDLE, PROCESSING

log = logging.getLogger("voice_input")

ENGINE_TAKEOVER_TIMEOUT_SECONDS = 3.0


def _try_lock(handle):
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def acquire_engine_lock(
    path=ENGINE_LOCK_PATH,
    *,
    timeout_seconds=ENGINE_TAKEOVER_TIMEOUT_SECONDS,
    sleeper=time.sleep,
    clock=time.monotonic,
):
    """Acquire the single-engine lock, replacing an orphaned older engine."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    handle = open(path, "a+", encoding="utf-8")
    if not _try_lock(handle):
        handle.seek(0)
        raw_pid = handle.read().strip()
        try:
            previous_pid = int(raw_pid)
        except ValueError:
            previous_pid = None

        if previous_pid and previous_pid != os.getpid():
            log.warning("旧音声エンジンを停止して引き継ぎます: pid=%d", previous_pid)
            try:
                os.kill(previous_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except PermissionError:
                log.exception("旧音声エンジンを停止できません: pid=%d", previous_pid)
                handle.close()
                return None

        deadline = clock() + timeout_seconds
        while clock() < deadline and not _try_lock(handle):
            sleeper(0.05)

        if not _try_lock(handle):
            log.error("別の音声エンジンが動作中のため重複起動を中止します: pid=%s", raw_pid or "unknown")
            handle.close()
            return None

    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    os.fsync(handle.fileno())
    return handle


def release_engine_lock(handle):
    if handle is None:
        return
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def run_headless_app(use_llm=False, config=None):
    engine_lock = acquire_engine_lock()
    if engine_lock is None:
        return

    app = None
    try:
        # The launcher snapshots the command offset before creating this process.
        # Commands arriving during Python imports or microphone startup are retained.
        ensure_bridge_files()
        command_reader = NativeCommandReader(start_offset=os.environ.get("VOICE_INPUT_COMMAND_OFFSET"))
        should_quit = threading.Event()

        def handle_signal(_signum, _frame):
            should_quit.set()

        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)
        app = VoiceInputApp(
            use_llm=use_llm,
            headless=True,
            native_output=True,
            config=config,
        )
        app._set_status(PROCESSING if app._processor.busy else IDLE, force=True)
        app.prepare_audio_stream()

        log.info("ヘッドレス音声入力エンジン起動: pid=%d command_file=%s", os.getpid(), command_reader.path)

        if not native_paste_bridge_ready():
            request_accessibility_permission(prompt=True)
        handlers = {
            "stop": lambda _pid: app.stop_recording(),
            "toggle_llm": lambda _pid: app.toggle_llm(),
        }
        while not should_quit.is_set():
            time.sleep(0.2)
            try:
                command_lines = command_reader.read_new_commands()
            except Exception:
                log.exception("コマンドファイルの読み込みに失敗しました")
                continue

            for command_line in command_lines:
                parsed = parse_command_line(command_line)
                if not parsed:
                    continue
                command = parsed["command"]
                target_pid = parsed["target_pid"]
                log.info("コマンド受信: %s target_pid=%s", command, target_pid)

                if command == "toggle":
                    app.toggle_recording(target_pid=target_pid)
                elif command == "start":
                    app.start_recording(target_pid=target_pid)
                elif command == "quit":
                    should_quit.set()
                elif command in handlers:
                    handlers[command](target_pid)
                else:
                    log.warning("未知のコマンド: %s", command)
                if should_quit.is_set():
                    break
    finally:
        if app is not None:
            app.shutdown()
        release_engine_lock(engine_lock)
        log.info("ヘッドレス音声入力エンジン終了")

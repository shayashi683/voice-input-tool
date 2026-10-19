"""macOS text insertion helpers."""

import logging
import time

import pyperclip

log = logging.getLogger("voice_input")

try:
    from ApplicationServices import (
        AXIsProcessTrusted,
        AXIsProcessTrustedWithOptions,
        CGEventCreateKeyboardEvent,
        CGEventKeyboardSetUnicodeString,
        CGEventPost,
        CGEventPostToPid,
        CGEventSetFlags,
        kAXTrustedCheckOptionPrompt,
        kCGEventFlagMaskCommand,
        kCGHIDEventTap,
    )
    from AppKit import NSApplicationActivateIgnoringOtherApps, NSWorkspace, NSRunningApplication

    HAS_CURSOR_INSERT = True
except ImportError:
    HAS_CURSOR_INSERT = False


def get_frontmost_application():
    if not HAS_CURSOR_INSERT:
        return None
    try:
        return NSWorkspace.sharedWorkspace().frontmostApplication()
    except Exception as e:
        log.warning("前面アプリ取得エラー: %s", e)
        return None


def application_for_pid(pid):
    if not HAS_CURSOR_INSERT:
        return None
    return NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)


def insert_text_at_cursor(text, target_app=None):
    if not text:
        return False

    if not copy_to_clipboard(text):
        return False

    if not HAS_CURSOR_INSERT:
        log.warning("カーソル位置への入力に必要なmacOS APIを読み込めません")
        return False

    try:
        if not AXIsProcessTrusted():
            log.warning("アクセシビリティ権限がないため、カーソル位置へ入力できません")
            return False
    except Exception as e:
        log.warning("アクセシビリティ権限確認エラー: %s", e)
        return False

    try:
        if target_app is not None:
            target_app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
            time.sleep(0.25)
            if target_pid(get_frontmost_application()) != target_pid(target_app):
                log.warning("貼り付け対象を前面化できません。テキストはコピー済みです")
                return False
        send_paste_shortcut()
        return True
    except Exception as e:
        log.error("カーソル位置への入力に失敗しました: %s", e)
        return False


def copy_to_clipboard(text):
    try:
        pyperclip.copy(text)
        return True
    except Exception as e:
        log.error("クリップボードへのコピーに失敗しました: %s", e)
        return False


def target_pid(target_app=None):
    try:
        if target_app is not None:
            return int(target_app.processIdentifier())
    except Exception:
        return None
    return None


def send_paste_shortcut(target_app=None):
    if not HAS_CURSOR_INSERT:
        return

    if target_app is not None:
        try:
            target_app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
            time.sleep(0.25)
        except Exception as e:
            log.warning("貼り付け対象アプリの前面化に失敗しました: %s", e)

    command_keycode = 55
    v_keycode = 9
    flags = kCGEventFlagMaskCommand

    for keycode, key_down, event_flags in (
        (command_keycode, True, flags),
        (v_keycode, True, flags),
        (v_keycode, False, flags),
        (command_keycode, False, 0),
    ):
        post_key_event(keycode, key_down, event_flags)
        time.sleep(0.01)
    log.info("貼り付けショートカット送信")


def post_key_event(keycode, key_down, flags=0, pid=None):
    event = CGEventCreateKeyboardEvent(None, keycode, key_down)
    CGEventSetFlags(event, flags)
    if pid:
        CGEventPostToPid(pid, event)
    else:
        CGEventPost(kCGHIDEventTap, event)


def type_text_directly(text, target_app=None):
    pid = target_pid(target_app)
    for char in text:
        event_down = CGEventCreateKeyboardEvent(None, 0, True)
        CGEventKeyboardSetUnicodeString(event_down, len(char), char)
        if pid:
            CGEventPostToPid(pid, event_down)
        else:
            CGEventPost(kCGHIDEventTap, event_down)

        event_up = CGEventCreateKeyboardEvent(None, 0, False)
        CGEventKeyboardSetUnicodeString(event_up, len(char), char)
        if pid:
            CGEventPostToPid(pid, event_up)
        else:
            CGEventPost(kCGHIDEventTap, event_up)
        time.sleep(0.003)
    log.info("直接文字入力イベント送信: pid=%s length=%d", pid, len(text))


def request_accessibility_permission(prompt=False):
    if not HAS_CURSOR_INSERT:
        return False
    try:
        options = {kAXTrustedCheckOptionPrompt: bool(prompt)}
        trusted = bool(AXIsProcessTrustedWithOptions(options))
        log.info("アクセシビリティ権限: %s", "許可済み" if trusted else "未許可")
        return trusted
    except Exception as e:
        log.warning("アクセシビリティ権限要求エラー: %s", e)
        return False

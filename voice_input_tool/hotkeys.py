"""Hotkey conversion helpers shared by UI and engine code."""

DEFAULT_RECORD_HOTKEY = "<ctrl>+<shift>+<space>"

DISPLAY_KEY_MAP = {
    "<left_shift>": "Left Shift",
    "<right_shift>": "Right Shift",
    "<ctrl>": "Ctrl",
    "<shift>": "Shift",
    "<alt>": "Alt",
    "<cmd>": "Cmd",
    "<space>": "Space",
    "<tab>": "Tab",
    "<enter>": "Enter",
    "<backspace>": "Backspace",
    "<esc>": "Esc",
    "<left>": "Left",
    "<right>": "Right",
    "<up>": "Up",
    "<down>": "Down",
}

MAC_KEYCODE_MAP = {
    49: "<space>", 48: "<tab>", 36: "<enter>", 51: "<backspace>",
    53: "<esc>", 123: "<left>", 124: "<right>", 125: "<down>", 126: "<up>",
    0: "a", 1: "s", 2: "d", 3: "f", 4: "h", 5: "g", 6: "z", 7: "x",
    8: "c", 9: "v", 11: "b", 12: "q", 13: "w", 14: "e", 15: "r",
    16: "y", 17: "t", 31: "o", 32: "i", 33: "[", 34: "p", 35: "]",
    37: "l", 38: "j", 39: "'", 40: "k", 41: ";", 45: "n", 46: "m",
    18: "1", 19: "2", 20: "3", 21: "4", 23: "5", 22: "6", 26: "7",
    28: "8", 25: "9", 29: "0", 27: "-", 24: "=", 42: "\\", 43: ",",
    47: ".", 44: "/", 50: "`", 94: "_",
}


def hotkey_to_display(hotkey: str) -> str:
    parts = (part.strip() for part in str(hotkey or "").split("+"))
    display_parts = []
    for part in parts:
        if not part:
            continue
        display = DISPLAY_KEY_MAP.get(part, part.strip("<>"))
        if len(display) == 1 and display.isalpha():
            display = display.upper()
        display_parts.append(display)
    return " + ".join(display_parts)


def display_to_hotkey(display: str) -> str:
    reverse_map = {value.lower(): key for key, value in DISPLAY_KEY_MAP.items()}
    parts = (part.strip() for part in str(display or "").split("+"))
    return "+".join(reverse_map.get(part.lower(), part.lower()) for part in parts if part)

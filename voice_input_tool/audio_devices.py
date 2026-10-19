"""Input device helpers backed by sounddevice."""

import ctypes
from functools import lru_cache
import logging
import sys

import sounddevice as sd

log = logging.getLogger("voice_input")
NAME_PREFIX = "name:"


def stable_input_device_id(device_id):
    """Upgrade an old PortAudio index using the current, unrefreshed device list."""
    if not device_id or str(device_id).startswith(NAME_PREFIX):
        return device_id or ""
    try:
        index = int(device_id)
        if index < 0:
            return ""
        device = sd.query_devices(index)
        if device.get("max_input_channels", 0) > 0 and device.get("name"):
            return NAME_PREFIX + device["name"]
    except (ValueError, TypeError, sd.PortAudioError):
        pass
    return str(device_id)


def refresh_audio_devices():
    """Refresh PortAudio's device list after microphones are connected or removed."""
    terminate = getattr(sd, "_terminate", None)
    initialize = getattr(sd, "_initialize", None)
    if not callable(terminate) or not callable(initialize):
        return False

    try:
        terminate()
        initialize()
        return True
    except Exception as error:
        log.warning("入力マイク一覧を更新できません: %s", error)
        return False


def list_input_devices():
    devices = []
    try:
        for index, device in enumerate(sd.query_devices()):
            channels = int(device.get("max_input_channels", 0))
            if channels <= 0:
                continue
            name = str(device.get("name", f"Input {index}"))
            devices.append(
                {
                    "id": NAME_PREFIX + name,
                    "legacy_id": str(index),
                    "label": f"{name} ({channels}ch)",
                }
            )
    except Exception:
        return []
    return devices


def _first_input_device():
    try:
        devices = sd.query_devices()
    except Exception:
        return None, None

    for index, device in enumerate(devices):
        if int(device.get("max_input_channels", 0)) > 0:
            return index, device
    return None, None


def _default_input_device():
    try:
        default_input = sd.default.device[0]
        if default_input is None or int(default_input) < 0:
            return None, None

        index = int(default_input)
        device = sd.query_devices(index)
        if int(device.get("max_input_channels", 0)) > 0:
            return index, device
    except Exception:
        return None, None

    return None, None


def resolve_input_device(device_id):
    """Convert a saved device id into a sounddevice InputStream value."""
    # PortAudio initializes when sounddevice is imported and can retain the old
    # device list after a USB microphone is connected. The recorder calls this
    # only while it has no open stream, so reinitialization is safe here.
    refresh_audio_devices()
    try:
        if device_id in (None, ""):
            index, device = _default_input_device()
            if device is not None:
                return index

            index, device = _first_input_device()
            if device is not None:
                log.warning(
                    "既定入力マイクが使えないため別の入力を使用します: %s",
                    device.get("name", index),
                )
                return index
            return None

        if str(device_id).startswith(NAME_PREFIX):
            name = str(device_id)[len(NAME_PREFIX):]
            for index, device in enumerate(sd.query_devices()):
                if device.get("name") == name and device.get("max_input_channels", 0) > 0:
                    return index
            raise ValueError(f"指定マイクが未接続です: {name}")

        index = int(device_id)
        if index < 0:
            raise ValueError("入力デバイス番号が負です")

        device = sd.query_devices(index)
        if int(device.get("max_input_channels", 0)) <= 0:
            raise ValueError("入力チャンネルがありません")
        return index
    except Exception as e:
        log.error("入力マイク設定を使用できません: id=%s error=%s", device_id, e)
        index, device = _default_input_device()
        if device is not None:
            log.warning("入力マイク設定の代わりに既定入力を使用します: %s", device.get("name", index))
            return index

        index, device = _first_input_device()
        if device is not None:
            log.warning("入力マイク設定の代わりに別の入力を使用します: %s", device.get("name", index))
            return index
        return None


class _PropertyAddress(ctypes.Structure):
    _fields_ = [("selector", ctypes.c_uint32), ("scope", ctypes.c_uint32),
                ("element", ctypes.c_uint32)]


def _fourcc(value):
    return int.from_bytes(value.encode("ascii"), "big")


@lru_cache(maxsize=1)
def _core_audio():
    if sys.platform != "darwin":
        return None
    library = ctypes.CDLL("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")
    args = [ctypes.c_uint32, ctypes.POINTER(_PropertyAddress), ctypes.c_uint32,
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    library.AudioObjectGetPropertyDataSize.argtypes = args
    library.AudioObjectGetPropertyDataSize.restype = ctypes.c_int32
    library.AudioObjectGetPropertyData.argtypes = args + [ctypes.c_void_p]
    library.AudioObjectGetPropertyData.restype = ctypes.c_int32
    return library


def _hardware_ids(library, selector):
    address = _PropertyAddress(_fourcc(selector), _fourcc("glob"), 0)
    size = ctypes.c_uint32()
    status = library.AudioObjectGetPropertyDataSize(1, ctypes.byref(address), 0, None,
                                                   ctypes.byref(size))
    if status:
        raise OSError(status, "CoreAudio property size")
    values = (ctypes.c_uint32 * (size.value // ctypes.sizeof(ctypes.c_uint32)))()
    status = library.AudioObjectGetPropertyData(1, ctypes.byref(address), 0, None,
                                               ctypes.byref(size), values)
    if status:
        raise OSError(status, "CoreAudio property data")
    return tuple(values[:size.value // ctypes.sizeof(ctypes.c_uint32)])


def audio_device_topology():
    """Read live macOS devices without reinitializing PortAudio's open streams."""
    try:
        library = _core_audio()
        if library is None:
            return None
        return (_hardware_ids(library, "dev#"), _hardware_ids(library, "dIn "))
    except (OSError, AttributeError):
        # The callback watchdog still recovers stalled streams if HAL is unavailable.
        log.debug("CoreAudioの接続状態を取得できません", exc_info=True)
        return None

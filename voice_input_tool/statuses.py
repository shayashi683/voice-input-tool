"""Canonical application states and their user-facing labels."""

from dataclasses import dataclass

IDLE = "idle"
STARTING = "starting"
LISTENING = "listening"
HEARING = "hearing"
PROCESSING = "processing"
CORRECTING = "correcting"
INSERTING = "inserting"

RECORDING_STATUSES = frozenset({STARTING, LISTENING, HEARING})
BUSY_STATUSES = frozenset({PROCESSING, CORRECTING, INSERTING})


@dataclass(frozen=True)
class StatusLabels:
    title: str
    record_title: str
    animate_activity: bool = False


def labels_for(status: str, hotkey_display: str = "") -> StatusLabels:
    hotkey_suffix = f" ({hotkey_display})" if hotkey_display else ""
    labels = {
        IDLE: StatusLabels("🎙", f"録音開始{hotkey_suffix}"),
        STARTING: StatusLabels("準備中", f"マイク起動中…{hotkey_suffix}"),
        LISTENING: StatusLabels("● 聞き取り中", f"録音停止・聞き取り中{hotkey_suffix}"),
        HEARING: StatusLabels(
            "● 聞き取り中",
            f"録音停止・聞き取り中{hotkey_suffix}",
            animate_activity=True,
        ),
        PROCESSING: StatusLabels("認識中", "音声認識中…"),
        CORRECTING: StatusLabels("補正中", "LLM補正中…"),
        INSERTING: StatusLabels("入力中", "カーソル位置へ入力中…"),
    }
    return labels.get(status, labels[IDLE])

"""Manual WAV-file recognition test mode."""

import os
import wave

import numpy as np

from voice_input_tool.app_paths import MODEL_DIR
from voice_input_tool.audio_constants import SAMPLE_RATE
from voice_input_tool.transcription import TranscriptionService


def run_test(recognizer=None, use_llm=False, config=None):
    test_dir = os.path.join(MODEL_DIR, "test_wavs")
    transcript_path = os.path.join(test_dir, "transcript.txt")
    transcripts = _load_transcripts(transcript_path)
    service = TranscriptionService(config or {}, recognizer=recognizer)

    print("=== テストモード ===\n")
    for index in range(1, 6):
        wav_path = os.path.join(test_dir, f"{index}.wav")
        if not os.path.exists(wav_path):
            continue

        samples = _read_wav(wav_path)
        result = service.transcribe(samples)
        text, elapsed = result.text, result.elapsed_seconds

        print(f"--- テスト {index} ({len(samples) / SAMPLE_RATE:.1f}s) ---")
        print(f"正解: {transcripts.get(f'{index}.wav', '')}")
        print(f"認識: {text}")
        print(f"時間: {elapsed:.2f}s")
        if use_llm:
            try:
                print(f"補正: {service.correct(text)}")
            except Exception as error:
                print(f"補正エラー: {error}")
        print()
    print("=== テスト完了 ===")


def _load_transcripts(path):
    transcripts = {}
    if not os.path.exists(path):
        return transcripts
    with open(path, "r", encoding="utf-8") as file_handle:
        for line in file_handle:
            parts = line.strip().split(maxsplit=1)
            if len(parts) == 2:
                transcripts[parts[0]] = parts[1]
    return transcripts


def _read_wav(path):
    with wave.open(path, "rb") as wav_file:
        if wav_file.getframerate() != SAMPLE_RATE or wav_file.getnchannels() != 1:
            raise ValueError(f"16kHz mono WAVのみ対応しています: {path}")
        raw = wav_file.readframes(wav_file.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

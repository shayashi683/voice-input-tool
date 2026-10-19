"""ASR engine setup."""

import logging
import os

import numpy as np

from voice_input_tool.app_paths import MODEL_DIR
from voice_input_tool.audio_constants import SAMPLE_RATE

log = logging.getLogger("voice_input")

ASR_CHUNK_SECONDS = 18.0
ASR_CHUNK_OVERLAP_SECONDS = 1.50
ASR_CHUNK_THRESHOLD_SECONDS = 24.0
ASR_SILENCE_FRAME_SECONDS = 0.02
ASR_SPLIT_SILENCE_SECONDS = 0.90
ASR_MIN_SPEECH_SECONDS = 0.20
ASR_MIN_SPEECH_RMS = 0.006


def create_recognizer(sample_rate=SAMPLE_RATE):
    """Create the ReazonSpeech Zipformer recognizer."""
    import sherpa_onnx

    int8_encoder = os.path.join(MODEL_DIR, "encoder-epoch-99-avg-1.int8.onnx")
    int8_decoder = os.path.join(MODEL_DIR, "decoder-epoch-99-avg-1.int8.onnx")
    int8_joiner = os.path.join(MODEL_DIR, "joiner-epoch-99-avg-1.int8.onnx")
    tokens = os.path.join(MODEL_DIR, "tokens.txt")

    missing = [p for p in [int8_encoder, int8_decoder, int8_joiner, tokens] if not os.path.exists(p)]
    if missing:
        raise FileNotFoundError("ASRモデルが見つかりません: " + ", ".join(missing))

    return sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=int8_encoder,
        decoder=int8_decoder,
        joiner=int8_joiner,
        tokens=tokens,
        num_threads=4,
        sample_rate=sample_rate,
        decoding_method="greedy_search",
        provider="cpu",
    )

def _recognize_once(recognizer, audio_samples, sample_rate):
    if len(audio_samples) < sample_rate * 0.1:
        return ""

    stream = recognizer.create_stream()
    stream.accept_waveform(sample_rate, audio_samples)
    recognizer.decode_stream(stream)
    return stream.result.text.strip()


def _merge_text_segments(segments):
    merged = ""
    for segment in segments:
        if not segment:
            continue
        if not merged:
            merged = segment
            continue

        max_overlap = min(12, len(merged), len(segment))
        overlap = 0
        for size in range(max_overlap, 0, -1):
            if merged.endswith(segment[:size]):
                overlap = size
                break
        merged += segment[overlap:]
    return merged


def _speech_segments(audio_samples, sample_rate):
    """Return padded speech regions separated by a sustained quiet pause."""
    frame_size = max(1, int(sample_rate * ASR_SILENCE_FRAME_SECONDS))
    frame_count = (len(audio_samples) + frame_size - 1) // frame_size
    if frame_count == 0:
        return [], 0.0

    rms_values = np.empty(frame_count, dtype=np.float32)
    for index in range(frame_count):
        frame = audio_samples[index * frame_size : (index + 1) * frame_size]
        rms_values[index] = float(np.sqrt(np.mean(frame * frame))) if len(frame) else 0.0

    quiet_rms = float(np.percentile(rms_values, 15))
    active_rms = float(np.percentile(rms_values, 85))
    threshold = max(
        ASR_MIN_SPEECH_RMS,
        quiet_rms + (active_rms - quiet_rms) * 0.25,
    )
    speech_frames = np.flatnonzero(rms_values >= threshold)
    if len(speech_frames) == 0:
        return [], threshold

    split_gap_frames = max(1, int(ASR_SPLIT_SILENCE_SECONDS / ASR_SILENCE_FRAME_SECONDS))
    groups = []
    group_start = int(speech_frames[0])
    previous = group_start
    for frame_index_value in speech_frames[1:]:
        frame_index = int(frame_index_value)
        if frame_index - previous > split_gap_frames:
            groups.append((group_start, previous))
            group_start = frame_index
        previous = frame_index
    groups.append((group_start, previous))

    minimum_frames = max(1, int(ASR_MIN_SPEECH_SECONDS / ASR_SILENCE_FRAME_SECONDS))
    groups = [
        (first_frame, last_frame)
        for first_frame, last_frame in groups
        if last_frame - first_frame + 1 >= minimum_frames
    ]
    if len(groups) <= 1:
        return [audio_samples], threshold

    boundaries = []
    for (_, left_last), (right_first, _) in zip(groups, groups[1:]):
        quiet_start = (left_last + 1) * frame_size
        quiet_end = right_first * frame_size
        boundaries.append((quiet_start + quiet_end) // 2)

    segments = []
    start = 0
    for boundary in boundaries:
        segments.append(audio_samples[start:boundary])
        start = boundary
    segments.append(audio_samples[start:])
    return segments, threshold


def _recognize_long_segment(recognizer, audio_samples, sample_rate):
    duration = len(audio_samples) / sample_rate
    if duration <= ASR_CHUNK_THRESHOLD_SECONDS:
        return _recognize_once(recognizer, audio_samples, sample_rate)

    chunk_size = int(sample_rate * ASR_CHUNK_SECONDS)
    overlap_size = int(sample_rate * ASR_CHUNK_OVERLAP_SECONDS)
    step = max(1, chunk_size - overlap_size)
    texts = []

    for start in range(0, len(audio_samples), step):
        end = min(len(audio_samples), start + chunk_size)
        if end - start < sample_rate * 0.1:
            break
        texts.append(_recognize_once(recognizer, audio_samples[start:end], sample_rate))
        if end >= len(audio_samples):
            break

    log.info(
        "ASR長尺分割: duration=%.1fs chunks=%d overlap=%.2fs segment_text_lengths=%s",
        duration,
        len(texts),
        ASR_CHUNK_OVERLAP_SECONDS,
        [len(text) for text in texts],
    )
    return _merge_text_segments(texts)


def recognize_speech(recognizer, audio_samples, sample_rate=SAMPLE_RATE):
    """Convert float32 audio samples to text."""
    audio_samples = np.asarray(audio_samples, dtype=np.float32)
    segments, threshold = _speech_segments(audio_samples, sample_rate)
    if len(segments) <= 1:
        return _recognize_long_segment(recognizer, audio_samples, sample_rate)

    texts = [
        _recognize_long_segment(recognizer, segment, sample_rate)
        for segment in segments
    ]
    log.info(
        "ASR無音分割: duration=%.1fs segments=%d threshold=%.4f segment_durations=%s segment_text_lengths=%s",
        len(audio_samples) / sample_rate,
        len(segments),
        threshold,
        [round(len(segment) / sample_rate, 1) for segment in segments],
        [len(text) for text in texts],
    )
    return "".join(text for text in texts if text)

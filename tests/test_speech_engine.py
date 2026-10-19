import unittest

import numpy as np

from voice_input_tool import speech_engine


class FakeResult:
    def __init__(self):
        self.text = ""


class FakeStream:
    def __init__(self):
        self.samples = None
        self.result = FakeResult()

    def accept_waveform(self, _sample_rate, samples):
        self.samples = samples


class FakeRecognizer:
    def __init__(self, texts):
        self.texts = iter(texts)
        self.streams = []

    def create_stream(self):
        stream = FakeStream()
        self.streams.append(stream)
        return stream

    def decode_stream(self, stream):
        stream.result.text = next(self.texts)


class SpeechEngineTests(unittest.TestCase):
    def test_merge_removes_chunk_overlap(self):
        merged = speech_engine._merge_text_segments(["これはテストです", "テストです続きです"])
        self.assertEqual(merged, "これはテストです続きです")

    def test_long_audio_is_chunked_and_merged(self):
        recognizer = FakeRecognizer(["前半テスト", "テスト後半"])
        sample_rate = 10
        samples = np.zeros(300, dtype=np.float32)
        with unittest.mock.patch.multiple(
            speech_engine,
            ASR_CHUNK_SECONDS=18.0,
            ASR_CHUNK_OVERLAP_SECONDS=0.35,
            ASR_CHUNK_THRESHOLD_SECONDS=24.0,
        ):
            text = speech_engine.recognize_speech(recognizer, samples, sample_rate=sample_rate)
        self.assertEqual(text, "前半テスト後半")

    def test_conversation_is_recognized_per_speech_region(self):
        recognizer = FakeRecognizer(["最初の発話", "次の発話"])
        sample_rate = 100
        samples = np.concatenate(
            [
                np.zeros(20, dtype=np.float32),
                np.full(100, 0.05, dtype=np.float32),
                np.zeros(100, dtype=np.float32),
                np.full(100, 0.05, dtype=np.float32),
                np.zeros(20, dtype=np.float32),
            ]
        )

        text = speech_engine.recognize_speech(
            recognizer,
            samples,
            sample_rate=sample_rate,
        )

        self.assertEqual(text, "最初の発話次の発話")
        self.assertEqual(len(recognizer.streams), 2)
        self.assertTrue(all(len(stream.samples) < len(samples) for stream in recognizer.streams))


if __name__ == "__main__":
    unittest.main()

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from voice_input_tool.recording_store import RecordingStore, atomic_write


class RecordingStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = RecordingStore(self.temp.name)

    def test_journal_preserves_all_samples_and_private_permissions(self):
        recording_id = self.store.create(target_pid=42)
        writer = self.store.open_audio(recording_id)
        for index in range(100):
            writer.append(np.full(512, index, dtype=np.float32))
        writer.finish()
        expected = np.repeat(np.arange(100, dtype=np.float32), 512)
        np.testing.assert_array_equal(self.store.load_audio(recording_id), expected)
        path = self.store.directory(recording_id) / 'audio.f32'
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)

    def test_failed_atomic_replace_retains_previous_checkpoint(self):
        path = Path(self.temp.name) / 'state.json'
        atomic_write(path, b'old')
        with patch('voice_input_tool.recording_store.os.replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                atomic_write(path, b'new')
        self.assertEqual(path.read_bytes(), b'old')
        self.assertEqual(list(Path(self.temp.name).glob('.checkpoint-*')), [])

    def test_interrupted_audio_recovers_without_replaying_pastes(self):
        audio_id = self.store.create()
        self.store.save_audio(audio_id, (np.ones(50, dtype=np.float32),))
        text_id = self.store.create()
        self.store.update(text_id, state='delivering', raw_text='原文', text='原文。')
        copied_id = self.store.create()
        self.store.update(copied_id, state='copied', text='コピー済み')
        fresh_store = RecordingStore(self.temp.name)
        self.assertEqual(fresh_store.recover(), [audio_id])
        self.assertEqual(fresh_store.read(text_id)['state'], 'recovered')
        self.assertEqual(fresh_store.read(copied_id)['state'], 'copied')
        self.assertEqual((fresh_store.directory(text_id) / 'transcript.txt').read_text(), '原文。')

    def test_truncated_final_sample_does_not_discard_recording(self):
        recording_id = self.store.create()
        self.store.save_audio(recording_id, (np.array([1, 2], dtype=np.float32),))
        with open(self.store.directory(recording_id) / 'audio.f32', 'ab') as handle:
            handle.write(b'\x00\x00')
        np.testing.assert_array_equal(self.store.load_audio(recording_id), [1, 2])

    def test_corrupt_entry_does_not_hide_good_entries(self):
        good = self.store.create()
        bad = self.store.create()
        (self.store.directory(bad) / 'recording.json').write_text('{')
        self.assertEqual([r['id'] for r in self.store.list()], [good])

    def test_disk_failure_in_final_flush_does_not_deadlock_finish(self):
        recording_id = self.store.create()
        writer = self.store.open_audio(recording_id)
        with patch('voice_input_tool.recording_store.os.fsync', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                writer.finish()
        self.assertFalse(writer._thread.is_alive())

    def test_path_traversal_is_rejected(self):
        with self.assertRaises(ValueError):
            self.store.read('../anything')

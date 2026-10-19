import os
import tempfile
import unittest

from voice_input_tool.headless import acquire_engine_lock, release_engine_lock


class HeadlessEngineLockTests(unittest.TestCase):
    def test_lock_can_be_reacquired_after_release(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "engine.lock")

            first = acquire_engine_lock(path)
            self.assertIsNotNone(first)
            first.seek(0)
            self.assertEqual(first.read(), str(os.getpid()))
            release_engine_lock(first)

            second = acquire_engine_lock(path)
            self.assertIsNotNone(second)
            release_engine_lock(second)


if __name__ == "__main__":
    unittest.main()

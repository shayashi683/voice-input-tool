import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest


class InstallScriptTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        scripts = self.root / "scripts"
        scripts.mkdir()
        self.script = scripts / "install_mac_app.sh"
        source = Path(__file__).resolve().parents[1] / "scripts" / self.script.name
        shutil.copyfile(source, self.script)
        self.env_file = self.root / ".env"
        self.environment = os.environ.copy()
        self.environment.pop("OPENAI_API_KEY", None)

    def install(self):
        return subprocess.run(
            ["bash", str(self.script), "--skip-deps", "--skip-model", "--skip-app"],
            env=self.environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
        )

    def test_saves_one_key_preserving_other_settings_with_private_permissions(self):
        self.env_file.write_text(
            "# personal settings\nOPENAI_API_KEY = previous\n"
            "export OPENAI_API_KEY=duplicate\nOTHER=value\n"
        )
        self.environment["OPENAI_API_KEY"] = "dummy-new-key"
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.env_file.read_text(),
            "# personal settings\nOTHER=value\nOPENAI_API_KEY=dummy-new-key\n",
        )
        self.assertEqual(stat.S_IMODE(self.env_file.stat().st_mode), 0o600)
        self.assertNotIn("dummy-new-key", result.stdout + result.stderr)
        self.assertFalse(list(self.root.glob(".env-*")))

    def test_key_with_control_characters_is_rejected_without_changing_file(self):
        original = "OPENAI_API_KEY=existing\nOTHER=value\n"
        self.env_file.write_text(original)
        for value in ("dummy-key\nOTHER=injected", "dummy-key\r", "dummy-key\t"):
            with self.subTest(value=value):
                self.environment["OPENAI_API_KEY"] = value
                result = self.install()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.env_file.read_text(), original)
                self.assertNotIn("dummy-key", result.stdout + result.stderr)

    def test_without_key_preserves_existing_file(self):
        original = "# keep settings\nOPENAI_API_KEY=existing\nOTHER=value\n"
        self.env_file.write_text(original)
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.env_file.read_text(), original)


if __name__ == "__main__":
    unittest.main()

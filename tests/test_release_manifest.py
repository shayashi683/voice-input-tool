import hashlib
from pathlib import Path
import plistlib
import tempfile
import unittest

from scripts.write_update_manifest import make_manifest


class ReleaseManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'packaging').mkdir()
        (self.root / 'landing/downloads').mkdir(parents=True)
        self.info = {'CFBundleShortVersionString': '1.0.14', 'LSMinimumSystemVersion': '13.0'}
        self.write_info()
        (self.root / 'packaging/release-notes.txt').write_text('更新を確認できます。')
        self.package = self.root / 'landing/downloads/VoiceInputTool-1.0.14-mac-arm64.pkg'
        self.package.write_bytes(b'xar!fixture')

    def write_info(self):
        with (self.root / 'packaging/VoiceInputTool-Info.plist').open('wb') as handle:
            plistlib.dump(self.info, handle)

    def test_feed_matches_package_and_minimum_os(self):
        value = make_manifest(self.root)
        self.assertEqual(value['version'], '1.0.14')
        self.assertEqual(value['minimum_macos'], '13.0.0')
        self.assertEqual(value['sha256'], hashlib.sha256(self.package.read_bytes()).hexdigest())
        self.assertEqual(value['size'], self.package.stat().st_size)
        self.assertEqual(value['url'], 'https://voice-input-tool.pages.dev/downloads/VoiceInputTool-1.0.14-mac-arm64.pkg')

    def test_non_pkg_rejected(self):
        self.package.write_text('<html>not found</html>')
        with self.assertRaises(ValueError):
            make_manifest(self.root)

    def test_path_traversal_or_invalid_version_rejected(self):
        for version in ['../1.0.14', '1.0', '1.0.14\n', 14]:
            self.info['CFBundleShortVersionString'] = version
            self.write_info()
            with self.assertRaises(ValueError):
                make_manifest(self.root)

    def test_oversized_package_rejected(self):
        with self.package.open('wb') as handle:
            handle.truncate(25 * 1024 * 1024 + 1)
        with self.assertRaises(ValueError):
            make_manifest(self.root)

    def test_empty_notes_rejected(self):
        (self.root / 'packaging/release-notes.txt').write_text('')
        with self.assertRaises(ValueError):
            make_manifest(self.root)

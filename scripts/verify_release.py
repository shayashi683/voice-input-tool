"""Fail closed before uploading a release with missing or mismatched assets."""
import hashlib
import json
from html.parser import HTMLParser
from pathlib import Path
import plistlib
import re
import subprocess
import tempfile
from write_update_manifest import make_manifest

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / 'landing'

def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def verify():
    with (ROOT / 'packaging/VoiceInputTool-Info.plist').open('rb') as handle:
        version = plistlib.load(handle)['CFBundleShortVersionString']
    name = f'VoiceInputTool-{version}-mac-arm64.pkg'
    package = SITE / 'downloads' / name
    assert package.read_bytes()[:4] == b'xar!'
    checksum, filename = package.with_suffix('.pkg.sha256').read_text().split()
    assert filename == name and sha256(package) == checksum
    assert json.loads((SITE / 'update.json').read_text()) == make_manifest(ROOT), 'Updater feed does not match package'
    html = (SITE / 'index.html').read_text()
    assert f'downloads/{name}' in html and f'v{version}' in html

    class Links(HTMLParser):
        def handle_starttag(self, tag, attrs):
            for key, value in attrs:
                if key in ('src', 'href') and value and not value.startswith(('#', 'https:', 'http:')):
                    target = SITE / value.split('?', 1)[0].lstrip('/')
                    assert target.is_file(), f'Missing page asset: {value}'
    Links().feed(html)
    for asset in re.findall(r'url\([\"\']?([^\)\"\']+)', (SITE / 'styles.css').read_text()):
        assert (SITE / asset).is_file(), asset
    for path in SITE.rglob('*'):
        if path.is_file():
            assert path.stat().st_size <= 25 * 1024 * 1024, path
            assert path.name not in ('.env', 'config.json', 'setup-guide.json')

    runtime = SITE / 'downloads/runtime'
    rows = (runtime / 'voiceinput-runtime-1.0.2-mac-arm64.tar.gz.manifest').read_text().splitlines()
    archive = hashlib.sha256()
    total = 0
    for row in rows[1:]:
        kind, checksum, part, size = row.split()
        assert kind == 'part' and Path(part).name == part
        data = (runtime / part).read_bytes()
        assert len(data) == int(size) and hashlib.sha256(data).hexdigest() == checksum
        total += len(data)
        archive.update(data)
    kind, checksum, _, size = rows[0].split()
    assert kind == 'archive' and archive.hexdigest() == checksum and total == int(size)
    assert (SITE / 'downloads/VoiceInputTool-1.0.12-mac-arm64.pkg').read_bytes()[:4] == b'xar!'
    assert (SITE / 'downloads/VoiceInputTool-1.0.13-mac-arm64.pkg').read_bytes()[:4] == b'xar!'

    with tempfile.TemporaryDirectory(prefix='voice-input-release-verify-') as scratch:
        unpacked = Path(scratch) / 'package'
        subprocess.run(['pkgutil', '--expand-full', str(package), str(unpacked)], check=True)
        payload = unpacked / 'Payload/Library/Application Support/VoiceInputToolInstaller/payload'
        for folder in ('voice_input_tool', 'native', 'tests', 'scripts', 'assets', 'packaging'):
            for source in (ROOT / folder).rglob('*'):
                if source.is_file() and '__pycache__' not in source.parts and source.suffix != '.pyc':
                    assert (payload / source.relative_to(ROOT)).read_bytes() == source.read_bytes(), source
        for file in payload.rglob('*'):
            assert file.name not in ('.env', 'config.json', 'recordings', 'logs', 'setup-guide.json'), file
            if file.is_file() and file.suffix not in ('.icns', '.png'):
                assert not re.search(rb'sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{20,}', file.read_bytes()), file
        postinstall = (unpacked / 'Scripts/postinstall').read_text()
        assert '@RUNTIME_' not in postinstall
        assert 'voiceinput-runtime-1.0.2-mac-arm64.tar.gz.manifest' in postinstall
        app = unpacked / 'Payload/Applications/VoiceInputTool.app'
        with (app / 'Contents/Info.plist').open('rb') as handle:
            assert plistlib.load(handle)['CFBundleShortVersionString'] == version
        assert (app / 'Contents/Resources/workdir.txt').read_text().strip() == '~/Library/Application Support/VoiceInputTool'
        subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
    print(f'Release {version}: links, checksums, runtime, packaged sources and app signature verified')

if __name__ == '__main__':
    verify()

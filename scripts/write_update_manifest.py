"""Generate the public updater feed from the exact package being published."""
import hashlib
import json
from pathlib import Path
import plistlib
import re

ROOT = Path(__file__).resolve().parents[1]

def make_manifest(root):
    root = Path(root)
    with (root / 'packaging/VoiceInputTool-Info.plist').open('rb') as handle:
        info = plistlib.load(handle)
    version = info['CFBundleShortVersionString']
    if not isinstance(version, str) or not re.fullmatch(r'[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}', version):
        raise ValueError('Release version must be major.minor.patch')
    minimum = info['LSMinimumSystemVersion']
    if not isinstance(minimum, str) or not re.fullmatch(r'[0-9]{1,6}\.[0-9]{1,6}(?:\.[0-9]{1,6})?', minimum):
        raise ValueError('Invalid minimum macOS version')
    if minimum.count('.') == 1:
        minimum += '.0'
    name = f'VoiceInputTool-{version}-mac-arm64.pkg'
    path = root / 'landing/downloads' / name
    if not 4 <= path.stat().st_size <= 25 * 1024 * 1024:
        raise ValueError('Package exceeds updater size limit')
    package = path.read_bytes()
    if package[:4] != b'xar!':
        raise ValueError('Expected a macOS PKG, not an HTML error page')
    notes = (root / 'packaging/release-notes.txt').read_text().strip()
    if not notes or len(notes) > 4000:
        raise ValueError('Missing or oversized release notes')
    return {
        'schema_version': 1,
        'version': version,
        'minimum_macos': minimum,
        'architecture': 'arm64',
        'url': f'https://voice-input-tool.pages.dev/downloads/{name}',
        'size': len(package),
        'sha256': hashlib.sha256(package).hexdigest(),
        'notes': notes,
    }

if __name__ == '__main__':
    manifest = make_manifest(ROOT)
    (ROOT / 'landing/update.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print('Generated updater feed for', manifest['version'])

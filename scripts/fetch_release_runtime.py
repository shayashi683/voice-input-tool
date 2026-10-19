"""Recover the existing public runtime, verifying every chunk before deployment."""
import concurrent.futures
import hashlib
from pathlib import Path
import subprocess

BASE = 'https://voice-input-tool.pages.dev/downloads/runtime/'
ARCHIVE = 'voiceinput-runtime-1.0.2-mac-arm64.tar.gz'
EXPECTED_ARCHIVE_SHA = '62d039a753fdd8a74058c11ac73283303cd3362168d5e583b8f19cf723149a83'
ROOT = Path(__file__).resolve().parents[1]

def fetch(url):
    return subprocess.check_output(['curl', '-fsSL', '--retry', '3', '--max-time', '120', url])

def main():
    destination = ROOT / 'landing/downloads/runtime'
    destination.mkdir(parents=True, exist_ok=True)
    manifest = fetch(BASE + ARCHIVE + '.manifest')
    rows = [line.split() for line in manifest.decode().splitlines() if line.strip()]
    assert rows[0][:3] == ['archive', EXPECTED_ARCHIVE_SHA, ARCHIVE]
    parts = rows[1:]
    assert len(parts) == 10
    for i, (kind, sha, name, size) in enumerate(parts):
        assert kind == 'part' and name == f'{ARCHIVE}.part-{i:03d}'
        assert len(sha) == 64 and 0 < int(size) <= 20 * 1024 * 1024

    def download(row):
        _, sha, name, size = row
        target = destination / name
        if target.exists() and target.stat().st_size == int(size):
            if hashlib.sha256(target.read_bytes()).hexdigest() == sha:
                return
        data = fetch(BASE + name)
        assert len(data) == int(size) and hashlib.sha256(data).hexdigest() == sha, name
        target.write_bytes(data)
        print('Verified', name, flush=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(download, parts))
    archive_hash = hashlib.sha256()
    for _, _, name, _ in parts:
        archive_hash.update((destination / name).read_bytes())
    assert archive_hash.hexdigest() == EXPECTED_ARCHIVE_SHA
    (destination / (ARCHIVE + '.manifest')).write_bytes(manifest)
    print('Runtime archive SHA-256 verified')

if __name__ == '__main__':
    main()

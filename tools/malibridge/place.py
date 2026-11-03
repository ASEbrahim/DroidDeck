#!/usr/bin/env python3
"""Check the pinned MaliBridge release asset and put its files where the APK build reads them."""
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[2]
ENV = 'tools/malibridge/release.env'
MANIFEST = 'MALIBRIDGE.json'
LINUXFS = 'app/src/main/assets/linuxfs/'
JNI = 'app/src/main/jniLibs/arm64-v8a/'
LICENSES = 'usr/local/share/licenses/malibridge/'
REQUIRED = {
    'usr/local/lib/vkbridge/libvulkan_droidbridge.so': LINUXFS + 'usr/local/lib/vkbridge/libvulkan_droidbridge.so',
    'usr/local/share/vkbridge/droidbridge_icd.json': LINUXFS + 'usr/local/share/vkbridge/droidbridge_icd.json',
    'usr/local/bin/droiddeck-vkbridge-smoke': LINUXFS + 'usr/local/bin/droiddeck-vkbridge-smoke',
    'android/arm64-v8a/libvkbridge_server.so': JNI + 'libvkbridge_server.so',
    'android/arm64-v8a/libvkbridge_jni.so': JNI + 'libvkbridge_jni.so',
}
OPTIONAL = {LICENSES + name: LINUXFS + LICENSES + name for name in ('LICENSE', 'NOTICE', 'THIRD-PARTY')}
KEYS = ('MALIBRIDGE_REPO', 'MALIBRIDGE_TAG', 'MALIBRIDGE_SHA256', 'MALIBRIDGE_SOURCE_COMMIT',
        'MALIBRIDGE_SOURCE_SHA256', 'MALIBRIDGE_INTERFACE')
MAX_FILE = 64 * 1024 * 1024


def read_env(root=ROOT):
    values = {}
    for line in (root / ENV).read_text().splitlines():
        if line and not line.startswith('#'):
            key, _, value = line.partition('=')
            values[key] = value
    missing = [key for key in KEYS if not values.get(key) or values[key] == 'TBD']
    if missing:
        raise ValueError(ENV + ': not set: ' + ', '.join(missing))
    return values


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_archive(archive):
    data = Path(archive).read_bytes()
    process = subprocess.run(['zstd', '-dc'], input=data, capture_output=True)
    if process.returncode:
        raise ValueError('The MaliBridge asset is not a valid zstd file')
    files = {}
    with tarfile.open(fileobj=io.BytesIO(process.stdout), mode='r:') as tar:
        for entry in tar:
            name = entry.name.removeprefix('./')
            parts = PurePosixPath(name).parts
            if entry.isdir():
                continue
            allowed = name == MANIFEST or name in REQUIRED or name in OPTIONAL
            if (not allowed or not entry.isfile() or name in files or '..' in parts
                    or PurePosixPath(name).as_posix() != name or entry.size > MAX_FILE):
                raise ValueError('Unexpected member in the MaliBridge asset: ' + entry.name)
            files[name] = (tar.extractfile(entry).read(), entry.mode & 0o777)
    return data, files


def check(archive, root=ROOT):
    pins = read_env(root)
    data, files = read_archive(archive)
    if digest(data) != pins['MALIBRIDGE_SHA256']:
        raise ValueError('The MaliBridge asset does not match MALIBRIDGE_SHA256 in ' + ENV)
    if MANIFEST not in files:
        raise ValueError('The MaliBridge asset has no ' + MANIFEST)
    manifest = json.loads(files.pop(MANIFEST)[0])
    if manifest.get('interface') != int(pins['MALIBRIDGE_INTERFACE']):
        raise ValueError('MaliBridge interface %r is not the %s this DroidDeck expects'
                         % (manifest.get('interface'), pins['MALIBRIDGE_INTERFACE']))
    if manifest.get('tag') != pins['MALIBRIDGE_TAG'] or manifest.get('commit') != pins['MALIBRIDGE_SOURCE_COMMIT']:
        raise ValueError('The MaliBridge asset was built from another tag or commit than ' + ENV + ' names')
    missing = [name for name in REQUIRED if name not in files]
    if missing:
        raise ValueError('The MaliBridge asset lacks: ' + ', '.join(missing))
    if set(manifest.get('files', {})) != set(files):
        raise ValueError(MANIFEST + ' does not list exactly the files in the asset')
    for name, (content, _) in files.items():
        if digest(content) != manifest['files'][name]:
            raise ValueError(MANIFEST + ' checksum mismatch for ' + name)
    return files


def place(archive, root=ROOT):
    files = check(archive, root)
    for name, (content, mode) in files.items():
        path = root / {**REQUIRED, **OPTIONAL}[name]
        if path.is_symlink():
            raise ValueError('Refusing to write through a symlink: ' + str(path))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        path.chmod(mode or 0o644)
    print('MaliBridge %s: %d files in place' % (read_env(root)['MALIBRIDGE_TAG'], len(files)))


if __name__ == '__main__':
    try:
        place(sys.argv[1])
    except (ValueError, OSError, IndexError, tarfile.TarError) as error:
        sys.exit('malibridge: ' + str(error))

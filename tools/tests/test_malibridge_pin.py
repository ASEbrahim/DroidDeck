import hashlib
import importlib.util
import io
import contextlib
import json
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('malibridge_place', ROOT / 'tools/malibridge/place.py')
place = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(place)

COMMIT = 'a' * 40


def sha(data):
    return hashlib.sha256(data).hexdigest()


class PinFileTest(unittest.TestCase):
    def test_release_env_is_complete_and_well_formed(self):
        values = place.read_env(ROOT)
        self.assertRegex(values['MALIBRIDGE_REPO'], r'^[\w.-]+/[\w.-]+$')
        self.assertRegex(values['MALIBRIDGE_TAG'], r'^v\d+(\.\d+)+$')
        self.assertRegex(values['MALIBRIDGE_SHA256'], r'^[0-9a-f]{64}$')
        self.assertRegex(values['MALIBRIDGE_SOURCE_COMMIT'], r'^[0-9a-f]{40}$')
        self.assertRegex(values['MALIBRIDGE_SOURCE_SHA256'], r'^[0-9a-f]{64}$')
        self.assertRegex(values['MALIBRIDGE_INTERFACE'], r'^\d+$')

    def test_recipe_is_keyed_on_the_pin_and_lists_every_output(self):
        spec = json.loads((ROOT / 'tools/components/recipes.json').read_text())['vkbridge']
        self.assertIn('tools/malibridge/release.env', spec['inputs'])
        self.assertIn('tools/malibridge/place.py', spec['inputs'])
        self.assertNotIn('packages', spec)
        recipe = (ROOT / spec['script']).read_text()
        self.assertIn('tools/malibridge/release.env', recipe)
        self.assertIn('tools/malibridge/place.py', recipe)
        for name in place.REQUIRED.values():
            self.assertIn(name, spec['outputs'])

    def test_jni_libraries_are_carried_through_the_build_plumbing(self):
        prepare = (ROOT / 'tools/components/prepare.py').read_text()
        inputs = (ROOT / 'tools/release/runtime_inputs.py').read_text()
        ignore = (ROOT / '.gitignore').read_text().splitlines()
        for library in ('libvkbridge_server.so', 'libvkbridge_jni.so'):
            self.assertIn(library, prepare)
            self.assertIn('lib/arm64-v8a/' + library, inputs)
            self.assertIn('app/src/main/jniLibs/arm64-v8a/' + library, ignore)
        self.assertIn("'tools/malibridge'", inputs)

    def test_no_submodule_is_left(self):
        old = 'external/' + 'MaliBridge'
        self.assertNotIn(old, (ROOT / '.gitmodules').read_text())
        for name in ('app/build.gradle', 'tools/build_local.sh', '.github/workflows/build.yml',
                     'tools/components/recipes.json', 'tools/components/recipes/vkbridge.sh'):
            self.assertNotIn(old, (ROOT / name).read_text(), name)
        listed = subprocess.run(['git', 'ls-files', '--', old], cwd=ROOT, capture_output=True, text=True)
        if listed.returncode == 0:
            self.assertEqual('', listed.stdout.strip())

    def test_asset_paths_match_the_files_the_build_checks(self):
        workflow = (ROOT / '.github/workflows/build.yml').read_text()
        for name in place.REQUIRED:
            if name.startswith('usr/'):
                self.assertIn(name.removeprefix('usr/local/'), workflow)


class PlaceTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / 'repo'
        (self.root / 'tools/malibridge').mkdir(parents=True)
        self.work = Path(directory.name)
        self.contents = {name: ('payload ' + name).encode() for name in place.REQUIRED}
        self.contents['usr/local/share/licenses/malibridge/NOTICE'] = b'notice'

    def write_env(self, archive, interface='1'):
        (self.root / place.ENV).write_text('\n'.join([
            'MALIBRIDGE_REPO=owner/repo', 'MALIBRIDGE_TAG=v1.2.3', 'MALIBRIDGE_SHA256=' + sha(archive),
            'MALIBRIDGE_SOURCE_COMMIT=' + COMMIT, 'MALIBRIDGE_SOURCE_SHA256=' + 'b' * 64,
            'MALIBRIDGE_INTERFACE=' + interface, '']))

    def archive(self, contents=None, manifest=None, extra=()):
        contents = dict(self.contents if contents is None else contents)
        manifest = dict(manifest or {})
        manifest.setdefault('interface', 1)
        manifest.setdefault('tag', 'v1.2.3')
        manifest.setdefault('commit', COMMIT)
        manifest.setdefault('files', {name: sha(data) for name, data in contents.items()})
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w') as tar:
            members = dict(contents)
            members[place.MANIFEST] = json.dumps(manifest).encode()
            for name, data in members.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                info.mode = 0o755 if name.endswith('smoke') else 0o644
                tar.addfile(info, io.BytesIO(data))
            for info in extra:
                tar.addfile(info, io.BytesIO(b'x' * info.size))
        data = subprocess.run(['zstd', '-c', '-19'], input=buffer.getvalue(), capture_output=True, check=True).stdout
        path = self.work / 'asset.tar.zst'
        path.write_bytes(data)
        return path

    def test_good_asset_is_placed(self):
        path = self.archive()
        self.write_env(path.read_bytes())
        with contextlib.redirect_stdout(io.StringIO()):
            place.place(path, self.root)
        server = self.root / 'app/src/main/jniLibs/arm64-v8a/libvkbridge_server.so'
        self.assertEqual(self.contents['android/arm64-v8a/libvkbridge_server.so'], server.read_bytes())
        icd = self.root / 'app/src/main/assets/linuxfs/usr/local/lib/vkbridge/libvulkan_droidbridge.so'
        self.assertTrue(icd.is_file())
        smoke = self.root / 'app/src/main/assets/linuxfs/usr/local/bin/droiddeck-vkbridge-smoke'
        self.assertTrue(smoke.stat().st_mode & 0o100)
        self.assertTrue((self.root / 'app/src/main/assets/linuxfs/usr/local/share/licenses/malibridge/NOTICE').is_file())

    def test_wrong_checksum_fails_and_places_nothing(self):
        path = self.archive()
        self.write_env(b'other bytes')
        with self.assertRaisesRegex(ValueError, 'MALIBRIDGE_SHA256'):
            place.place(path, self.root)
        self.assertFalse((self.root / 'app').exists())

    def test_unfilled_pin_fails(self):
        path = self.archive()
        self.write_env(path.read_bytes())
        env = self.root / place.ENV
        env.write_text(env.read_text().replace('MALIBRIDGE_SOURCE_COMMIT=' + COMMIT, 'MALIBRIDGE_SOURCE_COMMIT=TBD'))
        with self.assertRaisesRegex(ValueError, 'not set'):
            place.place(path, self.root)

    def test_other_interface_is_refused(self):
        path = self.archive(manifest={'interface': 2})
        self.write_env(path.read_bytes())
        with self.assertRaisesRegex(ValueError, 'interface'):
            place.place(path, self.root)
        self.assertFalse((self.root / 'app').exists())

    def test_other_tag_or_commit_is_refused(self):
        for manifest in ({'tag': 'v9.9.9'}, {'commit': 'c' * 40}):
            path = self.archive(manifest=manifest)
            self.write_env(path.read_bytes())
            with self.assertRaisesRegex(ValueError, 'tag or commit'):
                place.place(path, self.root)

    def test_manifest_checksum_mismatch_is_refused(self):
        files = {name: sha(data) for name, data in self.contents.items()}
        files['android/arm64-v8a/libvkbridge_jni.so'] = '0' * 64
        path = self.archive(manifest={'files': files})
        self.write_env(path.read_bytes())
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            place.place(path, self.root)

    def test_missing_required_file_is_refused(self):
        contents = dict(self.contents)
        del contents['android/arm64-v8a/libvkbridge_jni.so']
        path = self.archive(contents=contents)
        self.write_env(path.read_bytes())
        with self.assertRaisesRegex(ValueError, 'lacks'):
            place.place(path, self.root)

    def test_unexpected_members_are_refused(self):
        for name in ('../escape.so', '/abs.so', 'usr/local/bin/other'):
            contents = dict(self.contents) | {name: b'x'}
            path = self.archive(contents=contents)
            self.write_env(path.read_bytes())
            with self.assertRaisesRegex(ValueError, 'Unexpected member'):
                place.place(path, self.root)

    def test_links_are_refused(self):
        link = tarfile.TarInfo('usr/local/bin/droiddeck-vkbridge-smoke')
        link.type = tarfile.SYMTYPE
        link.linkname = '/etc/passwd'
        contents = dict(self.contents)
        del contents[link.name]
        path = self.archive(contents=contents, extra=[link])
        self.write_env(path.read_bytes())
        with self.assertRaisesRegex(ValueError, 'Unexpected member'):
            place.place(path, self.root)

    def test_not_a_zstd_file_is_refused(self):
        path = self.work / 'bad.tar.zst'
        path.write_bytes(b'not an archive')
        self.write_env(path.read_bytes())
        with self.assertRaisesRegex(ValueError, 'zstd'):
            place.place(path, self.root)


if __name__ == '__main__':
    unittest.main()

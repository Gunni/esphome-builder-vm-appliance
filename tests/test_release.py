import base64
import hashlib
import json
from pathlib import Path
import runpy
import shutil
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
R = runpy.run_path(str(ROOT / 'build-release.py'))


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_package_is_generic_and_excludes_local_credentials(self):
        source = self.root / 'source'
        for name in R['SOURCE_FILES']:
            destination = source / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, destination)
        (source / '.env').write_text('USERNAME=private-user\nSSH_KEY=private-key\nSERIAL_CONSOLE=true\n')
        (source / 'secret.key').write_text('secret must never be packaged')
        (source / 'unexpected.iso').write_bytes(b'not a release asset')
        with patch.dict(R['build_release'].__globals__, HERE=source):
            ignition, package, checksum = R['build_release'](self.root / 'release')
        cfg = json.loads(ignition.read_text())
        defaults = next(f for f in cfg['storage']['files'] if f['path'].endswith('account-defaults.json'))
        self.assertEqual(json.loads(base64.b64decode(defaults['contents']['source'].split(',', 1)[1])),
                         {'username': '', 'ssh_key': ''})
        with zipfile.ZipFile(package) as archive:
            expected = {'esphome-appliance/' + name for name in R['SOURCE_FILES']}
            expected.add('esphome-appliance/esphome-appliance.ign')
            self.assertEqual(set(archive.namelist()), expected)
            self.assertEqual(archive.read('esphome-appliance/esphome-appliance.ign'), ignition.read_bytes())
            self.assertNotIn(b'private-user', archive.read('esphome-appliance/esphome-appliance.ign'))
        for line in checksum.read_text().splitlines():
            digest, name = line.split('  ', 1)
            self.assertEqual(digest, hashlib.sha256((checksum.parent / name).read_bytes()).hexdigest())

    def test_deterministic_package(self):
        first = R['build_release'](self.root / 'first')
        second = R['build_release'](self.root / 'second')
        self.assertEqual([p.read_bytes() for p in first], [p.read_bytes() for p in second])

    def test_existing_assets_are_not_overwritten(self):
        output = self.root / 'release'
        output.mkdir()
        asset = output / 'SHA256SUMS'
        asset.write_text('keep')
        with self.assertRaises(ValueError):
            R['build_release'](output)
        self.assertEqual(asset.read_text(), 'keep')
        self.assertEqual(list(output.iterdir()), [asset])

    def test_partial_assets_removed_after_failure(self):
        with patch.object(zipfile.ZipFile, 'writestr', side_effect=OSError('test failure')):
            with self.assertRaises(OSError):
                R['build_release'](self.root / 'release')
        self.assertEqual(list((self.root / 'release').iterdir()), [])

    def test_fedora_selection_in_package_and_checksums(self):
        selection = {'release': '44.20260913.3.2', 'location': 'https://builds.coreos.fedoraproject.org/base.iso', 'sha256': 'a' * 64}
        metadata = self.root / 'selection.json'
        metadata.write_text(json.dumps(selection))
        ignition, package, fedora, checksum = R['build_release'](self.root / 'release', metadata)
        self.assertEqual(json.loads(fedora.read_text()), selection)
        with zipfile.ZipFile(package) as archive:
            self.assertEqual(archive.read('esphome-appliance/fedora-base.json'), fedora.read_bytes())
        self.assertIn(hashlib.sha256(fedora.read_bytes()).hexdigest() + '  fedora-base.json', checksum.read_text())

    def test_invalid_fedora_selection_refused_before_writing_assets(self):
        selection = self.root / 'selection.json'
        selection.write_text(json.dumps({'release': '44.1', 'location': 'https://untrusted.example/base.iso', 'sha256': 'a' * 64}))
        with self.assertRaises(ValueError):
            R['build_release'](self.root / 'release', selection)
        self.assertEqual(list((self.root / 'release').iterdir()), [])

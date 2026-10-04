import base64
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import runpy
import os
import shutil
import subprocess
import struct
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
B = runpy.run_path(str(ROOT / "build-installer.py"))
# Non-operational test-only key: never use in an appliance.
TYPE = b"ssh-ed25519"
KEY = "ssh-ed25519 " + base64.b64encode(struct.pack(">I", len(TYPE)) + TYPE + struct.pack(">I", 32) + bytes(32)).decode()


def decode_files(config):
    return {item["path"]: base64.b64decode(item["contents"]["source"].split(",", 1)[1]).decode()
            for item in config["storage"]["files"] if "contents" in item}


def synthetic_iso(path, serial=False):
    # Minimal ISO9660 fixture containing the documented embed path.
    data = bytearray(512 * 1024)
    def record(name, sector, size):
        name = name.encode()
        result = bytearray(34 + len(name) + (len(name) % 2))
        result[0] = len(result)
        struct.pack_into("<I", result, 2, sector)
        struct.pack_into("<I", result, 10, size)
        result[32] = len(name)
        result[33:33 + len(name)] = name
        return result
    pvd = 16 * 2048
    data[pvd:pvd + 7] = b"\x01CD001\x01"
    root = record(".", 20, 2048)
    data[pvd + 156:pvd + 156 + len(root)] = root
    directory = record("IMAGES", 21, 2048)
    data[20 * 2048:20 * 2048 + len(directory)] = directory
    embed = record("IGNITION.IMG;1", 64, 262144)
    data[21 * 2048:21 * 2048 + len(embed)] = embed
    if serial:
        default = "rw ignition.firstboot ignition.platform.id=metal"
        size = 256
        prefix = b"linux /vmlinuz "
        bootfile = prefix + default.encode() + b"\n" + b"#" * (size - len(default) - 1) + b"# COREOS_KARG_EMBED_AREA\n"
        metadata = json.dumps({"default": default, "size": size, "files": [
            {"path": "EFI/fedora/grub.cfg", "offset": len(prefix), "end": "\n", "pad": "#"},
            {"path": "isolinux/isolinux.cfg", "offset": len(prefix), "end": "\n", "pad": "#"}]}).encode()
        root_records = directory + record("COREOS", 22, 2048) + record("EFI", 23, 2048) + record("ISOLINUX", 24, 2048)
        data[20*2048:20*2048+len(root_records)] = root_records
        for sector, contents in [(22, record("KARGS.JSO;1", 32, len(metadata))),
                                  (23, record("FEDORA", 25, 2048)),
                                  (24, record("ISOLINUX.CFG;1", 31, len(bootfile))),
                                  (25, record("GRUB.CFG;1", 30, len(bootfile))),
                                  (30, bootfile), (31, bootfile), (32, metadata)]:
            data[sector*2048:sector*2048+len(contents)] = contents
    path.write_bytes(data)
    return 64 * 2048, 262144


class BuilderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_iso_roundtrip_and_outside_bytes(self):
        source, output = self.root / "base.iso", self.root / "custom.iso"
        offset, size = synthetic_iso(source)
        before = source.read_bytes()
        digest = B["build_iso"](source, output)
        after = output.read_bytes()
        self.assertEqual(before, source.read_bytes())
        self.assertEqual(before[:offset], after[:offset])
        self.assertEqual(before[offset + size:], after[offset + size:])
        self.assertEqual(len(before), len(after))
        actual = json.loads(B["extract_config"](after[offset:offset + size]))
        self.assertEqual(B["generic_config"](), actual)
        self.assertEqual(hashlib.sha256(after).hexdigest(), digest)
        self.assertIn(digest, output.with_suffix(".iso.sha256").read_text())

    def test_serial_console_changes_ignition_only(self):
        source, output = self.root / "base.iso", self.root / "serial.iso"
        offset, size = synthetic_iso(source, serial=True)
        before = source.read_bytes()
        B['build_iso'](source, output, serial_console=True)
        after = output.read_bytes()
        self.assertEqual(before, source.read_bytes())
        self.assertEqual(len(before), len(after))
        self.assertEqual(before[:offset], after[:offset])
        self.assertEqual(before[offset+size:], after[offset+size:])
        live = json.loads(B['extract_config'](after[offset:offset+size]))
        self.assertIn('--console tty0 --console ttyS0,115200',
                      decode_files(live)['/usr/local/bin/install-esphome-appliance'])
        self.assertNotIn('--console', decode_files(B['generic_config']())['/usr/local/bin/install-esphome-appliance'])

    def test_serial_console_needs_no_bootfile_reserves(self):
        source, output = self.root / "base.iso", self.root / "serial.iso"
        offset, size = synthetic_iso(source)
        before = source.read_bytes()
        B['build_iso'](source, output, serial_console=True)
        after = output.read_bytes()
        self.assertEqual(before[:offset], after[:offset])
        self.assertEqual(before[offset+size:], after[offset+size:])

    def test_serial_console_setting(self):
        env = self.root / '.env'
        for value, expected in [('true', True), ('false', False), ('TRUE', True)]:
            env.write_text('SERIAL_CONSOLE=' + value + '\n')
            self.assertEqual(B['read_settings'](env)['serial_console'], expected)
        env.write_text('SERIAL_CONSOLE=maybe\n')
        with self.assertRaises(ValueError):
            B['read_settings'](env)

    def test_existing_output_never_overwritten(self):
        source, output = self.root / "base.iso", self.root / "custom.iso"
        synthetic_iso(source)
        output.write_bytes(b"keep me")
        with self.assertRaises(ValueError):
            B["build_iso"](source, output)
        self.assertEqual(output.read_bytes(), b"keep me")

    def test_existing_checksum_never_overwritten(self):
        source, output = self.root / "base.iso", self.root / "custom.iso"
        synthetic_iso(source)
        checksum = output.with_suffix(".iso.sha256")
        checksum.write_text("keep me")
        with self.assertRaises(ValueError):
            B["build_iso"](source, output)
        self.assertEqual(checksum.read_text(), "keep me")
        self.assertFalse(output.exists())

    def test_partial_output_removed_on_failure(self):
        source, output = self.root / "base.iso", self.root / "custom.iso"
        synthetic_iso(source)
        with patch.dict(B["build_iso"].__globals__, extract_config=lambda blob: b"corrupt"):
            with self.assertRaises(ValueError):
                B["build_iso"](source, output)
        self.assertFalse(output.exists())
        self.assertFalse(output.with_suffix(".iso.sha256").exists())

    def test_customized_base_rejected(self):
        source, output = self.root / "base.iso", self.root / "custom.iso"
        offset, _ = synthetic_iso(source)
        with source.open("r+b") as fp:
            fp.seek(offset)
            fp.write(b"x")
        with self.assertRaises(ValueError):
            B["build_iso"](source, output)
        self.assertFalse(output.exists())

    def test_malformed_iso_rejected(self):
        for data in [b"", b"not an ISO", b"\x00" * 65536]:
            with self.assertRaises(ValueError):
                B["find_embed"](io.BytesIO(data))

    def test_build_settings(self):
        env = self.root / '.env'
        env.write_text('MIN_DISK_GIB=60\nMAX_DISK_GIB=100\nNTP_POOLS="0.pool.ntp.org 1.pool.ntp.org"\n')
        settings = B["read_settings"](env)
        self.assertEqual(settings["min_disk_gib"], 60)
        self.assertEqual(settings["max_disk_gib"], 100)
        self.assertEqual(settings["ntp_pools"], ["0.pool.ntp.org", "1.pool.ntp.org"])

    def test_account_defaults_and_global_ntp(self):
        env = self.root / '.env'
        env.write_text('USERNAME=builder\nSSH_KEY="' + KEY + ' fixture comment"\n')
        settings = B["read_settings"](env)
        self.assertEqual(settings["username"], "builder")
        self.assertEqual(settings["ssh_key"], KEY + " fixture comment")
        self.assertEqual(settings["ntp_pools"], [f"{n}.pool.ntp.org" for n in [2, 0, 1, 3]])
        source, output = self.root / "base.iso", self.root / "personalized.iso"
        offset, size = synthetic_iso(source)
        B["build_iso"](source, output, **settings)
        live = json.loads(B["extract_config"](output.read_bytes()[offset:offset + size]))
        defaults = json.loads(decode_files(live)["/etc/esphome-appliance/account-defaults.json"])
        self.assertEqual(defaults, {"username": "builder", "ssh_key": KEY + " fixture comment"})
        template = json.loads(decode_files(live)["/etc/esphome-appliance/template.ign"])
        chrony = decode_files(template)["/etc/chrony.conf"]
        self.assertIn("pool 2.pool.ntp.org iburst", chrony)
        self.assertNotIn(".is.pool.ntp.org", chrony)

    def test_standalone_verifier_and_cli(self):
        import sys
        source, output = self.root / 'original.iso', self.root / 'appliance.iso'
        offset, size = synthetic_iso(source)
        B['build_iso'](source, output, username='operator')
        expected = B['generic_config'](username='operator')
        self.assertEqual(B['verify_iso'](source, output, expected), expected)
        with self.assertRaisesRegex(ValueError, 'Embedded Ignition'):
            B['verify_iso'](source, output, B['generic_config']())
        expected_file, extracted = self.root / 'expected.ign', self.root / 'inspect.ign'
        expected_file.write_text(json.dumps(expected, indent=2))
        command = [sys.executable, str(ROOT / 'build-installer.py'), 'verify', str(source), str(output), '--ignition', str(expected_file), '--extract-ignition', str(extracted)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(extracted.read_text()), expected)
        # Inspection output is exclusive: a second invocation cannot overwrite it.
        self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
        before = output.read_bytes()
        for changed in (before[:-1], b'X' + before[1:], before[:offset] + bytes(size) + before[offset+size:]):
            output.write_bytes(changed)
            with self.assertRaises((ValueError, EOFError, B['lzma'].LZMAError)):
                B['verify_iso'](source, output)
        output.write_bytes(before)
        # An additional embedded file must not hide behind the readable first config.
        archive = B['cpio_entry']('config.ign', json.dumps(expected).encode(), 1) + B['cpio_entry']('extra', b'not allowed', 2) + B['cpio_entry']('TRAILER!!!', b'', 3)
        compressed = B['lzma'].compress(archive)
        output.write_bytes(before[:offset] + compressed + bytes(size-len(compressed)) + before[offset+size:])
        with self.assertRaisesRegex(ValueError, 'Unexpected files'):
            B['verify_iso'](source, output)

    def test_invalid_settings(self):
        env = self.root / '.env'
        for bad in ['MIN_DISK_GIB=9', 'USERNAME=root', 'USERNAME=esphome', 'USERNAME=bad/name', 'SSH_KEY_FILE=key.pub', 'PLATFORM=hyperv',
                    'INSTALL_DEVICE=/dev/sda', 'MIN_DISK_GIB=90\nMAX_DISK_GIB=80',
                    'NTP_POOLS="x;touch"', 'NTP_POOLS="unclosed', 'UNKNOWN=x',
                    'MIN_DISK_GIB=50\nMIN_DISK_GIB=60']:
            env.write_text(bad + "\n")
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                B["read_settings"](env)

    def test_generic_iso_has_no_shared_access_credentials(self):
        live = B["generic_config"]()
        self.assertEqual(live["passwd"]["users"], [{"name": "core", "shouldExist": False}])
        files = decode_files(live)
        self.assertNotIn(KEY, json.dumps(live))
        template = json.loads(files['/etc/esphome-appliance/template.ign'])
        user = template['passwd']['users'][1]
        self.assertEqual(user['name'], '@USERNAME@')
        self.assertEqual(user['sshAuthorizedKeys'], ['@SSH_KEY@'])
        provision = files['/usr/local/bin/provision-esphome-appliance']
        self.assertIn('https://github.com/$github_username.keys', provision)
        self.assertIn('ssh-keygen -lf /run/esphome/github.keys ||', provision)
        units = {u['name']: u['contents'] for u in live['systemd']['units']}
        self.assertIn('Requires=esphome-provision.service', units['install-esphome-appliance.service'])
        self.assertIn('After=esphome-provision.service', units['install-esphome-appliance.service'])
        self.assertIn('StandardInput=tty', units['esphome-provision.service'])

    def test_common_configuration_and_markers(self):
        dest = B["configs"](KEY, "builder", ntp_pools=["0.pool.ntp.org"])
        files = decode_files(dest)
        live_files = decode_files(B["generic_config"](min_disk_gib=60, max_disk_gib=100))
        self.assertEqual(files["/etc/hostname"], "esphome-builder-????????????\n")
        self.assertIn("Management user: builder", files["/etc/motd.d/20-esphome-appliance.motd"])
        self.assertNotIn("@NTP_POOLS@", files["/etc/motd.d/20-esphome-appliance.motd"])
        self.assertIn("pool 0.pool.ntp.org iburst", files["/etc/chrony.conf"])
        self.assertIn("wipefs --no-act", live_files["/usr/local/bin/install-esphome-appliance"])
        self.assertIn("60-100 GiB", live_files["/usr/local/bin/install-esphome-appliance"])
        self.assertNotIn("/dev/sda", live_files["/usr/local/bin/install-esphome-appliance"])
        # tmpfiles escapes the literal backslash in the systemd device-unit name.
        for link in dest["storage"]["links"][:1]:
            self.assertTrue(any(line.split()[:2] == ["z", link["path"].replace("\\", "\\\\")]
                for line in files["/etc/tmpfiles.d/esphome-appliance.conf"].splitlines()))
        units = {u["name"]: u.get("contents", "") for u in dest["systemd"]["units"]}
        tools = units["esphome-tools.service"]
        self.assertIn("install --idempotent --allow-inactive binutils", tools)
        starts = [line.split("=", 1)[1] for line in tools.splitlines() if line.startswith("ExecStart=")]
        self.assertEqual(starts[0], "-/usr/bin/rpm-ostree upgrade --bypass-driver")
        self.assertFalse(starts[1].startswith("-"))
        self.assertFalse(starts[2].startswith("-"))
        self.assertTrue(starts[1].startswith("/usr/bin/rpm-ostree install "))
        self.assertIn("systemd-run --unit=esphome-image-pull --wait --pipe --collect --uid=esphome", starts[2])
        self.assertTrue(starts[2].endswith("/usr/bin/podman pull ghcr.io/esphome/esphome:stable"))
        self.assertIn("Before=multi-user.target getty-pre.target", tools)
        self.assertIn("SuccessAction=reboot", tools)
        self.assertNotIn("RemainAfterExit", tools)
        self.assertIn("StandardOutput=journal+console", tools)
        self.assertIn("ConditionPathExists=!/etc/esphome-appliance-release", tools)
        self.assertNotIn(".packages-layered", json.dumps(dest))
        self.assertIn("ExecStartPost=/usr/bin/mv -T /etc/esphome-appliance-release.pending /etc/esphome-appliance-release", tools)
        self.assertIn("Requires=user-runtime-dir@2000.service", tools)
        self.assertNotIn("Requires=user@2000.service", tools)
        self.assertNotIn(".packages-layered", files["/etc/tmpfiles.d/esphome-appliance.conf"])
        self.assertNotIn("run-esphomemetadata.mount", units)
        self.assertNotIn("esphome-hostname.service", units)
        self.assertNotIn("/usr/local/bin/esphome-hostname", files)
        self.assertNotIn(".hostname-set", json.dumps(dest))
        self.assertNotIn("cidata", json.dumps(dest))
        self.assertNotIn("systemd-detect-virt", B["PROVISION"])
        dropins = {u["name"]: u.get("dropins", []) for u in dest["systemd"]["units"]}
        self.assertNotIn("hypervkvpd", json.dumps(dest))
        self.assertNotIn("qemu-guest-agent", json.dumps(dest))
        self.assertNotIn("ConditionVirtualization=", json.dumps(B["generic_config"]()["systemd"]))

    @unittest.skipUnless(os.name == 'posix' and shutil.which('bash'), 'Needs POSIX bash')
    def test_builder_shell_reuses_process_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            environ = root / 'environ'
            environ.write_bytes(b'ESPHOME_BUILD_PATH=/build\0PLATFORMIO_PACKAGES_DIR=/cache/platformio/packages\0CCACHE_DIR=/ccache\0VALUE_WITH_SPACES=keep spaces $literal\0')
            command = B['ESPHOME_SHELL'].split("/bin/bash -c '", 1)[1].rsplit("'", 1)[0]
            command = command.replace('/proc/1/environ', str(environ))
            result = subprocess.run(['bash', '-c', command], input='printf "%s\\n" "$ESPHOME_BUILD_PATH" "$PLATFORMIO_PACKAGES_DIR" "$CCACHE_DIR" "$VALUE_WITH_SPACES"\n', text=True, capture_output=True, check=True)
            self.assertEqual(result.stdout.splitlines(), ['/build', '/cache/platformio/packages', '/ccache', 'keep spaces $literal'])
            dest = B['configs']('ssh-ed25519 TEST', 'operator')
            helper = next(f for f in dest['storage']['files'] if f['path'] == '/usr/local/bin/esphome')
            self.assertEqual(helper['mode'], 0o755)
            self.assertIn('--workdir /config esphome-builder', B['ESPHOME_SHELL'])

    @unittest.skipUnless(os.name == 'posix' and shutil.which('bash'), 'Needs POSIX bash')
    def test_shell_switches_account_before_container_exec(self):
        mock = self.root / 'capture'
        mock.write_text('#!/bin/bash\nprintf "%s\\n" "$@"\n')
        mock.chmod(0o755)
        script = B['ESPHOME_SHELL'].replace('/usr/bin/run0', str(mock)).replace('/usr/bin/podman', str(mock))
        for username in ('operator', 'esphome'):
            command = 'function id { echo ' + username + '; }\n' + script
            result = subprocess.run(['bash', '-c', command], capture_output=True, text=True, check=True)
            args = result.stdout.splitlines()
            if username == 'operator':
                self.assertEqual(args, ['-u', 'esphome',
                                       '--setenv=XDG_RUNTIME_DIR=/run/user/2000', '/usr/local/bin/esphome', 'shell'])
            else:
                self.assertEqual(args[:6], ['exec', '--interactive', '--tty', '--workdir', '/config', 'esphome-builder'])
                self.assertNotIn('/usr/bin/run0', args)

    def test_rootless_account_and_boot_gating(self):
        dest = B['configs'](KEY, 'operator')
        files = decode_files(dest)
        account = next(u for u in dest['passwd']['users'] if u['name'] == 'esphome')
        self.assertEqual(account['uid'], 2000)
        self.assertEqual(account['shell'], '/usr/sbin/nologin')
        self.assertFalse(account.get('groups'))
        self.assertFalse(account.get('sshAuthorizedKeys'))
        # Every parent beneath HOME must belong to the rootless user, not just the leaf.
        self.assertNotIn('directories', dest['storage'])
        tmpfiles = files['/etc/tmpfiles.d/esphome-appliance.conf']
        rules = [line.split() for line in tmpfiles.splitlines()
                 if line.strip() and not line.lstrip().startswith('#')]
        directories = {fields[1]: fields for fields in rules if fields[0] == 'd'}
        for name in ('config', 'cache', 'build', 'ccache'):
            self.assertEqual(directories['/var/lib/esphome/' + name][2:5],
                             ['0700', 'esphome', 'esphome'])
        for item in dest['storage']['files'] + dest['storage']['links']:
            parent = PurePosixPath(item['path']).parent
            while parent.is_relative_to('/var/home/esphome'):
                self.assertEqual(directories[str(parent)][3:5], ['esphome', 'esphome'])
                parent = parent.parent
        self.assertNotIn('/var/lib/systemd/linger/esphome', files)
        self.assertIn(['f', '/var/lib/systemd/linger/esphome', '0644', 'root', 'root', '-', '-'], rules)
        quadlet = files['/etc/containers/systemd/users/2000/esphome-builder.container']
        self.assertNotIn('ConditionPathExists=', quadlet)
        manager = next(u for u in dest['systemd']['units'] if u['name'] == 'user@2000.service')
        self.assertEqual(manager['dropins'][0]['contents'],
                         '[Unit]\nRequires=esphome-tools.service\nAfter=esphome-tools.service\n')
        self.assertNotIn('WantedBy=', quadlet)
        self.assertNotIn('Restart=', quadlet)
        self.assertNotIn('Wants=esphome-builder.service', files['/var/home/esphome/.config/systemd/user/podman-auto-update.timer.d/10-appliance-schedule.conf'])
        self.assertNotIn('esphome-tools.service', quadlet)
        self.assertNotIn('/etc/containers/systemd/esphome-builder.container', files)
        system_names = {u['name'] for u in dest['systemd']['units']}
        self.assertNotIn('podman-auto-update.timer', system_names)
        self.assertNotIn('esphome-image-clean.timer', system_names)
        for path in ('/etc/subuid', '/etc/subgid'):
            item = next(f for f in dest['storage']['files'] if f['path'] == path)
            self.assertEqual(base64.b64decode(item['append'][0]['source'].split(',', 1)[1]).decode(), 'esphome:524288:65536\n')
        for name in ('podman-auto-update.timer.d/10-appliance-schedule.conf', 'esphome-image-clean.timer'):
            self.assertNotIn('ConditionPathExists=',
                          files['/var/home/esphome/.config/systemd/user/' + name])
        tmpfiles = files['/etc/tmpfiles.d/esphome-appliance.conf']
        for fields in directories.values():
            if fields[1] not in ('/var/lib/esphome', '/usr/local/libexec'):
                self.assertEqual(fields[3:5], ['esphome', 'esphome'])
        self.assertIn('/usr/bin/run0 -u esphome --setenv=XDG_RUNTIME_DIR=/run/user/2000 /usr/local/bin/esphome shell', B['ESPHOME_SHELL'])
        self.assertNotIn('/etc/esphome-appliance-release', files)
        self.assertIn('/etc/esphome-appliance-release.pending', files)
        self.assertNotIn('/etc/os-release', files)
        self.assertNotIn('/usr/lib/os-release', files)

    @unittest.skipUnless(os.name == 'posix' and shutil.which('bash'), 'Needs POSIX bash')
    def test_command_helpers_forward_arguments_and_exit_status(self):
        dest = B['configs'](KEY, 'operator')
        files = decode_files(dest)
        self.assertIn('/usr/local/bin/esphome', files)
        for verb in ('logs', 'start', 'stop', 'restart', 'status', 'shell', 'pairing', 'update', 'timers'):
            self.assertNotIn('/usr/local/bin/esphome-' + verb, files)
        mock = self.root / 'run0'
        mock.write_text('#!/bin/bash\nprintf "%s\\0" "$@"\nexit "${HELPER_EXIT:-0}"\n')
        mock.chmod(0o755)
        cases = {
            'logs': ['/usr/bin/journalctl', '_UID=2000', '_SYSTEMD_USER_UNIT=esphome-builder.service'],
            **{verb: ['/usr/bin/systemctl', '--user', verb, 'esphome-builder.service']
               for verb in ('start', 'stop', 'restart', 'status')},
            'update': ['/usr/bin/systemctl', '--user', 'start', 'podman-auto-update.service'],
            'timers': ['/usr/bin/systemctl', '--user', 'list-timers', 'podman-auto-update.timer', 'esphome-image-clean.timer'],
        }
        forwarded = ['--since', '2026-10-03 12:00:00', 'literal $value; no expansion']
        for verb, expected in cases.items():
            with self.subTest(verb=verb):
                path = '/usr/local/bin/esphome'
                helper = next(f for f in dest['storage']['files'] if f['path'] == path)
                self.assertEqual(helper['mode'], 0o755)
                script = self.root / ('esphome-' + verb)
                script.write_text(files[path].replace('/usr/bin/run0', str(mock)))
                result = subprocess.run(['bash', str(script), verb, *forwarded], capture_output=True,
                                        env=dict(os.environ, HELPER_EXIT='23'))
                self.assertEqual(result.returncode, 23)
                args = result.stdout.decode().split('\0')[:-1]
                if verb != 'logs':
                    self.assertEqual(args[:3], ['-u', 'esphome', '--setenv=XDG_RUNTIME_DIR=/run/user/2000'])
                    args = args[3:]
                self.assertEqual(args, expected + forwarded)

    @unittest.skipUnless(os.name == 'posix' and shutil.which('bash') and shutil.which('jq'), 'Needs Bash and jq')
    def test_pairing_boot_gate_and_explicit_helper(self):
        import shlex
        files = decode_files(B['configs'](KEY, 'operator'))
        unit = files['/etc/systemd/user/esphome-start-paired.service']
        self.assertIn('ConditionUser=esphome', unit)
        self.assertIn('ConditionPathExists=/var/lib/esphome/config/.receiver_peers.json', unit)
        command = shlex.split(next(line.split('=', 1)[1] for line in unit.splitlines() if line.startswith('ExecCondition=')))
        state = self.root / 'peers.json'
        command[-1] = str(state)
        peer = dict(dashboard_id='dashboard', static_x25519_pub=base64.b64encode(bytes(32)).decode(), label='test', pin_sha256='a'*64, paired_at=1.0)
        for data, expected in [(None, False), ({'peers': []}, False), ({'peers': [peer]}, True), ({'peers': [peer, {}]}, False), ({'peers': [{**peer, 'static_x25519_pub': 'invalid'}]}, False), ('broken', False)]:
            if data is None:
                state.unlink(missing_ok=True)
            else:
                state.write_text(data if isinstance(data, str) else json.dumps(data))
            result = subprocess.run(command, capture_output=True)
            self.assertEqual(result.returncode == 0, expected, data)
        start, logs = self.root / 'start', self.root / 'logs'
        start.write_text('#!/bin/bash\nexit "${START_EXIT:-0}"\n'); start.chmod(0o755)
        logs.write_text('#!/bin/bash\nprintf "%s\\0" "$@"\n'); logs.chmod(0o755)
        helper = self.root / 'pairing'
        helper.write_text(files['/usr/local/bin/esphome'].replace('/usr/local/bin/esphome start', str(start)).replace('/usr/local/bin/esphome logs', str(logs)))
        result = subprocess.run(['bash', str(helper), 'pairing', '--no-pager'], capture_output=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.split(b'\0')[:-1], [b'--follow', b'--lines=30', b'--no-pager'])
        result = subprocess.run(['bash', str(helper), 'pairing'], env={**os.environ, 'START_EXIT':'23'}, capture_output=True)
        self.assertEqual(result.returncode, 23)
        self.assertEqual(result.stdout, b'')

    @unittest.skipUnless(os.name == 'posix' and shutil.which('bash'), 'Needs POSIX bash')
    def test_completion_context_and_no_privileged_queries(self):
        dest = B['configs'](KEY, 'operator')
        files = decode_files(dest)
        item = next(f for f in dest['storage']['files'] if f['path'] == '/etc/bash_completion.d/esphome-appliance')
        self.assertEqual(item['mode'], 0o644)
        self.assertIn('bash-completion', next(u['contents'] for u in dest['systemd']['units'] if u['name'] == 'esphome-tools.service'))
        script = self.root / 'completion.bash'
        script.write_text(files[item['path']])
        harness = r"""
            source "$1"
            run0() { echo 'Unexpected privilege request' >&2; exit 99; }
            _journalctl() { printf '%s\0' "$COMP_CWORD" "${COMP_WORDS[@]}"; }
            _systemctl() { printf '%s\0' "$COMP_CWORD" "${COMP_WORDS[@]}"; }
            COMP_WORDS=(esphome "$2" --output json)
            COMP_CWORD=3
            _esphome_complete
            [[ $COMP_CWORD == 3 && ${COMP_WORDS[0]} == esphome ]] || exit 98
        """
        for verb in ('logs', 'pairing', 'start', 'stop', 'restart', 'status', 'update', 'timers'):
            result = subprocess.run(['bash', '-c', harness, 'completion-test', str(script), verb], capture_output=True, check=True)
            args = result.stdout.decode().split('\0')[:-1]
            if verb in ('logs', 'pairing'):
                self.assertEqual(args, ['2', 'journalctl', '--output', 'json'])
            else:
                command = 'start' if verb == 'update' else 'list-timers' if verb == 'timers' else verb
                units = ['podman-auto-update.timer', 'esphome-image-clean.timer'] if verb == 'timers' else ['podman-auto-update.service'] if verb == 'update' else ['esphome-builder.service']
                self.assertEqual(args, [str(4 + len(units)), 'systemctl', '--user', command, *units, '--output', 'json'])
        no_unit_query = harness.replace('COMP_WORDS=(esphome "$2" --output json)', 'COMP_WORDS=(esphome "$2" extra-unit)').replace('COMP_CWORD=3', 'COMP_CWORD=2').replace('$COMP_CWORD == 3', '$COMP_CWORD == 2')
        result = subprocess.run(['bash', '-c', no_unit_query, 'completion-test', str(script), 'start'], capture_output=True, check=True)
        self.assertEqual(result.stdout, b'')

    @unittest.skipUnless(os.name == 'posix' and shutil.which('bash') and
                         Path('/usr/share/bash-completion/bash_completion').is_file() and
                         all(Path('/usr/share/bash-completion/completions', name).is_file() for name in ('journalctl', 'systemctl')),
                         'Needs installed systemd Bash completion')
    def test_helpers_delegate_to_native_completion(self):
        harness = r"""
            source /usr/share/bash-completion/bash_completion
            source "$1"
            run0() { echo 'Unexpected privilege request' >&2; exit 99; }
            COMP_WORDS=(esphome pa); COMP_CWORD=1
            _esphome_complete
            [[ " ${COMPREPLY[*]} " == *' pairing '* ]] || exit 4
            COMP_WORDS=(esphome logs --fo); COMP_CWORD=2
            COMP_LINE='esphome logs --fo'; COMP_POINT=${#COMP_LINE}
            _esphome_complete
            [[ " ${COMPREPLY[*]} " == *' --follow '* ]] || exit 1
            COMP_WORDS=(esphome start --no-bl); COMP_CWORD=2
            COMP_LINE='esphome start --no-bl'; COMP_POINT=${#COMP_LINE}
            _esphome_complete
            [[ " ${COMPREPLY[*]} " == *' --no-block '* ]] || exit 2
            COMP_WORDS=(esphome logs -o j); COMP_CWORD=3
            COMP_LINE='esphome logs -o j'; COMP_POINT=${#COMP_LINE}
            _esphome_complete
            [[ " ${COMPREPLY[*]} " == *' json '* ]] || exit 3
        """
        subprocess.run(['bash', '-c', harness, 'completion-test', str(ROOT / 'esphome-completion.bash')], capture_output=True, text=True, check=True)

    @unittest.skipUnless(shutil.which('node'), 'Needs JavaScript runtime')
    def test_passwordless_run0_policy(self):
        dest = B['configs'](KEY, 'operator')
        files = decode_files(dest)
        rule = files['/etc/polkit-1/rules.d/10-esphome-run0.rules']
        self.assertFalse(any('/sudoers' in path for path in files))
        harness = """
            let check;
            const polkit = {Result: {YES: 'yes'}, addRule: callback => {check = callback;}};
        """ + rule + """
            for (const who of ['operator', 'esphome', 'other']) {
                for (const id of ['org.freedesktop.systemd1.manage-units',
                                  'org.freedesktop.systemd1.manage-unit-files',
                                  'org.freedesktop.login1.reboot']) {
                    const result = check({id}, {isInGroup: group => who === 'operator' && group === 'wheel',
                                              local: false, active: false});
                    const allowed = who === 'operator' && id === 'org.freedesktop.systemd1.manage-units';
                    if ((result === 'yes') !== allowed) throw new Error(who + ':' + id);
                }
            }
        """
        subprocess.run(['node', '-e', harness], capture_output=True, text=True, check=True)

    @unittest.skipUnless(os.name == 'posix' and shutil.which('bash'), 'Needs POSIX bash')
    def test_disable_autologin_retries_and_self_deletes_only_on_success(self):
        files = decode_files(B['configs'](KEY, 'operator'))
        source = files['/usr/local/libexec/esphome-disable-autologin']
        self.assertIn('set -euo pipefail', source)
        for failure in ('none', 'partial', 'reload', 'self'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                first = root / 'getty@.service.d/autologin.conf'
                second = root / 'serial-getty@.service.d/autologin.conf'
                for target in (first, second):
                    target.parent.mkdir()
                    target.write_text('autologin')
                helper = root / 'esphome-disable-autologin'
                mock_rm = root / 'rm'
                mock_rm.write_text('#!/bin/bash\nfor target in "$@"; do\n case "$target" in -f|--) continue ;; esac\n [[ "$target" != "${FAIL_PATH:-}" ]] || exit 1\n /usr/bin/rm -f -- "$target" || exit $?\ndone\n')
                mock_ctl = root / 'systemctl'
                mock_ctl.write_text('#!/bin/bash\n[[ "$*" == daemon-reload ]] || exit 99\ntouch "$RELOAD_LOG"\nexit "${FAIL_RELOAD:-0}"\n')
                for executable in (mock_rm, mock_ctl): executable.chmod(0o755)
                helper.write_text(source.replace('/etc/systemd/system', str(root)).replace('/usr/local/libexec/esphome-disable-autologin', str(helper)).replace('/usr/bin/rm', str(mock_rm)).replace('/usr/bin/systemctl', str(mock_ctl)))
                log = root / 'reloaded'
                env = dict(os.environ, RELOAD_LOG=str(log), FAIL_PATH=str(second) if failure == 'partial' else str(helper) if failure == 'self' else '', FAIL_RELOAD='1' if failure == 'reload' else '0')
                result = subprocess.run(['bash', str(helper), 'pairing'], env=env, capture_output=True)
                if failure == 'none':
                    self.assertEqual(result.returncode, 0)
                    self.assertFalse(helper.exists())
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertTrue(helper.exists())
                    self.assertFalse(first.exists())
                    self.assertEqual(log.exists(), failure != 'partial')
                    env.update(FAIL_PATH='', FAIL_RELOAD='0')
                    subprocess.run(['bash', str(helper), 'pairing'], env=env, capture_output=True, check=True)
                    self.assertFalse(helper.exists())
                self.assertFalse(first.exists())
                self.assertFalse(second.exists())
                self.assertTrue(log.exists())

    def test_console_autologin_uses_account_only_on_installed_system(self):
        dest = B['configs']('ssh-ed25519 TEST', 'operator')
        units = {u['name']: u for u in dest['systemd']['units']}
        for name in ('getty@.service', 'serial-getty@.service'):
            contents = units[name]['dropins'][0]['contents']
            self.assertIn('[Service]\nExecStart=\n', contents)
            self.assertIn('ExecStart=-/usr/bin/agetty --autologin operator --noclear ', contents)
            self.assertTrue(contents.endswith('- ${TERM}\n'))
            if name == 'serial-getty@.service':
                self.assertIn('--keep-baud 115200,57600,38400,9600', contents)
            self.assertNotIn('SetCredential=', contents)
            self.assertNotIn('enabled', units[name])
        self.assertNotIn('--autologin', json.dumps(B['generic_config']()['systemd']))

    @unittest.skipUnless(os.name == "posix" and shutil.which("bash"), "Needs Linux/POSIX bash")
    def test_guest_script_syntax(self):
        for name in ["INSTALL", "PROVISION", "ESPHOME_SHELL", "ESPHOME_PAIRING", "DISABLE_AUTOLOGIN"]:
            script = self.root / (name + ".sh")
            script.write_text(B[name])
            result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_startup_ordering_no_cycles(self):
        dest = B["configs"](KEY, "builder")
        units = dest["systemd"]["units"] + [{"name": "esphome-builder.service",
            "contents": (ROOT / "esphome-builder.container").read_text()}]
        edges = {}
        def edge(a, b):
            edges.setdefault(a, set()).add(b)
        for a, b in [("local-fs.target", "systemd-tmpfiles-setup.service"),
                     ("systemd-tmpfiles-setup.service", "sysinit.target"),
                     ("sysinit.target", "basic.target"), ("sysinit.target", "timers.target"),
                     ("basic.target", "user-runtime-dir@2000.service"),
                     ("user-runtime-dir@2000.service", "user@2000.service"),
                     ("basic.target", "network.target"), ("network.target", "network-online.target"),
                     ("getty-pre.target", "getty@tty1.service"),
                     ("getty@tty1.service", "getty.target"), ("getty.target", "multi-user.target")]:
            edge(a, b)
        for item in units:
            name = item["name"]
            body = item.get("contents", "") + "\n" + "\n".join(d["contents"] for d in item.get("dropins", []))
            if name.endswith(".service"):
                edge("basic.target", name)
            if name.endswith(".mount") and "DefaultDependencies=no" not in body:
                edge(name, "local-fs.target")
            for line in body.splitlines():
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                for other in value.split():
                    if key == "After": edge(other, name)
                    if key in {"Before", "WantedBy"}: edge(name, other)
        active, done = set(), set()
        def visit(name):
            self.assertNotIn(name, active, "Ordering cycle: " + name)
            if name in done: return
            active.add(name)
            for other in edges.get(name, []): visit(other)
            active.remove(name)
            done.add(name)
        for name in list(edges): visit(name)

if __name__ == "__main__":
    unittest.main()

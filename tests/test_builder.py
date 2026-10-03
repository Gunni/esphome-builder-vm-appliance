import base64
import hashlib
import io
import json
from pathlib import Path
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
            for item in config["storage"]["files"]}


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
        env = self.root / 'settings.env'
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
        env = self.root / "settings.env"
        env.write_text('MIN_DISK_GIB=60\nMAX_DISK_GIB=100\nNTP_POOLS="0.pool.ntp.org 1.pool.ntp.org"\n')
        settings = B["read_settings"](env)
        self.assertEqual(settings["min_disk_gib"], 60)
        self.assertEqual(settings["max_disk_gib"], 100)
        self.assertEqual(settings["ntp_pools"], ["0.pool.ntp.org", "1.pool.ntp.org"])

    def test_account_defaults_and_global_ntp(self):
        env = self.root / "settings.env"
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

    def test_invalid_settings(self):
        env = self.root / "settings.env"
        for bad in ['USERNAME=root', 'USERNAME=bad/name', 'SSH_KEY_FILE=key.pub', 'PLATFORM=hyperv',
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
        self.assertNotIn('ssh-keygen', provision)
        units = {u['name']: u['contents'] for u in live['systemd']['units']}
        self.assertIn('Requires=esphome-provision.service', units['install-esphome-appliance.service'])
        self.assertIn('After=esphome-provision.service', units['install-esphome-appliance.service'])
        self.assertIn('StandardInput=tty', units['esphome-provision.service'])

    def test_common_configuration_and_markers(self):
        dest = B["configs"](KEY, "builder", ntp_pools=["0.pool.ntp.org"])
        files = decode_files(dest)
        live_files = decode_files(B["generic_config"](min_disk_gib=60, max_disk_gib=100))
        self.assertEqual(files["/etc/hostname"], "esphome-builder-????????????\n")
        self.assertIn("SSH user: builder", files["/etc/motd.d/20-esphome-appliance"])
        self.assertNotIn("@NTP_POOLS@", files["/etc/motd.d/20-esphome-appliance"])
        self.assertIn("pool 0.pool.ntp.org iburst", files["/etc/chrony.conf"])
        self.assertIn("cmp --silent", live_files["/usr/local/bin/install-esphome-appliance"])
        self.assertIn("60-100 GiB", live_files["/usr/local/bin/install-esphome-appliance"])
        self.assertNotIn("/dev/sda", live_files["/usr/local/bin/install-esphome-appliance"])
        # tmpfiles escapes the literal backslash in the systemd device-unit name.
        for link in dest["storage"]["links"]:
            self.assertTrue(any(line.split()[:2] == ["z", link["path"].replace("\\", "\\\\")]
                for line in files["/etc/tmpfiles.d/esphome-appliance.conf"].splitlines()))
        units = {u["name"]: u.get("contents", "") for u in dest["systemd"]["units"]}
        tools = units["esphome-tools.service"]
        self.assertIn("install --idempotent --allow-inactive binutils", tools)
        starts = [line.split("=", 1)[1] for line in tools.splitlines() if line.startswith("ExecStart=")]
        self.assertEqual(starts[0], "/usr/bin/rpm-ostree upgrade --bypass-driver")
        self.assertTrue(starts[1].startswith("/usr/bin/rpm-ostree install "))
        self.assertEqual(starts[2], "/usr/bin/podman pull ghcr.io/esphome/esphome:stable")
        self.assertIn("Before=multi-user.target getty-pre.target", tools)
        self.assertIn("SuccessAction=reboot", tools)
        self.assertNotIn("RemainAfterExit", tools)
        self.assertIn("StandardOutput=journal+console", tools)
        self.assertIn("ConditionPathExists=!/var/lib/esphome/.packages-layered", tools)
        self.assertIn("/var/lib/esphome/.packages-layered", files["/etc/esphome-appliance/packages-complete.conf"])
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
        for item in B["generic_config"]()["systemd"]["units"]:
            for virt in ["microsoft", "kvm", "qemu"]:
                self.assertIn("ConditionVirtualization=|" + virt, item["contents"])

    @unittest.skipUnless(os.name == "posix" and shutil.which("bash"), "Needs Linux/POSIX bash")
    def test_guest_script_syntax(self):
        for name in ["INSTALL", "PROVISION"]:
            script = self.root / (name + ".sh")
            script.write_text(B[name])
            result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_startup_ordering_no_cycles(self):
        dest = B["configs"](KEY, "builder")
        units = dest["systemd"]["units"] + [{"name": "esphome-builder.service",
            "contents": (ROOT / ".esphome-builder.container").read_text()}]
        edges = {}
        def edge(a, b):
            edges.setdefault(a, set()).add(b)
        for a, b in [("local-fs.target", "systemd-tmpfiles-setup.service"),
                     ("systemd-tmpfiles-setup.service", "sysinit.target"),
                     ("sysinit.target", "basic.target"), ("sysinit.target", "timers.target"),
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

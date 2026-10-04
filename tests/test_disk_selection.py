"""Execute the installer with mocked disk tools; never access a real disk."""
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import unittest

B = runpy.run_path(str(Path(__file__).resolve().parents[1] / "build-installer.py"))


@unittest.skipUnless(os.name == "posix" and shutil.which("bash"), "Needs Linux/POSIX bash")
class DiskSelectionTests(unittest.TestCase):
    def test_disk_refusals_and_eligible_largest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            large, small = root / "large", root / "small"
            large.touch(); small.touch()
            size = 64 * 1024 ** 3
            commands = {
                "lsblk": '''#!/bin/bash
if [[ "$*" == *--nodeps* ]]; then cat "$FIXTURE/inventory";
elif [[ "$*" == *MOUNTPOINTS* ]]; then
    if [[ -f "$FIXTURE/rechecked" && -f "$FIXTURE/race-mounts" ]]; then cat "$FIXTURE/race-mounts"; else cat "$FIXTURE/mounts"; fi
else
    file="$FIXTURE/nodes"
    if [[ -f "$FIXTURE/queried" ]]; then
        touch "$FIXTURE/rechecked"
        [[ ! -f "$FIXTURE/race-nodes" ]] || file="$FIXTURE/race-nodes"
    fi
    touch "$FIXTURE/queried"
    while read -r node type; do
        if [[ -z "$type" ]]; then [[ "$node" == "$FIXTURE/large" ]] && type=disk || type=part; fi
        printf '%s %s\n' "$node" "$type"
    done < "$file"
fi
''',
                "wipefs": '#!/bin/bash\nif [[ -f "$FIXTURE/rechecked" && -f "$FIXTURE/race-signatures" ]]; then cat "$FIXTURE/race-signatures"; else cat "$FIXTURE/signatures"; fi\n',
                "cmp": '#!/bin/bash\ntouch "$FIXTURE/full-disk-scan"\nexit 99\n',
                "blockdev": '#!/bin/bash\necho "$DISK_SIZE"\n',
                "coreos-installer": '#!/bin/bash\nprintf "%s" "$*" > "$FIXTURE/installed"\n',
            }
            for name, content in commands.items():
                p = root / name; p.write_text(content, newline="\n"); p.chmod(0o755)
            # Replace only the block-node test with a fixture-file test.
            script = root / "install.sh"
            wrappers = ''.join(f'function {name} {{ /bin/bash "$FIXTURE/{name}" "$@"; }}\n' for name in commands)
            script.write_text('#!/bin/bash\n' + wrappers + B["INSTALL"].split('\n', 1)[1].replace('test -b "$disk"', 'test -f "$disk"'), newline="\n")
            cases = [
                ("empty-largest", f"{large} {size} disk 0 0\n{small} {size//2} disk 0 0\n", str(large), "", "", True),
                ("partitions", f"{large} {size} disk 0 0\n", str(large) + "\nchild", "", "", False),
                ("signature", f"{large} {size} disk 0 0\n", str(large), "", "gpt", False),
                ("raid-signature", f"{large} {size} disk 0 0\n", str(large), "", "linux_raid_member", False),
                ("swap", f"{large} {size} disk 0 0\n", str(large), "[SWAP]", "", False),
                ("mounted", f"{large} {size} disk 0 0\n", str(large), "/", "", False),
                ("opaque-leftover-data", f"{large} {size} disk 0 0\n", str(large), "", "", True),
                ("tied-largest", f"{large} {size} disk 0 0\n{small} {size} disk 0 0\n", str(large), "", "", False),
                ("out-of-range", f"{large} {size*4} disk 0 0\n", str(large), "", "", False),
                ("read-only", f"{large} {size} disk 1 0\n", str(large), "", "", False),
                ("removable", f"{large} {size} disk 0 1\n", str(large), "", "", False),
                ("occupied-largest-no-fallback", f"{large} {size} disk 0 0\n{small} {size-1} disk 0 0\n", str(large), "", "ext4", False),
            ]
            cases = [case + ('',) for case in cases]
            cases += [
                ("confirmed-partitions", f"{large} {size} disk 0 0\n", str(large) + "\nchild", "", "gpt", True, f"ERASE {large}\n"),
                ("confirmed-filesystem", f"{large} {size} disk 0 0\n", str(large), "", "ext4", True, f"ERASE {large}\n"),
                ("wrong-device", f"{large} {size} disk 0 0\n", str(large), "", "gpt", False, f"ERASE {small}\n"),
                ("active-mapped-device", f"{large} {size} disk 0 0\n", f"{large} disk\nchild crypt", "", "gpt", False, f"ERASE {large}\n"),
                ("confirmed-mounted-refused", f"{large} {size} disk 0 0\n", str(large), "/", "gpt", False, f"ERASE {large}\n"),
                ("confirmed-swap-refused", f"{large} {size} disk 0 0\n", str(large), "[SWAP]", "gpt", False, f"ERASE {large}\n"),
            ]
            for race in ('nodes', 'mounts', 'signatures'):
                cases.append(('race-' + race, f"{large} {size} disk 0 0\n", str(large), '', 'gpt', False, f"ERASE {large}\n"))
            for name, inventory, nodes, mounts, signatures, expected, stdin in cases:
                with self.subTest(name=name):
                    for file, value in [("inventory", inventory), ("nodes", nodes), ("mounts", mounts), ("signatures", signatures)]:
                        (root / file).write_text(value + "\n", newline="\n")
                    for fixture in ('queried', 'rechecked', 'race-nodes', 'race-mounts', 'race-signatures'):
                        (root / fixture).unlink(missing_ok=True)
                    if name.startswith('race-'):
                        (root / name).write_text({'race-nodes': f'{large} disk\nchild part\n', 'race-mounts': '/\n', 'race-signatures': 'ext4\n'}[name])
                    large.write_bytes(b'unrecognized leftover data' if name == 'opaque-leftover-data' else b'')
                    marker = root / "installed"; marker.unlink(missing_ok=True)
                    env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ["PATH"],
                               FIXTURE=str(root), DISK_SIZE=str(size))
                    result = subprocess.run(["bash", str(script)], env=env, input=stdin, text=True, capture_output=True)
                    self.assertEqual(result.returncode == 0, expected, result.stdout + result.stderr)
                    self.assertEqual(marker.exists(), expected)
                    self.assertFalse((root / 'full-disk-scan').exists(), 'Installer must not scan the entire disk')
                    if expected: self.assertIn(str(large), marker.read_text())

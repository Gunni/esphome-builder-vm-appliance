"""Exercise generic first-boot prompts using mocked network tools, never real network/disk."""
import base64
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import unittest

B = runpy.run_path(str(Path(__file__).resolve().parents[1] / "build-installer.py"))


@unittest.skipUnless(os.name == "posix" and shutil.which("bash") and shutil.which("jq"), "Needs POSIX bash and jq")
class ProvisionTests(unittest.TestCase):
    def test_prompts_key_import_and_refusals(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); runtime = root / "runtime"; runtime.mkdir()
            templates = root / "templates"; templates.mkdir()
            (templates / 'template.ign').write_text(json.dumps(B['configs']('@SSH_KEY@', '@USERNAME@')))
            script = root / 'provision.sh'
            wrappers = '''
function curl {
    touch "$FIXTURE/curl-called"
    [ "$CURL_RESULT" = 0 ] || return "$CURL_RESULT"
    cp "$FIXTURE/fetched.keys" "${@: -1}"
}
function ssh-keygen { echo "Unexpected key validation" >&2; return 99; }
'''
            content = B['PROVISION'].replace('/run/esphome', str(runtime)).replace('/etc/esphome-appliance', str(templates))
            script.write_text('#!/bin/bash\n' + wrappers + content.split('\n', 1)[1], newline='\n')
            keys = 'ssh-ed25519 TEST_ONLY_KEY_ONE\nssh-ed25519 TEST_ONLY_KEY_TWO\n'
            cases = [
                ('skip-ssh-offline', 'builder\n\n', '', '22', True),
                ('all-keys', 'builder\noctocat\n', keys, '0', True),
                ('no-keys', 'builder\noctocat\n', '', '0', False),
                ('download-fails', 'builder\noctocat\n', keys, '22', False),
                ('unvalidated-response', 'builder\noctocat\n', 'opaque public-key response\n', '0', True),
                ('reserved-builder-user', 'esphome\noctocat\n', keys, '0', False),
                ('bad-user', 'root\noctocat\n', keys, '0', False),
                ('bad-github', 'builder\nwrong/name\n', keys, '0', False),
            ]
            cases = [case + ('', '') for case in cases]
            cases += [
                ('skip-ssh-with-username-default', '\n', '', '22', True, 'builder', ''),
                ('username-default', 'octocat\n', keys, '0', True, 'builder', ''),
                ('key-default', 'builder\n', '', '22', True, '', keys.splitlines()[0]),
                ('both-defaults', '', '', '22', True, 'builder', keys.splitlines()[0]),
                ('literal-key-default', '', '', '22', True, 'builder',
                 'ssh-ed25519 OPAQUE $(touch SHOULD_NOT_EXIST) `echo literal`'),
                ('invalid-default-user', '', keys, '0', False, 'root', ''),
                ('no-final-newline', 'builder\noctocat\n', keys.rstrip('\n'), '0', True, '', ''),
            ]
            for name, stdin, data, curl_result, expected, username, ssh_key in cases:
                with self.subTest(name=name):
                    (root / 'fetched.keys').write_text(data, newline='\n')
                    (templates / 'account-defaults.json').write_text(json.dumps(dict(username=username, ssh_key=ssh_key)))
                    output = runtime / 'installed.ign'; output.unlink(missing_ok=True)
                    (root / 'curl-called').unlink(missing_ok=True)
                    env = dict(os.environ, FIXTURE=str(root), CURL_RESULT=curl_result)
                    result = subprocess.run(['bash', str(script)], input=stdin, env=env, capture_output=True, text=True)
                    self.assertEqual(result.returncode == 0, expected, result.stdout + result.stderr)
                    self.assertEqual(output.exists(), expected)
                    if expected:
                        dest = json.loads(output.read_text())
                        user = dest['passwd']['users'][1]
                        self.assertEqual(user['name'], 'builder')
                        service_user = dest['passwd']['users'][2]
                        self.assertEqual(service_user['name'], 'esphome')
                        self.assertEqual(service_user['uid'], 2000)
                        self.assertNotIn('sshAuthorizedKeys', service_user)
                        self.assertNotIn('wheel', service_user.get('groups', []))
                        installed_units = {u['name']: u for u in dest['systemd']['units']}
                        for getty in ('getty@.service', 'serial-getty@.service'):
                            credential = installed_units[getty]['dropins'][0]['contents']
                            self.assertIn('--autologin builder ', credential)
                            self.assertNotIn('@USERNAME@', credential)
                        self.assertEqual(user['sshAuthorizedKeys'], (ssh_key or data).splitlines())
                        if name.startswith('skip-ssh'):
                            self.assertEqual(user['sshAuthorizedKeys'], [])
                            self.assertFalse((root / 'curl-called').exists())
                        self.assertEqual('Linux system username:' in result.stdout, not bool(username))
                        self.assertEqual('GitHub username (' in result.stdout, not bool(ssh_key))
                        motd = next(f for f in dest['storage']['files'] if f['path'] == '/etc/motd.d/20-esphome-appliance')
                        text = base64.b64decode(motd['contents']['source'].split(',', 1)[1]).decode()
                        self.assertIn('Management user: builder', text)
                        self.assertNotIn('@USERNAME@', json.dumps(dest))

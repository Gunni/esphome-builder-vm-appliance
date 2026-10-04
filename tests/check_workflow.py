"""Exercise the actual workflow detection shell with mocked Fedora/GitHub calls."""
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap

root = Path(__file__).resolve().parents[1]
# Extract the detection step's literal shell block; no YAML dependency is needed.
for path in (root / '.github/workflows').glob('*.yml'):
    for reference in re.findall(r'^\s*(?:-\s*)?uses:\s*(\S+)', path.read_text(), re.MULTILINE):
        assert reference.startswith('./') or re.fullmatch(r'actions/[a-z-]+@[0-9a-f]{40}', reference), (path, reference)
print('PASS: external actions are pinned to full commit SHAs')
workflow = (root / '.github/workflows/release.yml').read_text()
script = textwrap.dedent(workflow.split('        id: detect\n', 1)[1].split('        run: |\n', 1)[1].split('\n  tests:', 1)[0])
selection = {'release': '44.20260913.3.2', 'location': 'https://builds.coreos.fedoraproject.org/base.iso', 'sha256': 'a' * 64}
iso_name = 'esphome-appliance-fcos-44.20260913.3.2-aaaaaaaaaaaa.iso'
asset_prefix = iso_name[:-4]
assets = [asset_prefix + '-setup.zip', asset_prefix + '-fedora-base.json', asset_prefix + '-SHA256SUMS', iso_name, iso_name + '.sha256']
complete = {'targetCommitish': 'a' * 40, 'isDraft': False, 'assets': [{'name': name} for name in assets]}
cases = [
    ('new-fedora', None, 'schedule', 'branch', True, True),
    ('published', complete, 'schedule', 'branch', False, False),
    ('draft', {**complete, 'isDraft': True}, 'schedule', 'branch', True, True),
    ('incomplete-published', {**complete, 'assets': []}, 'schedule', 'branch', False, False),
    ('missing-iso-published', {**complete, 'assets': complete['assets'][:4]}, 'schedule', 'branch', False, False),
    ('manual', complete, 'workflow_dispatch', 'branch', False, False),
    ('project-tag', None, 'push', 'tag', True, True),
    ('published-project-tag', complete, 'push', 'tag', False, False),
    ('draft-project-tag', {**complete, 'isDraft': True}, 'push', 'tag', True, True),
    ('incomplete-draft', {**complete, 'isDraft': True, 'assets': []}, 'schedule', 'branch', True, True),
    ('main-push', None, 'push', 'branch', True, False),
    ('feature-push', None, 'push', 'branch', True, False),
    ('off-main-tag', None, 'push', 'tag', True, False),
    ('main-push-after-release', complete, 'push', 'branch', True, False),
    ('no-project-tags', None, 'schedule', 'branch', False, False),
]
with tempfile.TemporaryDirectory(prefix='detect-') as tmp:
    work = Path(tmp)
    (work / 'download-coreos.py').write_text("import json; from pathlib import Path; Path('.ci').mkdir(exist_ok=True); Path('.ci/selection.json').write_text(json.dumps(" + repr(selection) + "))")
    tools = work / 'bin'
    tools.mkdir()
    (tools / 'python').symlink_to(sys.executable)
    gh = tools / 'gh'
    gh.write_text('#!' + sys.executable + '\n' + "import os, sys\nfrom pathlib import Path\nassert sys.argv[1] == 'release', sys.argv\np = Path(os.environ['RELEASE_FIXTURE'])\nif not p.exists(): sys.exit(1)\nprint(p.read_text())\n")
    gh.chmod(0o755)
    # Real Git objects/ancestry: the highest overall tag is on an unmerged branch.
    repo = work / 'git-fixture'
    git_env = {**os.environ, 'GIT_DIR': str(repo), 'GIT_AUTHOR_NAME': 'Fixture', 'GIT_AUTHOR_EMAIL': 'fixture@example.invalid', 'GIT_COMMITTER_NAME': 'Fixture', 'GIT_COMMITTER_EMAIL': 'fixture@example.invalid'}
    def git(*args, input=''):
        return subprocess.check_output(['git', *args], env=git_env, input=input, text=True).strip()
    git('init', '--bare', '--quiet', str(repo))
    tree = git('mktree')
    old_source = git('commit-tree', tree, input='old main')
    tagged_source = git('commit-tree', tree, '-p', old_source, input='tagged main')
    main_source = git('commit-tree', tree, '-p', tagged_source, input='current main')
    feature_source = git('commit-tree', tree, '-p', old_source, input='unmerged feature')
    git('update-ref', 'refs/remotes/origin/main', main_source)
    git('update-ref', 'refs/tags/v1.9.0', old_source)
    git('update-ref', 'refs/tags/v1.10.0', tagged_source)
    git('update-ref', 'refs/tags/v9.0.0', feature_source)
    for name, existing, event, ref_type, build, publish in cases:
        fixture = work / 'release.json'
        fixture.unlink(missing_ok=True)
        if existing is not None:
            fixture.write_text(json.dumps({**existing, 'targetCommitish': tagged_source}))
        output = work / 'output'
        output.unlink(missing_ok=True)
        source_commit = feature_source if name in ('feature-push', 'off-main-tag') else (tagged_source if ref_type == 'tag' else main_source)
        if name == 'no-project-tags':
            git('update-ref', '-d', 'refs/tags/v1.9.0')
            git('update-ref', '-d', 'refs/tags/v1.10.0')
        env = {**git_env, 'PATH': str(tools) + ':' + os.environ['PATH'], 'GH_REPO': 'test/repo', 'SOURCE_COMMIT': source_commit, 'EVENT_NAME': event, 'REF_TYPE': ref_type, 'REF_NAME': 'v1.0.0' if ref_type == 'tag' else 'feature/test' if name == 'feature-push' else 'main', 'GITHUB_OUTPUT': str(output), 'RELEASE_FIXTURE': str(fixture)}
        result = subprocess.run(['bash', '--noprofile', '--norc', '-e', '-o', 'pipefail', '-c', script], cwd=work, env=env, capture_output=True, text=True)
        assert result.returncode == 0, (name, result.stderr)
        values = dict(line.split('=', 1) for line in output.read_text().splitlines())
        assert values['build'] == str(build).lower(), (name, values)
        assert values['publish'] == str(publish).lower(), (name, values)
        if name != 'no-project-tags':
            expected_source = source_commit if event == 'push' else tagged_source
            assert values['source'] == expected_source, (name, values)
            assert json.loads(values['selection']) == selection
            if ref_type == 'tag' and name != 'off-main-tag':
                assert values['tag'] == 'v1.0.0'
            elif event != 'push':
                assert values['tag'] == 'fcos-44.20260913.3.2'
            else:
                assert 'tag' not in values
        print('PASS:', name, 'build=' + values['build'], 'publish=' + values['publish'])

# Execute the real publication block against a stateful mock of immutable releases.
publication = textwrap.dedent(workflow.split('      - name: Publish GitHub Release assets\n', 1)[1].split('        run: |\n', 1)[1])
with tempfile.TemporaryDirectory(prefix='publish-') as tmp:
    work = Path(tmp)
    output = work / 'build/release'
    output.mkdir(parents=True)
    for name in assets:
        (output / name).write_text('fixture for ' + name)
    tools = work / 'bin'
    tools.mkdir()
    (tools / 'python').symlink_to(sys.executable)
    gh = tools / 'gh'
    gh.write_text('#!' + sys.executable + '\n' + '''import json, os, sys
from pathlib import Path
state = Path(os.environ['RELEASE_STATE'])
log = Path(os.environ['RELEASE_CALLS'])
command = sys.argv[2]
with log.open('a') as fp: fp.write(command + '\\n')
if command == 'view':
    if not state.exists(): sys.exit(1)
    print(state.read_text())
elif command in ('create', 'upload'):
    if command == 'create':
        assert '--draft' in sys.argv, sys.argv
        assert not state.exists()
    else:
        assert json.loads(state.read_text())['isDraft'], 'Cannot upload to an immutable release'
    files = [Path(arg) for arg in sys.argv if arg.startswith('build/release/')]
    entries = [{'name': p.name, 'size': p.stat().st_size} for p in files]
    if os.environ['CASE'] == 'incomplete-upload': entries.pop()
    state.write_text(json.dumps({'isDraft': True, 'assets': entries}))
elif command == 'delete-asset':
    r = json.loads(state.read_text())
    assert r['isDraft'], 'Cannot delete assets from an immutable release'
    r['assets'] = [a for a in r['assets'] if a['name'] != sys.argv[4]]
    state.write_text(json.dumps(r))
elif command == 'edit':
    r = json.loads(state.read_text())
    assert r['isDraft'], 'Cannot edit an immutable release'
    assert '--draft=false' in sys.argv
    r['isDraft'] = False
    state.write_text(json.dumps(r))
else:
    raise AssertionError(sys.argv)
''')
    gh.chmod(0o755)
    for case in ['new-release', 'retry-draft', 'legacy-draft', 'already-published', 'incomplete-upload']:
        state = work / 'state.json'
        calls = work / 'calls.log'
        state.unlink(missing_ok=True)
        calls.unlink(missing_ok=True)
        if case in ['retry-draft', 'legacy-draft', 'already-published']:
            state.write_text(json.dumps({'isDraft': case != 'already-published', 'assets': [{'name': 'esphome-appliance.ign', 'size': 12}, {'name': 'SHA256SUMS', 'size': 12}] if case == 'legacy-draft' else []}))
        env = {**os.environ, 'PATH': str(tools) + ':' + os.environ['PATH'], 'CASE': case, 'RELEASE_STATE': str(state), 'RELEASE_CALLS': str(calls), 'ISO_NAME': iso_name, 'ASSET_PREFIX': asset_prefix, 'RELEASE_TAG': 'v1.0.0', 'RELEASE_COMMIT': 'a' * 40, 'FEDORA_SELECTION': json.dumps(selection)}
        result = subprocess.run(['bash', '--noprofile', '--norc', '-e', '-o', 'pipefail', '-c', publication], cwd=work, env=env, capture_output=True, text=True)
        operations = calls.read_text().splitlines()
        if case == 'incomplete-upload':
            assert result.returncode != 0 and 'edit' not in operations, (case, operations, result.stderr)
            assert json.loads(state.read_text())['isDraft']
        else:
            assert result.returncode == 0, (case, result.stderr)
            if case == 'already-published':
                assert operations == ['view'], operations
            else:
                expected_ops = ['view'] + (['delete-asset', 'delete-asset'] if case == 'legacy-draft' else []) + ['create' if case == 'new-release' else 'upload', 'view', 'edit']
                assert operations == expected_ops, operations
                assert not json.loads(state.read_text())['isDraft']
        print('PASS:', case, 'published assets remain immutable')

# The actual naming step must use the built revision, including Fedora tag builds.
naming = textwrap.dedent(workflow.split('      - name: Read pinned Fedora checksum and ISO filename\n', 1)[1].split('        run: |\n', 1)[1].split('      - name:', 1)[0])
with tempfile.TemporaryDirectory(prefix='iso-name-') as tmp:
    work = Path(tmp)
    (work / '.ci').mkdir()
    (work / '.ci/selection.json').write_text(json.dumps(selection))
    output = work / 'build/release'
    output.mkdir(parents=True)
    subprocess.run([sys.executable, str(root / 'build-release.py'), str(output), '--fedora-selection', str(work / '.ci/selection.json')], check=True, capture_output=True)
    import hashlib
    import zipfile
    tools = work / 'bin'
    tools.mkdir()
    (tools / 'python').symlink_to(sys.executable)
    env = {**os.environ, 'PATH': str(tools) + ':' + os.environ['PATH'], 'SOURCE_COMMIT': '9701a46d0556f44996ae22a0a45fc2dc6a0bf48b', 'GITHUB_SHA': 'b' * 40, 'GITHUB_ENV': str(work / 'env'), 'GITHUB_OUTPUT': str(work / 'output')}
    subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', naming], cwd=work, env=env, check=True)
    assert (work / 'env').read_text() == 'ASSET_PREFIX=esphome-appliance-fcos-44.20260913.3.2-9701a46d0556\nISO_NAME=esphome-appliance-fcos-44.20260913.3.2-9701a46d0556.iso\n'
    assert (work / 'output').read_text() == 'sha256=' + selection['sha256'] + '\n'
    print('PASS: ISO filename uses selected Fedora version and actual source revision')
    prefix = 'esphome-appliance-fcos-44.20260913.3.2-9701a46d0556'
    assert {p.name for p in output.iterdir()} == {prefix + '-setup.zip', prefix + '-fedora-base.json', prefix + '-SHA256SUMS'}
    for line in (output / (prefix + '-SHA256SUMS')).read_text().splitlines():
        digest, name = line.split('  ', 1)
        assert digest == hashlib.sha256((output / name).read_bytes()).hexdigest()
    print('PASS: all package filenames and checksum entries identify Fedora/source')
    with zipfile.ZipFile(output / (prefix + '-setup.zip')) as archive:
        ignition = json.loads(archive.read('esphome-appliance/esphome-appliance.ign'))
        assert ignition['ignition']['version']
        assert json.loads(archive.read('esphome-appliance/fedora-base.json')) == selection
    assert not list(output.glob('*.ign'))
    print('PASS: Ignition stays in the ZIP only; manifest covers every external package asset')

# Scheduled builds use the latest project tag, which may predate visible filenames.
fetch_script = textwrap.dedent(workflow.split('      - name: Download or verify pinned Fedora ISO\n        run: |\n', 1)[1].split('      - name:', 1)[0])
with tempfile.TemporaryDirectory() as tmp:
    for downloader in ('download-coreos.py', '.download-coreos.py'):
        work = Path(tmp) / downloader.replace('.', '_')
        work.mkdir()
        (work / downloader).write_text('import sys\nassert sys.argv[1:] == ["fetch", ".ci"]\nprint("downloaded")\n')
        result = subprocess.run(['bash', '-e', '-c', fetch_script], cwd=work, capture_output=True, text=True, check=True)
        assert result.stdout.strip() == 'downloaded'
print('PASS: Fedora fetch supports current filenames and historical project tags')

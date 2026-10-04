"""Build a small, credential-free release package without downloading Fedora."""
import argparse
import hashlib
import json
from pathlib import Path
import runpy
import zipfile

HERE = Path(__file__).resolve().parent
# Explicit allowlist: never package .env, keys, Git data or generated VM files.
SOURCE_FILES = (
    'README.md', 'LICENSE',
    'build-installer.py', 'build-release.py', 'download-coreos.py', '.env.example',
    'esphome-completion.bash', 'esphome-builder.container', 'esphome-tmpfiles.conf', 'motd.txt',
    '.github/workflows/test.yml', '.github/workflows/build-iso.yml',
    '.github/workflows/release.yml',
    'tests/test_builder.py', 'tests/test_download.py',
    'tests/test_disk_selection.py', 'tests/test_provision.py', 'tests/test_release.py',
    'tests/check_workflow.py',
)


def build_release(output, fedora_selection=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    names = ['esphome-appliance.ign', 'esphome-appliance-setup.zip']
    metadata = {}
    if fedora_selection is not None:
        selection = json.loads(Path(fedora_selection).read_text(encoding='utf-8'))
        downloader = runpy.run_path(str(HERE / 'download-coreos.py'))
        downloader['validate_selection'](selection)
        metadata['fedora-base.json'] = (json.dumps(selection, indent=2) + '\n').encode()
        names.append('fedora-base.json')
    paths = [output / name for name in names + ['SHA256SUMS']]
    if any(path.exists() for path in paths):
        raise ValueError('Release output already exists; choose a new directory')
    sources = {name: (HERE / name).read_bytes() for name in SOURCE_FILES}
    builder = runpy.run_path(str(HERE / 'build-installer.py'))
    # No settings file is read. Release payloads always use generic defaults.
    config = (json.dumps(builder['generic_config'](), indent=2) + '\n').encode()
    created = []
    try:
        with paths[0].open('xb') as fp:
            created.append(paths[0])
            fp.write(config)
        for name, data in metadata.items():
            path = output / name
            with path.open('xb') as fp:
                created.append(path)
                fp.write(data)
        with paths[1].open('xb') as fp:
            created.append(paths[1])
            with zipfile.ZipFile(fp, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                for name, data in sorted({**sources, **metadata, 'esphome-appliance.ign': config}.items()):
                    entry = zipfile.ZipInfo('esphome-appliance/' + name, (1980, 1, 1, 0, 0, 0))
                    entry.create_system = 3
                    entry.external_attr = 0o100644 << 16
                    entry.compress_type = zipfile.ZIP_DEFLATED
                    archive.writestr(entry, data)
        checksums = ''.join(hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + path.name + '\n'
                            for path in paths[:-1])
        with paths[-1].open('x', encoding='utf-8', newline='\n') as fp:
            created.append(paths[-1])
            fp.write(checksums)
    except BaseException:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--fedora-selection', type=Path, help='Validated Fedora stream selection to include with the release')
    args = parser.parse_args()
    try:
        paths = build_release(args.output, args.fedora_selection)
    except (OSError, ValueError) as error:
        parser.exit(1, f'Packaging failed: {error}\n')
    for path in paths:
        print(f'{path}: {path.stat().st_size} bytes')


if __name__ == '__main__':
    main()

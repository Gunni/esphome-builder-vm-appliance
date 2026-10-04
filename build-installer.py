"""Build a CoreOS appliance ISO using its documented Ignition embed area.
Run with Python 3.11+: build-installer.py BASE_ISO OUTPUT_ISO [--env SETTINGS].
Only the reserved Ignition area is modified; original stays intact.
"""
import argparse
import base64
import hashlib
import json
import lzma
from pathlib import Path
import re
import struct
from textwrap import dedent

HERE = Path(__file__).resolve().parent

def require(condition, message):
    if not condition:
        raise ValueError(message)


def file(path, content, mode=0o644, overwrite=False):
    result = {'path': path, 'mode': mode, 'contents': {'source': 'data:;base64,' + base64.b64encode(content.encode()).decode()}}
    if overwrite:
        result['overwrite'] = True
    return result

def unit(name, content):
    return {'name': name, 'enabled': True, 'contents': content}

DEFAULT_NTP_POOLS = tuple(f"{number}.pool.ntp.org" for number in (2, 0, 1, 3))

CHRONY = ("# Global NTP pools. Chrony selects sources based on measured quality.\n"
          + "".join(f"pool {pool} iburst\n" for pool in DEFAULT_NTP_POOLS)
          + dedent(r"""
    driftfile /var/lib/chrony/drift
    makestep 1.0 3
    rtcsync
    logdir /var/log/chrony
""").lstrip("\n"))

ESPHOME_SHELL = dedent(r"""
    #!/usr/bin/bash
    set -euo pipefail
    if [ "$(id -un)" != esphome ]; then
        exec /usr/bin/run0 -u esphome --setenv=XDG_RUNTIME_DIR=/run/user/2000 /usr/local/bin/esphome-shell
    fi
    exec /usr/bin/podman exec --interactive --tty --workdir /config esphome-builder /bin/bash -c '
        while IFS= read -r -d "" setting; do
            export "$setting"
        done < /proc/1/environ
        exec /bin/bash --noprofile --norc
    '
""").lstrip("\n")

DISABLE_AUTOLOGIN = dedent(r"""
    #!/usr/bin/bash
    set -euo pipefail
    /usr/bin/rm -f -- /etc/systemd/system/{getty@,serial-getty@}.service.d/autologin.conf
    /usr/bin/systemctl daemon-reload
    /usr/bin/rm -f -- /usr/local/bin/esphome-disable-autologin
""").lstrip("\n")

# Thin wrappers keep the correct journal filters and user-manager scope in one place.
ESPHOME_COMMANDS = {
    'esphome-logs': '/usr/bin/run0 /usr/bin/journalctl _UID=2000 _SYSTEMD_USER_UNIT=esphome-builder.service',
    **{f'esphome-{verb}': '/usr/bin/run0 -u esphome --setenv=XDG_RUNTIME_DIR=/run/user/2000 /usr/bin/systemctl --user ' + verb + ' esphome-builder.service'
       for verb in ('start', 'stop', 'restart', 'status')},
    'esphome-update': '/usr/bin/run0 -u esphome --setenv=XDG_RUNTIME_DIR=/run/user/2000 /usr/bin/systemctl --user start podman-auto-update.service',
    'esphome-timers': '/usr/bin/run0 -u esphome --setenv=XDG_RUNTIME_DIR=/run/user/2000 /usr/bin/systemctl --user list-timers podman-auto-update.timer esphome-image-clean.timer',
}

INSTALL = dedent(r"""
    #!/usr/bin/bash
    set -euo pipefail
    refuse() { echo "Refusing installation: $*" >&2; exit 1; }
    # Pick the unique largest writable, non-removable whole disk.
    # Never fall back to a smaller disk if the largest is occupied.
    disk= bytes=0 ties=0
    inventory=$(lsblk --bytes --nodeps --noheadings --raw --paths --output NAME,SIZE,TYPE,RO,RM)
    while read -r candidate size type readonly removable; do
        [ "$type" = disk ] && [ "$readonly" = 0 ] && [ "$removable" = 0 ] || continue
        [[ "$size" =~ ^[0-9]+$ ]] || refuse 'invalid disk size from lsblk'
        if (( size > bytes )); then
            disk=$candidate; bytes=$size; ties=1
        elif (( size == bytes )); then
            (( ties += 1 ))
        fi
    done <<< "$inventory"
    [ -n "$disk" ] || refuse 'no writable non-removable disk found'
    (( ties == 1 )) || refuse 'multiple disks share the largest size'
    test -b "$disk" || refuse 'selected device is not a block device'
    if (( bytes < 50 * 1024 * 1024 * 1024 || bytes > 80 * 1024 * 1024 * 1024 )); then
        refuse "$disk is outside expected 50-80 GiB range"
    fi
    nodes=$(lsblk --noheadings --raw --paths --output NAME "$disk")
    [ "$nodes" = "$disk" ] || refuse "$disk has partitions or mapped child devices"
    mounts=$(lsblk --noheadings --raw --output MOUNTPOINTS "$disk")
    [[ "$mounts" =~ ^[[:space:]]*$ ]] || refuse "$disk is mounted or used as swap"
    signatures=$(wipefs --no-act --noheadings --output TYPE "$disk")
    [ -z "$signatures" ] || refuse "$disk has a filesystem, partition-table or RAID signature"
    # Recheck topology/signatures immediately before the destructive operation.
    nodes=$(lsblk --noheadings --raw --paths --output NAME "$disk")
    signatures=$(wipefs --no-act --noheadings --output TYPE "$disk")
    mounts=$(lsblk --noheadings --raw --output MOUNTPOINTS "$disk")
    current_bytes=$(blockdev --getsize64 "$disk")
    [ "$nodes" = "$disk" ] || refuse 'disk topology changed'
    [ -z "$signatures" ] || refuse 'disk signatures changed'
    [[ "$mounts" =~ ^[[:space:]]*$ ]] || refuse 'disk became mounted'
    [ "$current_bytes" = "$bytes" ] || refuse 'disk size changed'
    echo "Disk checks passed: $disk ($bytes bytes). Installing Fedora CoreOS."
    coreos-installer install "$disk" --offline --ignition-file /run/esphome/installed.ign
    echo 'Installation complete. Eject installer ISO; systemd will reboot.'
""").lstrip("\n")

def configs(ssh_key, username, ntp_pools=None):
    chrony = CHRONY
    if ntp_pools:
        chrony = '\n'.join('pool ' + pool + ' iburst' for pool in ntp_pools) + '\n' + CHRONY[CHRONY.index('driftfile'):]

    dest = {
        'ignition': {'version': '3.4.0'},
        'passwd': {'users': [
            {'name': 'core', 'shouldExist': False},
            {'name': username, 'groups': ['wheel'], 'shell': '/bin/bash', 'sshAuthorizedKeys': [ssh_key]},
            {'name': 'esphome', 'uid': 2000, 'homeDir': '/var/home/esphome',
             'shell': '/usr/sbin/nologin', 'system': True},
        ]},
        'storage': {'links': [
            {'path': '/etc/localtime', 'target': '/usr/share/zoneinfo/UTC', 'overwrite': True},
        ] + [
            {'path': '/var/home/esphome/.config/systemd/user/timers.target.wants/' + name,
             'target': target}
            for name, target in (
                ('podman-auto-update.timer', '/usr/lib/systemd/user/podman-auto-update.timer'),
                ('esphome-image-clean.timer', '../esphome-image-clean.timer'))
        ], 'files': [
            file('/etc/esphome-appliance-release.pending', 'NAME="ESPHome appliance"\nID=esphome-appliance\nVARIANT="Remote builder"\nVARIANT_ID=remote-builder\n'),
            {'path': '/etc/subuid', 'append': [{'source': file('', 'esphome:524288:65536\n')['contents']['source']}]},
            {'path': '/etc/subgid', 'append': [{'source': file('', 'esphome:524288:65536\n')['contents']['source']}]},
            file('/etc/hostname', 'esphome-builder-????????????\n', overwrite=True),
            file('/etc/vconsole.conf', 'KEYMAP=us\n', overwrite=True),
            file('/etc/chrony.conf', chrony, overwrite=True),
            file('/etc/motd.d/20-esphome-appliance', (HERE / 'motd.txt').read_text(encoding='utf-8').replace('@USERNAME@', username).replace('@NTP_POOLS@', ' '.join(ntp_pools or DEFAULT_NTP_POOLS))),
            file('/etc/bash_completion.d/esphome-appliance', (HERE / 'esphome-completion.bash').read_text(encoding='utf-8')),
            file('/usr/local/bin/esphome-shell', ESPHOME_SHELL, mode=0o755),
            file('/usr/local/bin/esphome-disable-autologin', DISABLE_AUTOLOGIN, mode=0o755),
            *[file('/usr/local/bin/' + name, '#!/usr/bin/bash\nexec ' + command + ' "$@"\n', mode=0o755)
              for name, command in ESPHOME_COMMANDS.items()],
            file('/etc/containers/systemd/users/2000/esphome-builder.container', (HERE / 'esphome-builder.container').read_text(encoding='utf-8')),
            file('/var/home/esphome/.config/systemd/user/esphome-image-clean.service', dedent(r"""
                [Unit]
                Description=Remove unused dangling container images
                After=podman-auto-update.service
                [Service]
                Type=oneshot
                ExecStart=/usr/bin/podman image prune --force
            """).lstrip("\n")),
            file('/var/home/esphome/.config/systemd/user/esphome-image-clean.timer', dedent(r"""
                [Unit]
                Description=Weekly unused container image cleanup
                [Timer]
                OnCalendar=Sun *-*-* 05:30:00 UTC
                Persistent=true
                [Install]
                WantedBy=timers.target
            """).lstrip("\n")),
            file('/var/home/esphome/.config/systemd/user/podman-auto-update.timer.d/10-appliance-schedule.conf', dedent(r"""
                [Unit]
                Wants=esphome-builder.service
                After=esphome-builder.service
                [Timer]
                OnCalendar=
                OnCalendar=*-*-* 03:00:00 UTC
                OnActiveSec=3min
                RandomizedDelaySec=0
                Persistent=true
            """).lstrip("\n")),
            file('/etc/tmpfiles.d/esphome-appliance.conf', (HERE / 'esphome-tmpfiles.conf').read_text(encoding='utf-8')),
            file('/etc/systemd/journald.conf.d/50-appliance-limits.conf', '[Journal]\nSystemMaxUse=256M\nRuntimeMaxUse=64M\nMaxRetentionSec=14day\n'),
            file('/etc/ssh/sshd_config.d/20-key-only.conf', 'PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin no\n'),
            file('/etc/polkit-1/rules.d/10-esphome-run0.rules', dedent(r"""
                // Admin access from both console and SSH sessions.
                polkit.addRule(function(action, subject) {
                    if (action.id === "org.freedesktop.systemd1.manage-units" &&
                        subject.isInGroup("wheel")) {
                        return polkit.Result.YES;
                    }
                });
            """).lstrip("\n")),
            file('/etc/zincati/config.d/55-updates.toml', dedent(r"""
                [updates]
                enabled = true
                strategy = "periodic"
                [updates.periodic]
                time_zone = "UTC"
                [[updates.periodic.window]]
                days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
                start_time = "04:00"
                length_minutes = 60
            """).lstrip("\n")),
        ]},
        'systemd': {'units': [
            unit('esphome-tools.service', dedent(r"""
                [Unit]
                Description=Update appliance OS, layer tools and prepare ESPHome
                Wants=network-online.target getty-pre.target
                Requires=user-runtime-dir@2000.service
                After=network-online.target systemd-tmpfiles-setup.service user-runtime-dir@2000.service
                Before=multi-user.target getty-pre.target sshd.service zincati.service
                ConditionPathExists=!/etc/esphome-appliance-release
                SuccessAction=reboot
                [Service]
                Type=oneshot
                ExecStartPre=/usr/bin/busctl call org.freedesktop.systemd1 /org/freedesktop/systemd1 org.freedesktop.systemd1.Manager SetShowStatus s no
                ExecStart=-/usr/bin/rpm-ostree upgrade --bypass-driver
                ExecStart=/usr/bin/rpm-ostree install --idempotent --allow-inactive binutils bash-completion htop btop vim-enhanced tmux jq bind-utils tcpdump ethtool traceroute
                ExecStart=/usr/bin/systemd-run --unit=esphome-image-pull --wait --pipe --collect --uid=esphome --setenv=HOME=/var/home/esphome --setenv=XDG_RUNTIME_DIR=/run/user/2000 /usr/bin/podman pull ghcr.io/esphome/esphome:stable
                ExecStopPost=/usr/bin/busctl call org.freedesktop.systemd1 /org/freedesktop/systemd1 org.freedesktop.systemd1.Manager SetShowStatus s ""
                StandardOutput=journal+console
                StandardError=journal+console
                ExecStartPost=/usr/bin/mv -T /etc/esphome-appliance-release.pending /etc/esphome-appliance-release
                TimeoutStartSec=0
                Restart=on-failure
                RestartMode=direct
                RestartSec=60
                [Install]
                WantedBy=multi-user.target
            """).lstrip("\n")),
        ] + [
            {'name': 'user@2000.service', 'dropins': [{'name': '10-appliance-setup.conf',
                'contents': '[Unit]\nRequires=esphome-tools.service\nAfter=esphome-tools.service\n'}]}
        ] + [
            # Explicit agetty autologin avoids dependence on credential delivery.
            {'name': name, 'dropins': [{'name': 'autologin.conf',
                'contents': '[Service]\nExecStart=\nExecStart=-/usr/bin/agetty --autologin ' + username
                            + ' --noclear ' + options + '- ${TERM}\n'}]}
            for name, options in (('getty@.service', ''),
                                  ('serial-getty@.service', '--keep-baud 115200,57600,38400,9600 '))
        ] + [
            # Local passwd accounts, local disks and Podman need none of these.
            {'name': 'systemd-homed.service', 'enabled': False, 'mask': True},
            {'name': 'systemd-homed-activate.service', 'enabled': False, 'mask': True},
            {'name': 'gssproxy.service', 'enabled': False, 'mask': True},
            {'name': 'fwupd-refresh.timer', 'enabled': False},
            {'name': 'raid-check.timer', 'enabled': False},
            {'name': 'docker.socket', 'enabled': False},
            {'name': 'chronyd.service', 'enabled': True},
            {'name': 'systemd-tmpfiles-clean.timer', 'enabled': True},
        ]}
    }
    return dest


PROVISION = dedent(r"""
    #!/usr/bin/bash
    set -euo pipefail
    refuse() { echo "Provisioning stopped: $*" >&2; exit 1; }
    echo 'ESPHome appliance setup'
    echo 'No disk will be written until the account settings and public keys are ready.'
    defaults=/etc/esphome-appliance/account-defaults.json
    username=$(jq -r '.username' "$defaults")
    umask 077
    if [ -z "$username" ]; then
        printf 'Linux system username: '
        read -r username
    fi
    [[ "$username" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || refuse 'invalid Linux username'
    [[ "$username" != root && "$username" != core && "$username" != esphome ]] || refuse 'choose a username other than root, core or esphome'
    jq -j '.ssh_key' "$defaults" > /run/esphome/github.keys
    if [ ! -s /run/esphome/github.keys ]; then
        printf 'GitHub username (all SSH public keys; Enter to skip SSH setup): '
        read -r github_username
        if [ -n "$github_username" ]; then
            [[ "$github_username" =~ ^[a-zA-Z0-9]([a-zA-Z0-9-]{0,37}[a-zA-Z0-9])?$ ]] || refuse 'invalid GitHub username'
            [[ "$github_username" != *--* ]] || refuse 'invalid GitHub username'
            echo "Downloading SSH public keys for $github_username..."
            curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
                --connect-timeout 15 --max-time 60 --retry 3 \
                "https://github.com/$github_username.keys" --output /run/esphome/github.keys
            [ -s /run/esphome/github.keys ] || refuse 'this GitHub account has no SSH public keys'
        else
            echo 'Skipping SSH keys. Use console autologin to access the appliance.'
        fi
    else
        echo 'Using public SSH key supplied at build time.'
    fi
    template=/etc/esphome-appliance/template.ign
    motd=$(jq -r '.storage.files[] | select(.path == "/etc/motd.d/20-esphome-appliance") | .contents.source' "$template")
    motd=$(printf '%s' "${motd#data:;base64,}" | base64 -d | sed "s/@USERNAME@/$username/g" | base64 -w0)
    umask 077
    jq --arg user "$username" --rawfile keys /run/esphome/github.keys --arg motd "$motd" '
        .passwd.users |= map(if .name != "@USERNAME@" then . else
            .name = $user | .sshAuthorizedKeys = ($keys | split("\n") | map(select(length > 0))) end)
        | .systemd.units |= map(if .name == "getty@.service" or .name == "serial-getty@.service" then
            .dropins |= map(.contents |= gsub("@USERNAME@"; $user)) else . end)
        | .storage.files |= map(if .path == "/etc/motd.d/20-esphome-appliance" then
            .contents.source = ("data:;base64," + $motd) else . end)
    ' "$template" > /run/esphome/installed.ign
    echo "Settings ready: user $username."
""").lstrip("\n")


def generic_config(username="", ssh_key="", serial_console=False, **settings):
    template = configs('@SSH_KEY@', '@USERNAME@', ntp_pools=settings.get('ntp_pools'))
    live = {
        'ignition': {'version': '3.4.0'},
        # No initial live SSH access. Resolve build defaults/prompts before disk installation.
        'passwd': {'users': [{'name': 'core', 'shouldExist': False}]},
        'storage': {'files': [
            file('/etc/esphome-appliance/template.ign', json.dumps(template)),
            file('/etc/esphome-appliance/account-defaults.json',
                 json.dumps({'username': username, 'ssh_key': ssh_key}), 0o600),
            file('/usr/local/bin/provision-esphome-appliance', PROVISION, 0o755),
            file('/usr/local/bin/install-esphome-appliance', INSTALL.replace('50 * 1024',
                str(settings.get('min_disk_gib', 50)) + ' * 1024').replace('80 * 1024',
                str(settings.get('max_disk_gib', 80)) + ' * 1024').replace('50-80 GiB',
                str(settings.get('min_disk_gib', 50)) + '-' + str(settings.get('max_disk_gib', 80)) + ' GiB').replace(
                '--ignition-file /run/esphome/installed.ign',
                '--ignition-file /run/esphome/installed.ign' +
                (' --console tty0 --console ttyS0,115200' if serial_console else '')), 0o755),
            file('/etc/tmpfiles.d/esphome-live.conf', 'd /run/esphome 0700 root root - -\n'),
        ]},
        'systemd': {'units': [
            unit('esphome-provision.service', dedent(r"""
                [Unit]
                Description=Configure ESPHome account defaults or GitHub SSH keys
                ConditionVirtualization=|microsoft
                ConditionVirtualization=|kvm
                ConditionVirtualization=|qemu
                Wants=network-online.target getty-pre.target
                After=network-online.target systemd-tmpfiles-setup.service
                Before=getty-pre.target install-esphome-appliance.service
                [Service]
                Type=oneshot
                ExecStartPre=/usr/bin/busctl call org.freedesktop.systemd1 /org/freedesktop/systemd1 org.freedesktop.systemd1.Manager SetShowStatus s no
                ExecStart=/usr/local/bin/provision-esphome-appliance
                ExecStopPost=/usr/bin/busctl call org.freedesktop.systemd1 /org/freedesktop/systemd1 org.freedesktop.systemd1.Manager SetShowStatus s ""
                StandardInput=tty
                TTYPath=/dev/console
                TTYReset=yes
                StandardOutput=tty
                StandardError=tty
                RemainAfterExit=yes
                TimeoutStartSec=0
                [Install]
                WantedBy=multi-user.target
            """).lstrip("\n")),
            unit('install-esphome-appliance.service', dedent(r"""
                [Unit]
                Description=Install ESPHome appliance to the largest empty disk
                ConditionVirtualization=|microsoft
                ConditionVirtualization=|kvm
                ConditionVirtualization=|qemu
                Requires=esphome-provision.service
                Wants=getty-pre.target
                After=esphome-provision.service
                Before=getty-pre.target
                SuccessAction=reboot
                [Service]
                Type=oneshot
                ExecStartPre=/usr/bin/busctl call org.freedesktop.systemd1 /org/freedesktop/systemd1 org.freedesktop.systemd1.Manager SetShowStatus s no
                ExecStart=/usr/local/bin/install-esphome-appliance
                ExecStopPost=/usr/bin/busctl call org.freedesktop.systemd1 /org/freedesktop/systemd1 org.freedesktop.systemd1.Manager SetShowStatus s ""
                StandardOutput=journal+console
                StandardError=journal+console
                TimeoutStartSec=0
                [Install]
                WantedBy=multi-user.target
            """).lstrip("\n")),
        ]}
    }
    return live


def extent(record):
    require(len(record) >= 34, 'Truncated ISO directory record')
    return struct.unpack_from('<I', record, 2)[0] * 2048, struct.unpack_from('<I', record, 10)[0]

def find_iso_file(fp, components):
    fp.seek(16 * 2048)
    pvd = fp.read(2048)
    require(pvd[:7] == b'\x01CD001\x01', 'Expected ISO9660 primary descriptor')
    offset, size = extent(pvd[156:190])
    for component in components:
        fp.seek(offset)
        directory = fp.read(size)
        found = None
        pos = 0
        while pos < len(directory):
            length = directory[pos]
            if length == 0:
                pos = (pos // 2048 + 1) * 2048
                continue
            rec = directory[pos:pos + length]
            require(length >= 34 and len(rec) == length and 33 + rec[32] <= length, 'Invalid ISO directory record')
            name = rec[33:33 + rec[32]].decode('ascii', errors='replace').split(';')[0].rstrip('.')
            if name.upper() == component.upper():
                found = extent(rec)
                break
            pos += length
        require(found is not None, f'Missing ISO file component {component}')
        offset, size = found
    return offset, size

def find_embed(fp):
    return find_iso_file(fp, ('IMAGES', 'IGNITION.IMG'))


def cpio_entry(name, data, inode):
    encoded = name.encode() + b'\0'
    fields = [inode, 0o100644, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(encoded), 0]
    header = b'070701' + ''.join(f'{value:08x}' for value in fields).encode()
    part = header + encoded
    part += bytes((-len(part)) % 4)
    part += data
    return part + bytes((-len(data)) % 4)

def extract_config(blob):
    decoder = lzma.LZMADecompressor()
    raw = decoder.decompress(blob)
    require(decoder.eof, 'ISO validation failed')
    require(raw[:6] == b'070701', 'ISO validation failed')
    fields = [int(raw[6+i*8:14+i*8], 16) for i in range(13)]
    length, namesize = fields[6], fields[11]
    require(raw[110:110+namesize] == b'config.ign\0', 'ISO validation failed')
    start = (110 + namesize + 3) & ~3
    return raw[start:start+length]

def build_iso(source, output, **settings):
    source, output = Path(source).resolve(), Path(output).resolve()
    checksum = output.with_suffix(output.suffix + '.sha256')
    require(source.is_file(), 'Base ISO does not exist')
    require(source != output, 'Source and output must be separate')
    require(output.suffix.lower() == '.iso', 'Output filename must end in .iso')
    require(not output.exists() and not checksum.exists(), 'Output ISO or checksum exists; choose a new filename')
    require(output.parent.is_dir(), 'Output directory does not exist')
    require(isinstance(settings.get('serial_console', False), bool), 'SERIAL_CONSOLE must be a boolean')
    live = generic_config(**settings)
    data = json.dumps(live).encode()
    archive = cpio_entry('config.ign', data, 1) + cpio_entry('TRAILER!!!', b'', 2)
    archive += bytes((-len(archive)) % 512)
    compressed = lzma.compress(archive, format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC32)
    with source.open('rb') as fp:
        offset, size = find_embed(fp)
        require(size > 0 and offset + size <= source.stat().st_size, 'Invalid Ignition embed area')
        fp.seek(offset)
        require(fp.read(size) == bytes(size), 'Base ISO already customized')
    require(len(compressed) <= size, 'Ignition exceeds the reserved embed area')
    mutable_ranges = [(offset, offset + size)]
    created_iso = created_checksum = False
    try:
        # Exclusive creation prevents accidentally overwriting an existing file.
        with output.open('xb') as customized, source.open('rb') as original:
            created_iso = True
            while chunk := original.read(4 * 1024 * 1024):
                customized.write(chunk)
        with output.open('r+b') as fp:
            fp.seek(offset)
            fp.write(compressed + bytes(size - len(compressed)))
        with output.open('rb') as fp:
            require(find_embed(fp) == (offset, size), 'Embed location changed')
            fp.seek(offset)
            require(extract_config(fp.read(size)) == data, 'Embedded Ignition verification failed')
        # Every byte outside the reserved Ignition area must remain unchanged.
        with source.open('rb') as original, output.open('rb') as customized:
            position = 0
            while True:
                a, b = original.read(4 * 1024 * 1024), customized.read(4 * 1024 * 1024)
                if not a:
                    require(not b, 'Output length changed')
                    break
                start = 0
                for left, right in mutable_ranges:
                    left, right = max(0, left - position), min(len(a), right - position)
                    if left < right:
                        require(a[start:left] == b[start:left], 'ISO bytes outside reserved areas changed')
                        start = right
                require(a[start:] == b[start:], 'ISO bytes outside reserved areas changed')
                position += len(a)
        with output.open('rb') as fp:
            digest = hashlib.file_digest(fp, 'sha256').hexdigest()
        with checksum.open('x', encoding='utf-8', newline='\n') as fp:
            created_checksum = True
            fp.write(f'{digest}  {output.name}\n')
    except BaseException:
        if created_checksum:
            checksum.unlink(missing_ok=True)
        if created_iso:
            output.unlink(missing_ok=True)
        raise
    return digest


ENV_KEYS = {'MIN_DISK_GIB', 'MAX_DISK_GIB', 'NTP_POOLS', 'USERNAME', 'SSH_KEY', 'SERIAL_CONSOLE'}


def read_settings(path):
    path = Path(path).resolve()
    require(path.is_file(), 'Settings file not found')
    result = {}
    for number, line in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        require('=' in line, f'{path.name}:{number}: expected NAME=value')
        key, value = line.split('=', 1)
        key, value = key.strip(), value.strip()
        require(key in ENV_KEYS, f'{path.name}:{number}: unknown setting {key}')
        require(key not in result, f'{path.name}:{number}: duplicate setting {key}')
        if value.startswith(('"', "'")):
            require(len(value) >= 2 and value[-1] == value[0], f'{path.name}:{number}: unmatched quotes')
            value = value[1:-1]
        result[key] = value
    minimum = int(result.get('MIN_DISK_GIB', '50'))
    maximum = int(result.get('MAX_DISK_GIB', '80'))
    require(1 <= minimum <= maximum <= 65536, 'Disk bounds must be positive and MIN_DISK_GIB <= MAX_DISK_GIB')
    pools = result.get('NTP_POOLS', ' '.join(DEFAULT_NTP_POOLS)).split()
    require(pools and all(re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}', pool) for pool in pools),
            'NTP_POOLS must be a space-separated list of NTP hostnames or IPv4 addresses')
    username = result.get('USERNAME', '')
    require(not username or (re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', username)
                            and username not in {'root', 'core', 'esphome'}), 'Invalid USERNAME')
    serial = result.get('SERIAL_CONSOLE', 'false').lower()
    require(serial in {'true', 'false'}, 'SERIAL_CONSOLE must be true or false')
    return dict(min_disk_gib=minimum, max_disk_gib=maximum, ntp_pools=pools,
                serial_console=serial == 'true',
                username=username, ssh_key=result.get('SSH_KEY', ''))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('base_iso', help='Unmodified Fedora CoreOS stable x86_64 Live DVD ISO')
    parser.add_argument('output_iso', help='New customized .iso file; never overwritten')
    parser.add_argument('--env', type=Path, help='Optional build settings: disk bounds, NTP pools, Linux username, public SSH key and optional serial console')
    args = parser.parse_args()
    try:
        settings = read_settings(args.env) if args.env else {}
        digest = build_iso(args.base_iso, args.output_iso, **settings)
    except (OSError, ValueError, EOFError, lzma.LZMAError) as error:
        parser.exit(1, f'Build failed: {error}\n')
    print(f'Created {Path(args.output_iso).resolve()}\n'
          f'Verified embedded Ignition and every ISO byte outside its reserved area.\nSHA256: {digest}')


if __name__ == '__main__':
    main()

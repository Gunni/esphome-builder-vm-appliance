# ESPHome VM appliance

An ESPHome remote builder based on Fedora CoreOS stable, for Hyper-V and QEMU/KVM hosts including Proxmox. It runs the stable ESPHome container as an unprivileged user and updates automatically.

## Install

1. Download the **ISO** and verify the checksum from [Releases](https://github.com/Gunni/esphome-builder-vm-appliance/releases).
2. Create a dedicated VM with a **new 64 GB disk**, networking in a vlan your existing esphome can access. If you use UEFI on Hyper-V, ensure you use the **Microsoft UEFI Certificate Authority** Secure Boot template.
3. Ensure boot order is HDD, then ISO.
4. Boot the ISO. Enter your Linux management username, then optionally, enter your GitHub username to import the SSH keys from it, or press Enter to use console access only.

**The installer automatically writes to the largest, non-removable disk.** By default the disk limits are 50–80 GiB. An occupied disk requires typing `ERASE <device>` at the console; mounted disks, swap and active mapped devices are refused. This overwrites the installation; it is not a secure erase.

Treat VM console access as administrator access: it logs into your management account automatically, with passwordless `run0`; SSH requires ssh key authentication you supplied or added manually. The hostname is `esphome-builder-<12 hex characters>` and stays the same across reboots.

## Pair and use

IP addresses and SSH Host keys are printed on the console screen, and it also gets automatically logged in.
Run `esphome-pairing` to start the builder and show its pairing fingerprint and one-time key. Pairing does not start on boot; after pairing, the builder starts normally. An expired pairing window stays closed until you start it again. In your existing ESPHome dashboard, choose **Settings > Send builds > Pair with a build server**, enter the address and port **6055**, and verify the pairing details.

| Command | Purpose |
| --- | --- |
| `esphome-pairing` | Start first pairing and follow its output |
| `esphome-status` | ESPHome Builder status |
| `esphome-logs -f` | Follow ESPHome builder logs; accepts `journalctl` arguments |
| `esphome-start`, `esphome-stop`, `esphome-restart` | Control the ESPHome builder |
| `esphome-shell` | ESPHome Builder container shell |
| `esphome-update` | Check for a container update |
| `esphome-timers` | Show container update and cleanup schedules |
| `run0 journalctl -u esphome-tools -b` | Diagnose initial setup |

The helpers handle account switching and support Bash completion.

First setup needs outbound access to Fedora and GHCR. Builds need package/source services such as PyPI, PlatformIO and GitHub, plus any sources your configuration uses. GitHub SSH-key import is optional.

Back up `/var/lib/esphome/config` if you wish to preserve pairing; builds and caches also live under `/var/lib/esphome`. Container updates run at **03:00 UTC**; CoreOS update reboots are allowed **04:00–05:00 UTC**. Updates and restarts can interrupt builds.

To disable console autologin, first set a password with `run0 passwd <your-username>`, then run:

```sh
run0 esphome-disable-autologin
```

The next console session requires a normal login. The helper deletes itself after success; a failed attempt can be retried.

## Build locally

Download and verify the original Fedora CoreOS **stable x86_64 Live DVD** from [Fedora](https://fedoraproject.org/coreos/download/). Extract the release’s **setup ZIP**, or clone this repository. With Python 3.11+:

```sh
python3 build-installer.py fedora-coreos-live.iso esphome-appliance.iso
```

For optional settings, copy `.env.example` to `.env`, edit it and add `--env .env` to the command. Settings cover disk-size bounds, NTP pools, account defaults and serial console. Public SSH keys only; never embed private keys. Set `SERIAL_CONSOLE=true` for an installed serial console alongside VGA; Proxmox users can add `serial0` for terminal copy/paste.

The builder modifies only the ISO’s reserved Ignition area and verifies all other bytes remain unchanged. The setup ZIP contains the Ignition configuration and source for inspection; the matching Fedora manifest identifies the original ISO and checksum. [Upstream embedding and inspection commands](https://coreos.github.io/coreos-installer/cmd/iso/).

## Builds and releases

- **Every branch push:** tests and downloadable build artifacts in Actions.
- **A signed `v*` tag reachable from `main`:** a GitHub Release.
- **New Fedora stable versions:** checked every six hours and released using the latest eligible project tag.

Release tags must be signed by a key in `.github/allowed_signers` on `main`. Release assets carry GitHub attestations recording the signed source tag/commit and Fedora base. Verify a downloaded asset with:

```sh
gh attestation verify <asset> --repo Gunni/esphome-builder-vm-appliance --signer-workflow Gunni/esphome-builder-vm-appliance/.github/workflows/release.yml --predicate-type https://github.com/Gunni/esphome-builder-vm-appliance/attestations/release-inputs/v1
```

Add `--format json` to inspect the attested source commit and Fedora base against the release details.

Published releases are immutable. Fixes require a new tag. For an extra build, use **Actions > Build appliance ISO**. Selective Actions policies must allow GitHub’s official actions; all external actions are SHA-pinned.

MIT licensed; Fedora CoreOS and ESPHome retain their own licenses.

# ESPHome VM appliance

This project turns a Fedora CoreOS Live DVD into a guided ESPHome remote-builder installer for Hyper-V, Proxmox and other QEMU/KVM hosts. Python 3.11+ and the standard library are all you need on the build computer.

The guest runs the latest stable ESPHome container using Podman Quadlet, host networking and all allocated VM CPUs. Fedora CoreOS handles OS updates through Zincati. This is a dedicated remote builder for the main ESPHome Device Builder dashboard, not a second dashboard.

**The customized ISO automatically installs to the unique largest writable, non-removable whole disk.** Use a dedicated VM with a fresh disk. The installer refuses tied-largest disks, disks outside its size bounds, partitions or mapped children, mounts or swap, filesystem/partition-table/RAID signatures, and any nonzero data in a full-disk read. It never falls back to a smaller disk when the largest is occupied. The defaults accept a 50–80 GiB disk. The zero-filled check reads the whole disk and can take time; it does not write to it. Installation proceeds only after these checks pass.

## Download an installer or setup package

Open this repository’s **Releases** page. For a ready-to-boot installer, download **`esphome-appliance-fcos-<Fedora version>-<12-character source SHA>.iso`** and verify it with the matching **`.iso.sha256`** or **`SHA256SUMS`**.

To inspect the configuration and build locally, download:

- **`esphome-appliance-setup.zip`**: the portable Python builder, its templates, generic live Ignition configuration, source, tests and documentation. Extract it before use.
- **`esphome-appliance.ign`**: the same generic live Ignition configuration as a standalone file for users of upstream CoreOS tooling.
- **`SHA256SUMS`**: checksums for all release assets.

For local builds, download the original Fedora CoreOS **stable x86_64 Live DVD** using the exact URL in `fedora-base.json` or the release notes, and verify Fedora’s checksum/signature. The small setup ZIP contains configuration and source; the bootable ISO is a separate release asset. It uses generic defaults, including `SERIAL_CONSOLE=false`, and never reads a local `.env` or includes personal account settings or keys.

You can inspect the source and configuration, then use the portable builder below. Alternatively, use upstream `coreos-installer` to embed the supplied configuration:

```sh
coreos-installer iso ignition embed --ignition-file esphome-appliance.ign \
  --output esphome-appliance.iso fedora-coreos-live.iso
coreos-installer iso ignition show esphome-appliance.iso
```

The supplied file configures the **live installer**, including disk checks and account/GitHub prompts; it is not a configuration to attach directly to an installed Hyper-V/QEMU disk image. Ignition includes administrative provisioning scripts, so inspect the payload as well as verifying Fedora’s image. Upstream embedding/inspection commands are documented in [CoreOS installer](https://coreos.github.io/coreos-installer/cmd/iso/).

## Automatic builds and GitHub Releases

The **Build and release appliance** workflow runs on pushes to any branch, project version tags such as `v1.0.0`, and a six-hour Fedora check at 00:17, 06:17, 12:17 and 18:17 UTC:

- A push to any branch tests and builds the current source. Download the ISO and setup files from its Actions artifact; artifacts expire after seven days.
- A pushed `v*` project tag tests and builds that tagged source. It publishes a GitHub Release only when the tag’s commit is reachable from `main`; tags on other branches produce build artifacts. Hyphenated project tags are prereleases.
- The scheduled check reads [Fedora’s stable stream metadata](https://builds.coreos.fedoraproject.org/streams/stable.json). A new stable x86_64 Live DVD version produces a release such as `fcos-44.20260913.3.2`, built from the latest project version tag reachable from `main`. Version tags are ordered by Git’s version ordering; only tags whose commits are reachable from `main` are eligible. Until a project version tag exists, Fedora publication is skipped. Manual workflow dispatch runs the same Fedora check immediately.

Every build runs normal/optimized tests on Windows/Linux with Python 3.13. It downloads or verifies a cached copy of the selected Fedora ISO, embeds the generic Ignition payload, verifies every byte outside the reserved Ignition area is unchanged, and checks all output checksums.

The ISO and its checksum accompany the small setup ZIP, standalone live Ignition, **`fedora-base.json`** and **`SHA256SUMS`**. The manifest records the exact Fedora release, original ISO URL and checksum, and is also included in the ZIP. `SHA256SUMS` covers all six assets. Release downloads remain available beyond the Actions artifact retention period. ISO filenames identify the exact Fedora version and source commit, for example `esphome-appliance-fcos-44.20260913.3.2-9701a46d0556.iso`; checksums use the same basename plus `.sha256`. Actions archive names also include the workflow run and attempt to distinguish builds.

Published releases are immutable and skipped on reruns. Only drafts are retried, using their original source revision. New releases stay draft until all six uploads are verified. Corrections to a published release require a new version tag. Publishing confirms source/package tests and ISO verification; new Fedora versions still require guest runtime verification.

Scheduled runs use the default branch and GitHub may delay them. The workflows and dotfile templates must be committed at the repository root. With a selective Actions allowlist, enable **Allow actions created by GitHub** for the SHA-pinned official actions. Publication uses the repository’s `GITHUB_TOKEN` with `contents: write`; repository or organization policy must permit that permission. Installed guests continue their normal OS and container update schedules independently.

## Additional ISO builds from GitHub Actions

Open **Actions > Build appliance ISO > Run workflow** for a convenience build. No username, key or platform inputs are required. The workflow must exist on the default branch for manual runs; users without repository write access can run it in their own forks.

This workflow resolves Fedora’s current **stable x86_64 Live DVD** from [official stream metadata](https://builds.coreos.fedoraproject.org/streams/stable.json), caches and verifies the pristine ISO by SHA-256, runs normal/optimized tests and builds a generic installer with default settings. Download its artifact ZIP containing `esphome-appliance-fcos-<Fedora version>-<12-character source SHA>.iso` and its matching checksum, then **extract the ISO before mounting it**. These convenience artifacts expire after **seven days**. Automatic releases also include the bootable ISO, with permanent release downloads. The manual workflow is useful for an extra build without publishing a release.

## Build an installer locally

Download the Fedora CoreOS **stable, x86_64, Live DVD** ISO from [Fedora](https://fedoraproject.org/coreos/download/) and verify it using Fedora's published checksum/signature instructions. Our checksum verifies the customized image; it does not authenticate the Fedora download.

```powershell
python3 .\build-installer.py 'C:\Downloads\fedora-coreos-live.iso' 'C:\Downloads\esphome-appliance.iso'
```

Use the command that launches Python 3.11+ on your computer (`python3`, `python`, or `py -3`). On Linux/macOS, use `python3` with local paths.

No `.env` or credentials are needed. Keep the script and its three dotfile templates together; `.download-coreos.py` is the internal GitHub CI helper.

For optional build defaults, copy `.env.example` to `.env`, edit it and append `--env /path/to/.env`. The file is only read when explicitly supplied. It controls:

| Setting | Default / purpose |
| --- | --- |
| `MIN_DISK_GIB`, `MAX_DISK_GIB` | `50`, `80`; the largest disk must fall within these bounds. A nominal 64 GB disk fits. |
| `NTP_POOLS` | `2.pool.ntp.org`, `0.pool.ntp.org`, `1.pool.ntp.org`, `3.pool.ntp.org`; space-separated hostnames, no forced priority. |
| `SERIAL_CONSOLE` | `false`; set `true` to add standard `console=tty0 console=ttyS0,115200` boot arguments to the installed system; the live ISO keeps Fedora’s default console. |
| `USERNAME` | Optional Linux account name; prompt at VM boot if missing or empty. |
| `SSH_KEY` | Optional single-line public SSH key, including its key type; if missing or empty, prompt for a GitHub username at VM boot and download that account’s public keys. |

Settings support `NAME=value`, blank lines, full-line `#` comments and matching quotes. No shell execution, variable expansion, `export` syntax or inline comments are supported. Supplied `USERNAME` and `SSH_KEY` values are embedded in the ISO. Use a public key, never a private key. For example:

```dotenv
USERNAME=builder
SSH_KEY="ssh-ed25519 <public-key-base64> optional-comment"
```

Leave either setting empty to prompt only for the missing information at VM boot. Omit both for a generic installer. An ISO built with account defaults is personalized: anyone installing it authorizes the embedded key, or uses the embedded username with keys fetched at boot. Distribute generic installers with these settings omitted.

The source ISO stays untouched. Builds refuse existing output ISOs/checksums and remove partial output on failure. The only generated files are the new ISO and its `.iso.sha256`. Each build decodes/compares embedded Ignition and verifies every byte outside the reserved Ignition area, including when serial is enabled. The kernel, initramfs, bootloader files and live boot arguments remain byte-for-byte identical to the source ISO. Checks also run with `python -O`.

## Account setup: build defaults and console prompts

At the first ISO boot, the installer uses any account defaults embedded with `--env` and asks only for missing information:

1. **Linux system username**, if `USERNAME` is missing or empty: the key-login/passwordless-sudo account to create. Use a lowercase Linux account name; `root` and `core` are refused.
2. **GitHub username**, if `SSH_KEY` is missing or empty: the account whose **all public SSH keys** should be authorized for that Linux user. You must hold at least one corresponding private key.

When `SSH_KEY` is supplied, the installer uses that public key without a GitHub prompt or download. Otherwise it downloads `https://github.com/<username>.keys` over HTTPS and requires a successful, nonempty response. It authorizes the public keys in that response. Account setup completes before any disk is written. The GitHub path requires network connectivity, DNS and outbound HTTPS during live-ISO setup.

The supplied public key, or all public keys downloaded from the chosen GitHub account, get administrative access through passwordless `wheel` sudo. Trust the chosen key/account. Downloaded keys are a snapshot taken at installation; changes to GitHub keys are not synchronized afterward. The live ISO has no initial SSH account. If console setup fails, correct the network/account input and reboot the installer; rebuild it if an embedded default needs correction; no disk installation has begun.

Every installation uses the same configuration. Hyper-V heartbeat and graceful shutdown use the kernel’s native integration; KVM/QEMU uses the standard ACPI power button and systemd-logind for graceful shutdown. Find the guest’s address through DHCP/router records or the console. The live installer uses native `ConditionVirtualization=` conditions for Hyper-V, KVM and software QEMU. Software emulation can be substantially slower than KVM.

## Create a Hyper-V VM

1. Create a **Generation 2** VM with a new **64 GB** disk on SCSI, one DVD drive and a network adapter on a suitable virtual switch/VLAN. Allocate the CPUs/RAM you want the builder to use; an earlier Hyper-V setup used 16 CPUs and 10 GB RAM, while current isolated KVM tests use 4 CPUs and 4 GiB RAM. The appliance adds no CPU cap.
2. While the VM is off, open **Settings > Security**, enable **Secure Boot**, and select **Microsoft UEFI Certificate Authority**, the Linux template. Keep the host's Secure Boot support up to date. See [Microsoft's Generation 2 security documentation](https://learn.microsoft.com/en-us/windows-server/virtualization/hyper-v/generation-2-virtual-machine-security-features).
3. Under **Integration Services**, keep **Heartbeat** and **Operating system shutdown** enabled.
4. Under **Checkpoints**, clear **Enable checkpoints** unless you deliberately want them. If you enable them, choose the checkpoint policy appropriate for your backups; checkpoints are not a replacement for backups of pairing state. Automatic checkpoint creation is unnecessary for this appliance.
5. Mount **only the customized installer ISO**. In **Firmware**, put the hard disk before the DVD. The fresh disk has no bootable OS, so the first boot uses the DVD. After installation, the disk boots first. Start the VM, answer any missing account prompts, then automatic disk verification/installation begins.
6. On the installer's automatic reboot, eject the installer; leave the hard disk first. Booting the installer again hits the nonempty-disk guard; eject it and boot the disk.

## Create a Proxmox VM

The common configuration is tested in an isolated QEMU/KVM VM. A real Proxmox installation and Secure Boot test are still required before claiming full Proxmox support.

1. Create an x86_64 Linux VM using **OVMF (UEFI)** with an EFI disk, **VirtIO SCSI single**, one fresh **64 GB SCSI disk** and a VirtIO NIC on your bridge/VLAN. Choose CPU `host` when suitable for your migration requirements and allocate the desired cores/RAM. The installer discovers both SCSI and VirtIO Block disks. UEFI Secure Boot depends on the EFI disk's enrolled keys; validate the Fedora ISO with your chosen firmware/keys. It has not been tested here on Proxmox.
2. Leave **QEMU Guest Agent** disabled in the VM’s options and keep **ACPI** enabled. Proxmox shutdown uses the ACPI power button.
3. Mount the customized installer ISO and place the DVD first for installation.
4. Start the VM and complete any missing account prompts. At the installer reboot, eject the ISO and put the installed disk first.

For optional text console access after installation, build with `SERIAL_CONSOLE=true`. In Proxmox add a **Serial Port** (`serial0`) and select the serial console to get terminal copy/paste. Use the normal VM console for installation. The installation script embedded in Ignition passes standard `--console` options to `coreos-installer` for the installed disk. Kernel console arguments and systemd then provide the serial login; the VGA console remains enabled, and adding a serial device is optional.

CoreOS uses Ignition; account defaults and console/GitHub setup supply the account and keys. Networking follows CoreOS/NetworkManager defaults.

## First startup and hostname

Provisioning has three stages:

1. The live ISO resolves the account settings from embedded defaults and any required prompts, verifies the disk, installs CoreOS and reboots.
2. The first disk boot checks CoreOS updates, stages debugging packages and pulls the current stable ESPHome image. Only after all steps succeed does it mark setup complete and reboot to activate the staged deployment.
3. The next boot starts the builder and allows console login and SSH. Later boots skip the completed setup stage and use the normal update schedules.

Initial setup needs DNS and outbound HTTPS. Failures retry every 60 seconds without creating the completion marker; console login, SSH and the builder remain held. Progress/errors go to the console and journal, with systemd status output suppressed during setup and restored afterward. Use the VM console to diagnose setup failures.

The permanent default hostname is **`esphome-builder-<12 hex characters>`**. Ignition writes `esphome-builder-????????????` to `/etc/hostname`; systemd expands the question marks using a cryptographic hash of `/etc/machine-id`. The same machine-id produces the same name across reboots, independent of the hypervisor, DHCP and VM display name. Cloning an installed disk without changing its machine-id preserves the same hostname. See [systemd hostname patterns](https://raw.githubusercontent.com/systemd/systemd/main/man/hostname.xml).

Networking requires address configuration, DNS, outbound HTTPS to Fedora repositories/container registries and NTP, plus TCP **22** for SSH administration, TCP **6055** from the main dashboard and multicast UDP **5353** if you use mDNS. Host networking enables local-link discovery; VLAN boundaries require an mDNS reflector or manual pairing. Proxmox cloud-init network settings are not consumed; the appliance uses CoreOS/NetworkManager network defaults.


## Pair and operate

Find the address through your hypervisor, router or network tools and connect as the Linux user supplied at build time or entered at boot:

```sh
ssh <chosen-username>@<VM-address>
```

Use the corresponding private SSH key to log in. Retrieve the pairing fingerprint and key from the builder logs:

```sh
sudo journalctl -u esphome-builder -n 100 --no-pager
```

In the main Device Builder dashboard, choose **Settings > Send builds > Pair with a build server**, enter the guest address and port **6055**, then verify the fingerprint and key. There is no dashboard UI on port 6052. Preserve `/var/lib/esphome/config` to retain the builder identity/pairing. See [ESPHome's remote-builder documentation](https://github.com/esphome/device-builder#headless-build-server---remote-build-only).

```sh
sudo systemctl status esphome-builder zincati chronyd
sudo journalctl -u esphome-builder -f
sudo systemctl restart esphome-builder
sudo systemctl start podman-auto-update.service
systemctl list-timers podman-auto-update.timer
sudo journalctl -u esphome-tools -b --no-pager
chronyc sources -v
```

## Updates and persistence

- ESPHome uses `ghcr.io/esphome/esphome:stable`, `AutoUpdate=registry` and the native Podman updater. The first installed boot pulls the current stable image before the setup reboot; later boots use the cached image. The updater timer starts after the builder, checks three minutes after activation and daily at **03:00 UTC**. A missed daily check can run immediately on timer activation because `Persistent=true`. Updates restart the container and can interrupt a build.
- Zincati automatically stages CoreOS updates and allows update reboots **04:00–05:00 UTC daily**. Its periodic window controls reboots, not a mandatory daily reboot or an OS-update check on every boot. Layered packages carry forward into OS updates. [Zincati strategies](https://coreos.github.io/zincati/usage/updates-strategy/).
- All VM CPUs are available. Console keyboard is **US** and timezone **UTC**. NTP pools are configurable in `.env`; there is no forced source priority.
- Quadlet creates/operates the rootful container with `Network=host`. Persistent config, builds and caches live under `/var/lib/esphome/{config,build,cache,ccache}` with SELinux container relabeling. `/var/tmp/esphome` supplies temporary container files; standard tmpfiles cleanup removes unused/unchanged files after seven days. Persistent caches have no automatic age-based deletion.
- Tmpfiles creates directories and restores SELinux policy labels on the timezone symlink before normal services start. The package-completion marker uses a separate tmpfiles configuration outside `tmpfiles.d`, so normal boot processing cannot create it prematurely. SELinux stays enforcing.
- Journald limits persistent logs to **256 MB / 14 days**, and runtime logs to **64 MB**. A weekly timer prunes dangling container images Sunday **05:30 UTC**.
- Before offering console login, SSH or the builder, the first installed boot checks and stages CoreOS updates, layers debugging tools, and pulls stable ESPHome. It marks setup complete only after all steps succeed, then reboots to activate the staged deployment. Failures retry through systemd after 60 seconds; initial setup requires working network access. Subsequent boots use the completed marker and normal update schedules.
- Debugging tools `binutils`, `htop`, `btop`, `vim-enhanced`, `tmux`, `jq`, `bind-utils`, `tcpdump`, `ethtool` and `traceroute` are layered on first disk boot; they are not RPM payloads embedded in the installer.

Changes to build defaults or Ignition affect newly installed guests only. To change an installed Quadlet, reload systemd and restart the builder. Its `[Install]` section is handled by the generator; do not enable the generated service directly. [Quadlet documentation](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html).

## Development and release status

```sh
python -m unittest discover -s tests -v
python -O -m unittest discover -s tests -v
```

The test suite covers ISO encoding/verification, malformed inputs, no-overwrite safeguards, failure cleanup, settings, native hypervisor conditions and generated startup ordering. CI runs the same tests on Windows/Linux with Python 3.13, checks build/release policy using real Git ancestry fixtures, and lints workflow syntax. ISO builds also verify a serial-enabled installer against the original Fedora image. Real systemd, SELinux, hypervisor integration, Secure Boot, connectivity and firmware builds require VM testing. Build verification is not a claim of successful installation.

The original Hyper-V implementation installed and built firmware successfully, including the console ordering and SELinux label fix. QEMU/KVM tests have verified installation, package staging/reboot, builder readiness, stored builder identity across reboot. A fresh installation verified machine-id hostnames, installed UEFI serial output/login, initial update checking, package staging, image pull, the setup reboot and gated SSH/builder startup. A subsequent service audit verified zero failed services and graceful ACPI poweroff. No newer CoreOS release was available during that check; activation of a newer release, installed BIOS serial boot, Hyper-V and actual Proxmox still need current runtime verification. Treat this source as a release candidate until those checks pass.

This source uses the **MIT license**. Fedora CoreOS, ESPHome and bundled guest software retain their own licenses. The builder modifies only the documented reserved Ignition area. Every byte outside that area is verified unchanged against the source ISO, for all settings. Optional serial configuration is carried inside Ignition and applied to the installed disk; it never changes the live ISO’s boot files or arguments. [CoreOS ISO embedding format](https://coreos.github.io/coreos-installer/iso-embed-ignition/).

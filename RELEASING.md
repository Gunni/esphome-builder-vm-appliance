# Release checklist

Before pushing a version tag (which automatically publishes a GitHub Release):

- Run `python build-release.py build/release` in a clean output directory, verify `SHA256SUMS`, and inspect the setup ZIP and standalone live Ignition. Confirm all templates and source are present, no personal settings/keys are packaged, and no Fedora ISO is included.
- Run a manual generic GitHub ISO build and verify the downloadable artifact and SHA-256. This optional convenience build is separate from the small release package.
- Test disk discovery: empty SCSI/VirtIO disks succeed; partitions, signatures, nonzero data, equal largest sizes, out-of-range sizes and mounted disks refuse installation. An occupied largest disk must never cause installation to a smaller disk.
- Run the automated tests on Windows and Linux; check GitHub Actions is green.
- Build the generic installer and boot it on both hypervisors from a pristine stable Fedora CoreOS x86_64 Live DVD, then enter your Linux username and GitHub username at boot.
- Test successful import of multiple GitHub keys, empty/no-key accounts, invalid usernames and network failure. Confirm successful nonempty responses are imported without per-key validation. No failure should write the disk.
- Test no account defaults, username only, public key only, and both defaults. Only missing information should prompt; supplied keys must skip GitHub download. Confirm personalized images are excluded from public artifacts.
- Verify global NTP defaults and custom NTP settings.
- Verify the single installed payload on Hyper-V and QEMU/KVM: no userspace guest-agent packages install; Hyper-V heartbeat/shutdown use native kernel integration and QEMU/Proxmox shuts down gracefully through ACPI. No hostname helper or metadata CD should be required.
- Verify `esphome-builder-<12 hex characters>` remains stable across reboot for an unchanged machine-id.
- Wipe the VM disk between installation tests. Verify disk installation, console progress without status animation, first-boot OS update/package installation/image pull/reboot and eventual login/builder readiness.
- Hyper-V: verify Generation 2 Secure Boot with Microsoft UEFI Certificate Authority; native heartbeat and graceful shutdown without KVP; machine-id hostname persistence after reboot; no new SELinux denials for localtime.
- Proxmox: verify OVMF/SCSI and VirtIO Block device choices; graceful ACPI shutdown with QEMU Guest Agent disabled; machine-id hostname persistence; Secure Boot separately if advertising it.
- Pair with the current main ESPHome dashboard and compile real firmware. Verify mDNS on the intended link, stored pairing after reboot, all VM CPUs available and update timers.
- Check container updates/restarts and Zincati window behavior; document any changed dashboard compatibility.
- Publish source and optionally the generic installer: no `.env`, private/public key files or generated per-installation Ignition. Never distribute a personalized installed Ignition.
- Update CHANGELOG.md with the tested CoreOS/ESPHome/dashboard/hypervisor versions and exact tested features; use a version tag only after tests pass.

Source archives must include the three dotfile templates and `.env.example`. The automated tests use a non-operational synthetic public key solely for fixtures. Report configuration/build checks separately from guest runtime tests.

- Verify all ISO bytes outside Ignition are unchanged with serial enabled and disabled. Test installed `SERIAL_CONSOLE=true` with BIOS and UEFI, with and without a serial device; verify installed kernel arguments and serial login. Verify failed initial updates/pulls cannot create the setup marker or expose SSH/login/builder readiness.

After validation, push a `v*` version tag (for example `git push origin v1.0.0`) to trigger Release setup package. Hyphenated tags are prereleases. Confirm the test matrix and release job pass, the GitHub Release contains the ZIP, live Ignition and SHA256SUMS, and downloaded checksums verify. A workflow rerun replaces those assets; version tags should identify reviewed source.

Automatic Fedora releases: Release setup package checks stable.json every six hours and on manual dispatch, publishes missing/incomplete fcos-<release> packages after tests, and includes fedora-base.json. Commit the workflow on the default branch to enable this; no manually pushed tag is needed. Verify unchanged/published releases skip and incomplete/draft releases recover. Treat each automatically selected new Fedora release as requiring runtime verification before claiming VM compatibility.

- Audit units after boot: homed/GSS proxy masked, fwupd/RAID timers and Docker socket disabled; login, SSH, builder, network, chrony and updates working with zero failed units. Preserve debugging tools. Verify Hyper-V native heartbeat/shutdown on a real Hyper-V host.

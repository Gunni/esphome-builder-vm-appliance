# Changelog

## Unreleased

- Generic Fedora CoreOS installer for an ESPHome remote-builder VM, using standard-library Python.
- Console prompts for Linux username and GitHub username; imports all public SSH keys from the chosen GitHub account before disk installation, requiring a successful nonempty download without per-key validation.
- One generic installed configuration with native Hyper-V kernel integration and ACPI shutdown on QEMU/KVM; no userspace guest agents.
- Permanent `esphome-builder-<12 hex characters>` hostname from systemd’s machine-id pattern; removed hypervisor-name scripts and metadata-CD handling.
- Largest-disk selection with ambiguity rejection, size bounds and read-only partition/signature/mount/full-zero checks; no fallback to a smaller disk.
- Native package staging/reboot, console login ordering, clean progress, Podman Quadlet/auto-updates and Zincati.
- Tmpfiles lifecycle and SELinux symlink label restoration, journal limits and image cleanup.
- Generic GitHub Actions builds from latest verified Fedora stable metadata, cached base ISO and seven-day downloadable artifacts.
- Optional `.env` build defaults for disk-size bounds, global NTP pools, Linux username and a public SSH key; missing account settings prompt at VM boot, with GitHub used to fetch missing keys. Builds without account defaults remain generic.
- Automated tests/Windows-Linux CI; isolated QEMU/KVM installation and runtime checks completed, with Hyper-V and real Proxmox release checks tracked separately.
- Optional `SERIAL_CONSOLE=true` uses standard coreos-installer console options for installed boots; live ISO modifications remain Ignition-only.
- First installed boot checks CoreOS updates, layers debugging tools, and pulls stable ESPHome before its setup reboot; SSH joins the setup gate.

- Small GitHub Release assets on project version tags: allowlisted source/setup ZIP, generic live Ignition and SHA256SUMS, after Windows/Linux tests. ISO builds remain optional manual Actions artifacts.

- Poll Fedora stable stream metadata every six hours and on manual dispatch; automatically tag/publish missing setup packages with a Fedora URL/checksum manifest. Skip complete releases and recover incomplete/draft uploads.

- Removed Hyper-V KVP and QEMU guest-agent layers; masked unused homed/GSS proxy services and disabled firmware-refresh/RAID timers and Docker socket while retaining Fedora base packages and debugging tools.

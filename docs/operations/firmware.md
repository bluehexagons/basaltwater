# Firmware auditing and updates

Basaltwater provides a small local firmware workflow backed by `fwupd`. It is
audit-first, does not enable unattended firmware flashing, and never reboots a
host automatically.

## Audit firmware

Run the audit on the physical machine whose firmware you want to inspect:

```bash
basaltw firmware audit
```

The audit records DMI system and BIOS identity, the running kernel, related
Debian package versions, fwupd devices, and firmware updates available from the
configured fwupd remotes. It refreshes remote metadata by default. Use cached
metadata or produce machine-readable output with:

```bash
basaltw firmware audit --no-refresh
basaltw firmware audit --json
```

If `fwupdmgr` is unavailable, Basaltwater offers to install the `fwupd` Debian
package through APT before continuing. The installer uses `sudo` when needed
and available. For an already-authorized non-interactive dependency install,
use `--install-dependencies`.

An empty fwupd update list is not proof that every device is current. fwupd can
only report devices and releases supported by the system vendor or LVFS. Older
machines may require a vendor bootable image or another model-specific offline
procedure. Debian packages such as `pve-firmware`, `intel-microcode`, and
`amd64-microcode` do not replace motherboard BIOS or UEFI updates.

## Apply updates

Start with an audit, stop or migrate workloads, and then update every eligible
device:

```bash
basaltw firmware audit
basaltw firmware update
```

To update one device, copy its ID from the audit:

```bash
basaltw firmware update DEVICE_ID
```

The update command repeats the audit immediately before changing anything. On
a local Proxmox host it also checks `qm list` and `pct list`; running guests
block the update unless the operator explicitly passes
`--allow-running-guests`. A failed guest-state check always blocks the update.

The final firmware action requires confirmation. `--yes` acknowledges that
confirmation and passes fwupd's assume-yes option, but Basaltwater always
suppresses fwupd's reboot prompt and does not bypass the audit or Proxmox
checks:

```bash
basaltw firmware update --yes
```

Firmware can interrupt devices or leave an update pending until shutdown or
reboot. Keep local console access available, use stable power, review the
vendor's release and recovery instructions, and reboot manually only after
reading fwupd's result.

## Extension boundary

Dependency installation is represented by a command/package declaration, and
fwupd output is normalized into stable audit records before the CLI renders it.
Future vendor-specific backends can reuse the dependency installer and add
their own inventory and guarded update adapter without changing the public
audit report shape.

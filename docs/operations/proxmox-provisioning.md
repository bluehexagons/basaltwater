# Proxmox provisioning policy

Use `basaltw setup ... --provision-on NODE` to configure startup and deletion
protection for QEMU VMs and both privileged and unprivileged LXC containers.
These settings belong to the Proxmox guest, rather than its installed services.
See the [Proxmox guide](proxmox.md)
for storage, networking, and initial provisioning requirements.

| Option | Behavior |
| --- | --- |
| `--autostart` / `--no-autostart` | Enable or disable starting the guest when its Proxmox node boots (`onboot`) |
| `--start-order N` | Set the node's guest startup order; smaller numbers start first and shutdown uses reverse order |
| `--start-delay SECONDS` | Wait this long after starting this guest before starting the next guest (`startup.up`) |
| `--shutdown-timeout SECONDS` | Set the guest's shutdown wait limit during the node's shutdown sequence (`startup.down`) |
| `--protection` / `--no-protection` | Enable or disable Proxmox's protection against deleting the guest and its disks |

Order, delay, and timeout accept non-negative integers, including zero. These
are Proxmox's native startup settings; they do not check application readiness
or define a cross-node dependency graph. They can be saved while auto-start is
disabled. For native details, see the upstream [QEMU startup and shutdown
guide](https://pve.proxmox.com/pve-docs/chapter-qm.html#qm_startup_and_shutdown)
and [container configuration reference](https://pve.proxmox.com/pve-docs/pct.conf.5.html).

## Provision a guest

New guests still default to auto-start enabled and deletion protection disabled.
For example, provision a VM that starts in order 2, allows a 30-second startup
delay and a 120-second shutdown timeout, and is protected against deletion:

```bash
basaltw setup server_lite 192.0.2.50/24 agent \
  --machine vm --provision-on pve.example.test \
  --memory 2G --storage root local-lvm 16G \
  --gateway 192.0.2.1 --dns 192.0.2.1 \
  --autostart --start-order 2 --start-delay 30 --shutdown-timeout 120 \
  --protection
```

Use `--machine unprivileged` for an LXC container with the same policy. To keep
a new guest off after future node reboots, select `--no-autostart`. Provisioning
still starts it immediately so bootstrap and remote setup can run. New guests
receive protection after successful provisioning bootstrap, allowing failed VM
bootstrap to use the existing cleanup path. A later policy-application failure
preserves the bootstrapped guest and reports an error.

Add `--dry-run` to preview the selected policy without applying provider changes.

## Change an existing guest

Inspect the saved command with `basaltw cmd HOST`, then rerun its `setup`
command with the desired options. Keep its `--provision-on` selection. For
example, to disable auto-start for the saved VM above:

```bash
basaltw setup server_lite 192.0.2.50 agent \
  --machine vm --provision-on pve.example.test --no-autostart
```

Saved provisioning metadata supplies omitted storage and network declarations.
Explicit startup and protection options force provider verification even when
the saved values already match, so a rerun repairs provider drift. Basaltwater
checks the existing guest's identity, applies changes, and reads back the
provider settings before continuing remote setup.

Omitted options retain saved choices. With no saved choice, an existing guest's
provider value is preserved. Startup members are merged individually: changing
only `--start-delay 0` keeps the existing order and shutdown timeout. Disabling
auto-start also retains the schedule for later re-enabling. Removing protection
requires an explicit `--no-protection`.

Use `setup` for these provider settings. `patch` rejects explicit startup and
protection options because it operates inside the guest. Ordinary patches
preserve the saved policy without applying it to Proxmox.

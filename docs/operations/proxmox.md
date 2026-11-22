# Proxmox workflows

Basaltwater can register Proxmox hosts, cache their capabilities, provision
Debian VMs or unprivileged LXCs, and manage guest lifecycle operations.
[Machine types](../reference/machine-types.md) explains the guest capability differences.
Provider-specific host operations and advanced mutations remain under
`basaltw proxmox ...`. Common guest observations, resource statistics,
power-state lifecycle, boot ordering, and confirmed QEMU VM destruction are
available through the provider-neutral `basaltw vm ...` commands; see the
command reference for the stable JSON shape.

These workflows target stable Proxmox VE 9.x and use its `qm` and `pct`
interfaces. Bridge discovery identifies Linux bridge interfaces by type, so
explicitly named Proxmox SDN bridges are supported alongside conventional
`vmbr*` bridges.

## Choose a topic

<a id="quick-setup-proxmox-host-to-coding-vm"></a>
<a id="optional-host-web-panel"></a>
<a id="joining-a-cluster-with-two-factor-authentication"></a>
<a id="concurrent-setup-and-provisioning"></a>
- [Set up and join hosts](proxmox-host-setup.md) First host setup, optional web panel, cluster join, and concurrent provisioning.

<a id="host-safety-defaults"></a>
- [Host safety, storage, and admission](proxmox-host-safety.md) Host safety defaults, capacity checks, and storage policy.

<a id="graphical-vm-hardware-baseline"></a>
<a id="provisioned-web-server-vm"></a>
<a id="provisioned-lxc"></a>
- [Provision VMs and containers](proxmox-guest-setup.md) Graphical hardware, web-server VMs, and unprivileged LXC setup.

<a id="guest-lifecycle"></a>
<a id="resource-pressure-and-boot-recovery"></a>
<a id="power-state-commands"></a>
- [Operate and recover guests](proxmox-guest-lifecycle.md) Resource pressure, boot recovery, and power-state commands.

<a id="placement-backups-and-migration"></a>
- [Placement, backups, and migration](proxmox-placement.md) Placement policies and storage migration.

<a id="orphaned-volumes-and-stuck-locks"></a>
<a id="cluster-and-notifications"></a>
<a id="network-planning"></a>
<a id="real-system-smoke-test"></a>
- [Recovery, notifications, and smoke checks](proxmox-recovery.md) Orphaned volumes, stuck locks, cluster checks, and validation.

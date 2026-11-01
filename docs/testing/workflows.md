# Tested workflow examples

**Evidence reconciled through 2026-11-01.** These are representative commands and
procedures for workflows with selected live evidence. Values are fictional;
the exact commands, versions and configuration used in a private run can
differ. A successful dry run checks planning only. Read each linked guide and
use a disposable target and data before applying setup.

## Debian server and shared desktop

🟢 **Labbed:** selected workflow; crash and interruption cases remain open.

Selected clean Debian 13 `server_lite` setup with XFCE and RDP, populated rerun,
reboot, client input/clipboard and reconnect passed. Desktop crash, interrupted
setup and the complete release case remain open. The
[XRDP guide](../workstations/xrdp.md) describes setup and client checks.

```bash
basaltw setup server_lite 192.0.2.10 operator --desktop xfce --rdp --dry-run
```

Substitute a reachable disposable target and review the generated plan before
an actual setup. Follow the guide's authentication and networking instructions.

## Build on one machine, deploy on another

🟢 **Labbed:** selected builder and deployment workflows; public ingress remains open.

Selected Debian VM checks covered builder enrollment, Node/static and Go builds,
artifact delivery, rerun, target refresh with the builder offline and retained
application data. Public tunnel/ACME and power-loss behavior are separate.
The [single-site builder walkthrough](../examples/builds/homelab-build-test.md)
contains a fictional two-command provisioning example and HTTP verification;
the [multi-application walkthrough](../examples/builds/homelab-local-builder.md)
adds example manifests and SSH source restrictions. These adapted examples are
not a claim that those exact fictional endpoints were tested.

## Storage and service recovery

🟢 **Labbed:** selected storage and same-host recovery; off-host recovery remains open.

Selected checks covered Btrfs setup/rerun/reboot, Samba and Syncthing exchange,
Gogs Git/LFS, HomeBox API backup/restore and same-host guest recovery. Follow
the [storage procedures](../storage/storage-operations.md), [Gogs guide](../services/gogs.md)
or [HomeBox guide](../services/homebox.md) for the relevant workflow. Verify
restored data and service behavior; same-host restore is not off-host recovery.

## Proxmox maintenance and setup repair

🟢 **Labbed:** selected saved patch and CLI/auth checks; package-changing upgrades remain open.

A saved public Proxmox patch recovered the interrupted panel setup and completed
all 21 steps. Installed CLI help, an update preview and refusal under an occupied
maintenance lock passed. Before/after checks preserved guest states, running
processes, configuration, network/storage policy and existing panel auth. The
installed updater found no packages to upgrade. Follow
[Proxmox troubleshooting](../operations/proxmox-setup-troubleshooting.md) for the
root-identity and missing-command fixes; this does not qualify a fresh host
installation or the complete public password-replacement flow.

The separately authorized USB test-pool activation mounted existing ext4 by UUID,
retained the mount-point guard and passed a temporary 1 MiB write/read hash check.
It started no guests and reformatted no data. This establishes bounded availability,
not endurance or survival of a subsequent reboot with the new mount declaration.

## Shares & Data approvals and recovery

🟢 **Labbed:** earlier installed backend; current installed UI requalification remains open.

Installed HTTPS forms, a separate approval identity and actual root workers
exercised Samba account/share lifecycle, backup mirrors, PAR2 scans and targeted
verify/restore/accept. SMB permissions, retained files and credentials were checked.
Later per-task filtering, drafts and recovery-guard changes passed local regression
and Chromium/Firefox checks using simulated services; they were not redeployed for
installed-system qualification. See the [evidence boundary](evidence.md#shares-and-data)
and [operator procedure](../services/shares-data.md). The Proxmox root panel cannot
substitute for the required non-root guest operator and separate approval service.

## Offline developer checks

🟡 **Partial:** automated coverage; commands do not qualify live installations.

From a prepared development environment, choose the scope for the change:

```bash
make check-fast
make test SUITE=web
make check-ci
```

The fast loop, selected suites and full CI-equivalent run have different
coverage and costs. See [contributor checks](../agents/contributing/README.md#standard-checks)
and [browser qualification](../agents/browser-testing.md). Do not run the full
suite twice on unchanged source merely to repeat overlapping checks.

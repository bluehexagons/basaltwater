# Tested workflow examples

**Evidence through 2026-10-31.** These are representative commands and
procedures for workflows with selected live evidence. Values are fictional;
the exact commands, versions and configuration used in a private run can
differ. A successful dry run checks planning only. Read each linked guide and
use a disposable target and data before applying setup.

## Debian server and shared desktop

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

Selected Debian VM checks covered builder enrollment, Node/static and Go builds,
artifact delivery, rerun, target refresh with the builder offline and retained
application data. Public tunnel/ACME and power-loss behavior are separate.
The [single-site builder walkthrough](../examples/builds/homelab-build-test.md)
contains a fictional two-command provisioning example and HTTP verification;
the [multi-application walkthrough](../examples/builds/homelab-local-builder.md)
adds example manifests and SSH source restrictions. These adapted examples are
not a claim that those exact fictional endpoints were tested.

## Storage and service recovery

Selected checks covered Btrfs setup/rerun/reboot, Samba and Syncthing exchange,
Gogs Git/LFS, HomeBox API backup/restore and same-host guest recovery. Follow
the [storage procedures](../storage/storage-operations.md), [Gogs guide](../services/gogs.md)
or [HomeBox guide](../services/homebox.md) for the relevant workflow. Verify
restored data and service behavior; same-host restore is not off-host recovery.

## Offline developer checks

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

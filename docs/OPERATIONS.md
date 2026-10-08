# Saved configuration operations

Basaltwater stores setup arguments, host metadata, credentials, run status, and
history under the active workspace. These commands operate on saved
configurations without requiring a new setup command line each time.

## Inspect saved hosts

```bash
basaltw list
basaltw list production
basaltw info production
basaltw list --json
basaltw info --compact
basaltw cmd production
```

`list` filters by host, friendly name, or tag. `info` shows configuration and
last-run status. `cmd` reconstructs a safe, redacted setup command; webhook
targets and credential-bearing Git URLs from older saved configurations become
placeholders in terminal output. `list --json` emits recognized metadata and
sanitized setup arguments, including the same placeholders. Invalid cache
records stop the inventory with an error.

Saved setup caches, machine/setup state, and deployment-target readers reject
malformed JSON, wrong record shapes, unsafe file types, and unreadable files.
Versioned readers accept legacy unversioned records and version 1; unsupported
versions fail without overwriting data. Reads are capped at 1 MiB. Only missing
files receive defaults. Cache inventories stop on invalid records rather than
silently omitting hosts, and saves refuse to overwrite invalid existing cache
or machine/setup state.

Recovery errors name the affected path without printing its contents. The file
stays in place as a guard against accidental fresh setup. Restore a verified
backup, or explicitly move the file to a private quarantine location after
reviewing the target's actual state and deciding that fresh configuration is
appropriate. Do not delete state merely to suppress an error.

## Patch and redeploy

Tool and profile selection accepts the documented per-tool
[third-party installer channels](INSTALLER_POLICY.md). Installer provenance is
recorded privately before downloaded code executes.

Local and SSH setup execution has a four-hour deadline, including the SSH
upload. Set `BASALTWATER_SETUP_TIMEOUT` to a positive number of seconds to
override it on the controller. Source preparation occurs before this budget.
Output streams during upload and execution; a stalled input or inherited output
pipe cannot suppress the deadline. Timeout/cancellation terminates the local
process group. The SSH command also uses a target-side `timeout` with a
ten-second kill grace, so loss of the controller does not leave setup unbounded.
Already completed changes and work detached into separate services are not
rolled back. Inspect the target and its operation marker before retrying.

Setup credentials and argument files travel in a separate archive and are
staged under `/run/basaltwater-setup/payload-*` (directories 0700, files 0600).
The installed source tree contains only temporary links to that private lease.
Normal exit removes it; reboot clears `/run`. Each lease records its owner,
process ID and expiry (setup deadline plus one minute). Startup removes expired
leases only after acquiring their lock, leaving active setup untouched. Uploads
reject links, traversal, more than 10,000 entries, or over 64 MiB of payload data.
Local and SSH setup share target-side locks while replacing the runtime and
running setup. Local runtime code is copied beside the current installation
and activated by rename, preserving the old tree if staging fails. For managed
Git worktrees, uploaded deployment sources and setup payloads are staged beside
the installation and replaced with per-item backups. A failed activation
restores the previous items; incomplete recovery leaves the backup directory
named in the error for inspection. Runtime staging rejects links in the
controller source tree before changing permissions or uploading code.

The optional [restart after setup](COMMAND_LINE.md#restart-after-setup) checks
the target's reboot marker and restart capability. A detected Proxmox host
requires the guest and maintenance safety checks even when setup uses a
different profile, such as `custom_steps`. Local Proxmox restarts require the
explicit Proxmox maintenance workflow. Remote restart waits accept only a
valid kernel boot ID that differs from the pre-restart ID; malformed SSH
output cannot count as a successful reboot. Invalid machine state stops the
restart check and leaves the target running.

Deployment and gateway readiness requests use literal loopback addresses,
ignore proxy environment variables, and never follow redirects. Deployment
activation requires a local 2xx response from the configured health endpoint.

CI/CD jobs share a four-hour execution budget across checkout, install, build,
test and deployment. Each command uses the smaller of its own limit and the
remaining job time, with process-group cleanup on timeout. The executor's
systemd unit can drain multiple jobs without a shorter batch-wide timeout.
Timeout events include the stage and whether deployment had begun; completed
remote changes require inspection before retrying. Notification delivery uses
its own bounded network calls after execution ends.

Managed application, Antistatic, Gogs, CI/CD, storage-ops and maintenance timer units use
one serialized replacement transaction. Candidates are staged privately on the
unit filesystem and checked with `systemd-analyze verify` before atomic writes.
Failed writes, reloads or activation restore previous files, modes, ownership,
enablement and active state. Timer/path updates do not restart a running oneshot.
This restores unit configuration, not application data changed during startup.

A crash or failed rollback leaves `/etc/systemd/system/.basaltwater-unit-operation.json`
and blocks further replacements. Its `backup_dir` contains private `previous.json`
with the old unit text, metadata and activation states. After confirming the
original setup process has stopped, restore those files (remove units recorded
as null), reload systemd, and restore the recorded enabled/active states. Verify
the services before archiving the marker and backup privately; keep the stable
`.lock` file in place. Do not clear the marker merely to force a retry.

Use [transaction recovery](TRANSACTION_RECOVERY.md) for phase-specific setup,
release and unit recovery. Completed operations retain a private last-result
record beside the marker, including phase history and the rollback outcome.

Package, service, and user probes have a 15-second deadline. A timeout or
unavailable probe raises an explicit unknown-state error and stops dependent
setup instead of treating the package, service, or user as absent. Repair the
reported command before retrying. These read-only probes also run in dry-run
mode; optional diagnostics must explicitly catch `ProbeError` if they can
continue without the answer.

`patch` merges targeted options into the saved configuration and executes the
remote setup flow:

```bash
basaltw patch production --ssl --ssl-email admin@example.com
basaltw patch production --deploy api.example.com https://github.com/user/api.git
```

Use `deploy` to rerun saved configurations:

```bash
basaltw deploy production
basaltw deploy production --yes
```

Some capabilities have narrower fast paths. Use
[Samba share updates](SAMBA_SHARES.md#fast-share-only-updates) to update Samba
without running unrelated setup work.

## Recall and reconstruction

```bash
basaltw recall example.com admin
basaltw recall example.com admin --key ~/.ssh/id_ed25519
basaltw reconstruct
```

`reconstruct` analyzes the current host; `recall` targets a remote host.
If the remote tool is missing, recall uploads source into an exclusively created
`/tmp/basaltwater-recall.*` directory and runs reconstruction there. It does not
replace or populate `/opt/basaltwater`. The remote command has a 60-second
deadline and a five-second termination grace; the local SSH process group has
a 120-second deadline. Remote hosts need `timeout`, `base64`, `tar`, and Python 3.
Connection/probe failures abort before upload. Normal completion, extraction
failure, and handled signals remove temporary source. A hard kill or power loss
may leave the temporary directory; inspect it after confirming the recall
process has stopped, then remove it. No installation activation needs rollback.

## Remove saved configurations

Removal affects workspace metadata only; it does not uninstall software from a
target:

```bash
basaltw rm old-server
basaltw rm old-server --yes
```

## Interactive shell

```bash
basaltw shell
```

Useful commands include `list`, `info`, `cmd`, `new`, `setup`, `deploy`, `rm`,
`recall`, `workspace`, and `proxmox`. Startup commands can be placed in
`~/.basaltwaterrc`; history is stored at `~/.local/share/basaltwater/shell_history`.

## Testing

```bash
python3 -m unittest discover -s tests
./run_tests.py
./run_tests.py --suite smoke
./run_tests.py --list-suites
./run_tests.py --list-categories
./run_tests.py --durations 20
```

The default runner captures test stdout/stderr and reports only the unittest
failure or error report. Use `--show-output` when diagnosing a noisy failing
test; use `-v` when you deliberately want every test name and live task log.
Setup command echoes are also quiet by default because each setup step already
reports progress. Set `BASALTWATER_VERBOSE=1` to echo every command, and use a
setup dry run to validate the configuration and preview its handoff without
applying the target setup. It does not execute or enumerate every target-side
command. The dry-run plan and live progress use the same step sequence,
including deployment, Cloudflare, Samba, SMB mounts, storage operations, and
final state saving. A failure in any of these phases names its step in the
setup operation marker and failure notification.

Live Proxmox and other expensive tests are opt-in. See
[`tests/expensive_support.py`](../tests/expensive_support.py) and
[Proxmox workflows](PROXMOX.md) for environment-specific requirements.

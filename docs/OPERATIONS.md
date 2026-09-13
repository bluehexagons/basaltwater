# Saved configuration operations

infra-tools stores setup arguments, host metadata, credentials, run status, and
history under the active workspace. These commands operate on saved
configurations without requiring a new setup command line each time.

## Inspect saved hosts

```bash
infra-tools list
infra-tools list production
infra-tools info production
infra-tools list --json
infra-tools info --compact
infra-tools cmd production
```

`list` filters by host, friendly name, or tag. `info` shows configuration and
last-run status. `cmd` reconstructs a safe, redacted setup command.

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

Local and SSH setup execution has a four-hour deadline, including the SSH
upload. Set `INFRA_TOOLS_SETUP_TIMEOUT` to a positive number of seconds to
override it on the controller. Source preparation occurs before this budget.
Output streams during upload and execution; a stalled input or inherited output
pipe cannot suppress the deadline. Timeout/cancellation terminates the local
process group. The SSH command also uses a target-side `timeout` with a
ten-second kill grace, so loss of the controller does not leave setup unbounded.
Already completed changes and work detached into separate services are not
rolled back. Inspect the target and its operation marker before retrying.

Setup credentials and argument files travel in a separate archive and are
staged under `/run/infra-tools-setup/payload-*` (directories 0700, files 0600).
The installed source tree contains only temporary links to that private lease.
Normal exit removes it; reboot clears `/run`. Each lease records its owner,
process ID and expiry (setup deadline plus one minute). Startup removes expired
leases only after acquiring their lock, leaving active setup untouched. Uploads
reject links, traversal, more than 10,000 entries, or over 64 MiB of payload data.

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

Package, service, and user probes have a 15-second deadline. A timeout or
unavailable probe raises an explicit unknown-state error and stops dependent
setup instead of treating the package, service, or user as absent. Repair the
reported command before retrying. These read-only probes also run in dry-run
mode; optional diagnostics must explicitly catch `ProbeError` if they can
continue without the answer.

`patch` merges targeted options into the saved configuration and executes the
remote setup flow:

```bash
infra-tools patch production --ssl --ssl-email admin@example.com
infra-tools patch production --deploy api.example.com https://github.com/user/api.git
```

Use `deploy` to rerun saved configurations:

```bash
infra-tools deploy production
infra-tools deploy production --yes
```

Some capabilities have narrower fast paths. Use
[Samba share updates](SAMBA_SHARES.md#fast-share-only-updates) to update Samba
without running unrelated setup work.

## Recall and reconstruction

```bash
infra-tools recall example.com admin
infra-tools recall example.com admin --key ~/.ssh/id_ed25519
infra-tools reconstruct
```

`reconstruct` analyzes the current host; `recall` targets a remote host.
If the remote tool is missing, recall uploads source into an exclusively created
`/tmp/infra-tools-recall.*` directory and runs reconstruction there. It does not
replace or populate `/opt/infra_tools`. The remote command has a 60-second
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
infra-tools rm old-server
infra-tools rm old-server --yes
```

## Interactive shell

```bash
infra-tools shell
```

Useful commands include `list`, `info`, `cmd`, `new`, `setup`, `deploy`, `rm`,
`recall`, `workspace`, and `proxmox`. Startup commands can be placed in
`~/.infra_toolsrc`; history is stored at `~/.local/share/infra_tools/shell_history`.

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
reports progress. Set `INFRA_TOOLS_VERBOSE=1` to echo every command, and use a
setup dry run to validate the configuration and preview its handoff without
applying the target setup. It does not execute or enumerate every target-side
command.

Live Proxmox and other expensive tests are opt-in. See
[`tests/expensive_support.py`](../tests/expensive_support.py) and
[Proxmox workflows](PROXMOX.md) for environment-specific requirements.

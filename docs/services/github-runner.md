# Debian GitHub Actions runner

`server_github_runner` installs GitHub's official persistent runner for one
private `github.com` repository on native Debian 13/Trixie x86-64 with systemd.
The implementation has offline checks; clean-VM, reboot and GitHub-connected
qualification remain required before production use. Windows, WSL, containers,
organization registration and ephemeral runners are outside phase 1.
See the [planned runner improvements](github-runner-improvements.md) for
deferred registration, organization scope, and panel token entry.

## Install

Use a dedicated CI VM. Allowed jobs execute trusted code with the runner
account's access, including its reconnect credentials. Keep production secrets
and unrelated workloads off this host. The local gate does not turn a
persistent runner into a sandbox for hostile code.

In the private repository's **Settings → Actions → Runners → New self-hosted
runner**, obtain a repository registration token. It expires after one hour.
Place only that token in a private local file (`chmod 600`), then run:

```bash
basaltw setup server_github_runner ci.example.net operator \
  --machine vm --ci-repository owner/repository \
  --ci-allowed-user alice --ci-allowed-user bob \
  --ci-label debian-ci --ci-registration-token-file /private/runner-token
```

The operator account is separate from `basaltwater-runner`. The service account
has a locked login shell, a dedicated non-root primary group, no supplementary groups, no sudo and no Docker
access. Basaltwater installs neither Docker nor the other webhook-based CI
services. The runner is pinned to official `actions/runner` **2.338.0**, Linux
x64, with SHA-256 verification before extraction. Native runtime libraries and
CA certificates are installed from Debian packages. Provide outbound HTTPS
access to GitHub and its runner/action download endpoints.

The token crosses SSH in the private expiring setup payload, outside the
installed source archive. Registration runs unprivileged and receives the token
through a short-lived process environment. Neither token nor local source path
is included in saved setup commands, configuration, unit files or logs. The
target token payload is removed on success and failure; remove your original
local token file after use. No long-lived GitHub API token is required. The
runner-generated reconnect credentials remain readable by its service account
and root under the protected application directory.

Add `--dry-run` to inspect the controller plan without a token or target
mutation. A target-side dry run also verifies the Debian release and architecture.
Optional `--web-panel PORT` uses the existing authenticated panel installation
and TLS settings; see [web panel setup](web-panel.md).
The runner profile rejects other workloads, deployments, tunnels, file sharing,
storage jobs, privilege brokers and maintenance agents. Enabling its panel
does not install maintenance-agent services. Configure those workloads on
separate hosts.

## Job identity and gate

The authoritative policy is root-owned
`/etc/basaltwater/github-runner.json`. Change the allowlist by repeating setup
with the **complete** desired `--ci-allowed-user` set and the same repository and
labels. An existing registration needs no new token for an allowlist change.
The panel displays this policy read-only. Repository and label changes require
the deliberate replacement procedure below.
An allowlist update stops the listener and its current job before publishing
policy, then restarts the service. Drain trusted jobs before repeating setup.

| Event | Identity required |
| --- | --- |
| `push` | GitHub actor and webhook sender must match an allowed human user; deleted refs are denied |
| `workflow_dispatch` | GitHub actor and webhook sender must match an allowed human user |
| `pull_request` | PR author and the matching actor/sender must all be allowed human users; only `opened`, `reopened`, `synchronize`, with both head and base in this private repository |

Repository names and user logins compare without case sensitivity. The hook
requires a private repository payload matching the configured repository and
the `github.com` server context. Commit author names/emails grant no access.
Missing, malformed or mismatching identities, forks, public repositories, bots,
reruns (`GITHUB_RUN_ATTEMPT` other than `1`) and every other event are denied.
This includes `pull_request_target`, schedules, workflow/repository dispatch
events other than manual `workflow_dispatch`, and `workflow_run`.

The root-owned `ACTIONS_RUNNER_HOOK_JOB_STARTED` hook executes on the host before
workflow steps. Any denial, unreadable event/policy, Python startup failure or
telemetry write failure kills the parent `Runner.Worker`. This also prevents
steps with `if: always()` from running after a denied gate. The listener remains
available for later jobs; GitHub may describe a denial as a lost job worker.
The service refuses startup if critical policy/application files become
non-root-owned, writable by another account, symlinked, unreadable, or the wrong
file type, or if its binaries cannot execute as the service account.

The application and hook live outside job-writable paths. The service uses a
read-only system view with writes limited to
`/var/lib/basaltwater-github-runner`, private temporary storage and no privilege
escalation. In-place runner self-update is disabled to keep the application and
hook environment immutable to jobs.

This is a local guard after GitHub assigns a job. It does not hold jobs for
approval, change GitHub queue policy, or authorize PRs. GitHub's
[hook ADR](https://github.com/actions/runner/blob/v2.338.0/docs/adrs/1751-runner-job-hooks.md)
explicitly excludes security-policy guarantees. Action downloads and runner
setup can occur before the hook. Keep workflow changes and repository access
trusted and complete the qualification below before sending jobs here.

## Health and panel

```bash
sudo systemctl status basaltwater-github-runner.service
sudo systemctl is-enabled basaltwater-github-runner.service
sudo -u basaltwater-runner /usr/bin/python3 -I /opt/basaltwater/common/service_tools/github_runner_gate.py --check
sudo journalctl -u basaltwater-github-runner.service --since today
```

The `--check` entry point safely validates the local installation without
running a job hook or submitting a GitHub job. A successful check exits zero.

The service is enabled at boot and restarts its listener after failure. Local
`active` status proves only that the service runs. Confirm **Idle/Online** in
GitHub's repository runner settings and inspect `_diag` output under
`/var/lib/basaltwater-github-runner/diag` for connectivity problems.

The Debian panel's **GitHub runner** page appears when the fixed policy and
unit are installed. **Load runner status** reads repository, labels, allowlist,
pinned version, local service state, boot enablement and up to 100 recent gate
decisions. It never reads runner credentials or webhook payloads. The outcome
file is runner-writable operational telemetry, not an independent audit trail.
Collection errors remain visible; opening the page collects nothing until you
press Load. Registration, replacement, allowlist editing, queue and lifecycle
controls are deferred. The existing agent template **Review GitHub Actions
failures** serves a different purpose.

## Updates, deregistration and recovery

GitHub requires timely runner updates. Basaltwater deliberately uses
`--disableupdate`; monitor GitHub's runner notices and the official
[runner releases](https://github.com/actions/runner/releases). Use a Basaltwater
revision with a reviewed version/checksum pin and qualified gate behavior.
Do not make the application writable to enable automatic updates.

For upgrade, replacement, repository/label changes or lost registration:

1. Stop assigning jobs to this runner and let any trusted job finish. Stop and
   disable `basaltwater-github-runner.service` as root.
2. Remove the runner in the repository's **Settings → Actions → Runners**. This
   revokes its GitHub registration without copying an API credential onto the
   host. A replacement uses a fresh one-hour registration token, not a removal
   token or PAT.
3. Quarantine the old `/opt/basaltwater-github-runner` application,
   `/etc/basaltwater/github-runner.json` policy and
   `/var/lib/basaltwater-github-runner` data under a root-only recovery directory.
   Retain diagnostics if needed. Treat the application archive as secret because
   it contains reconnect credentials. Do not reuse workspace contents after a
   suspected compromise.
4. Rerun setup with the reviewed Basaltwater revision, full repository/allowlist/
   label settings, and fresh registration token file. The dedicated account
   can remain; setup refuses accounts with unexpected privileges or home/shell.
5. Verify service boot enablement, GitHub connectivity and the smoke/gate checks
   before restoring traffic. Delete the root-only quarantine when recovery no
   longer needs it.

An interrupted install is deliberately preserved for inspection. An application
without a valid managed policy is refused; quarantine it before retrying.
If registration completed in GitHub before local installation failed, remove
that stale GitHub entry first. A failed allowlist/service reconfiguration leaves
the runner stopped or failing its preflight; inspect the managed unit recovery
marker and [transaction recovery](../operations/transaction-recovery.md) before
retrying. A unit rollback does not undo policy or GitHub registration changes.

For full removal, perform steps 1–3, then remove the fixed unit with the normal
systemd daemon reload. Remove `/etc/basaltwater/github-runner/` only after the
service is gone. Remove the dedicated account only when no process uses it.

## Qualification

On a disposable, clean Debian 13 x86-64 VM and a private test repository:

1. Install without Docker; confirm only the dedicated account owns job data,
   has no supplementary groups, cannot write the policy/application/hook or
   access an operator's home, and has no sudo or Docker socket access.
2. Confirm the runner is online. Reboot and confirm it returns online with the
   service enabled. Copy this Docker-free workflow into the private test
   repository's `.github/workflows/runner-qualification.yml` on its default
   branch, then create a `runner-qualification` branch from it:

   ```yaml
   name: Debian runner smoke
   on:
     workflow_dispatch:
     push:
       branches: [runner-qualification]
     pull_request:
       branches: [runner-qualification]
       types: [opened, reopened, synchronize]
   permissions: {}
   jobs:
     smoke:
       runs-on: [self-hosted, Linux, X64, debian-ci]
       timeout-minutes: 5
       steps:
         - name: Smoke canary
           if: always()
           run: |
             test "$(id -un)" = basaltwater-runner
             grep -q '^ID=debian$' /etc/os-release
             printf 'RUNNER_SMOKE_STEP_EXECUTED\n'
   ```

3. Dispatch the workflow, push to `runner-qualification`, and open/update a
   same-repository PR targeting that branch. For every supported trigger,
   deliver allowed and denied actors; for PRs also
   vary the author independently. Confirm denied jobs never print the canary,
   including `always()` steps and action pre/post steps. Test malformed/missing
   identities offline with `make test TEST=tests.test_github_runner`; also run
   the deployed hook against bounded malformed event fixtures in a disposable
   worker process. Never invoke a failing hook from an administrator's shell:
   its denial contract kills its parent.
4. Verify public/fork/bot/unsupported-event/rerun rejection, unreadable or invalid
   policy, missing Python/gate, telemetry failure and restored service recovery.
   Confirm workflow environment cannot override the hook or identity context.
5. Load the panel as the separate operator. Check configured policy, failed and
   successful service states, allowed/denied/malformed outcomes and empty or
   corrupt history. Confirm there are no credentials, payloads or policy-edit
   controls. Repeat after the documented upgrade and re-registration procedure.

Offline tests validate identity rules, protected credential staging, service
configuration and a disposable worker/canary boundary. They do not establish
GitHub delivery behavior or reboot persistence. Keep the phase's production
qualification open until these checks have recorded evidence.

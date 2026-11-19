# Installed maintenance jobs and update policy

[Documentation index](maintenance.md)

## Installed Jobs

| Unit | Schedule | Scope |
|------|----------|-------|
| `security-monitor.timer` | Every 15 minutes | VM, bare-metal, and Proxmox host security events; XRDP certificate health when configured |
| `auto-update-apt.timer` | Daily at 06:00 | Security-enabled setups |
| `auto-restart-if-needed.timer` | Daily at 02:00 and 30 minutes after boot | Machines that can restart themselves, including LXC; saved policy chooses restart or deferral |
| `auto-update-node.timer` | Sunday at 03:00 | Setups with Node.js/nvm |
| `auto-update-uv.timer` | Sunday at 05:00 | Setups with Python and uv |
| `auto-update-gogs.timer` | Sunday at 05:30 | Setups with Gogs |
| `auto-update-homebox.timer` | Sunday at 06:00 | Enabled HomeBox services |
| `auto-update-godot.timer` | Sunday at 06:30 | Setups with Godot; also reconciles selected Godot bundles |
| `codex-auth-maintenance.timer` | Daily and 15 minutes after boot | Setups with Codex; refreshes file-backed ChatGPT auth only when its cached token is approaching expiry or refresh metadata is overdue |
| `cleanup-maintenance.timer` | Sunday at 03:30 | Security-enabled setups |
| `user-cache-maintenance.timer` | Daily at 07:00 | Security-enabled setups with a non-root setup user |

Container capabilities are respected. OCI containers cannot restart the system,
and security monitoring, auditd, AppArmor, and kernel-level setup are skipped
where the container cannot safely provide them. An unprivileged LXC can request
an in-container restart, but still does not receive host-kernel controls.
Its restart timer is installed without the kernel post-install hook. Disabling
automatic restarts leaves the timer available to record policy deferrals; the
saved forced-restart deadline remains independent (`0` disables that deadline).

Inspect a job and its recent output with:

```bash
sudo systemctl status auto-update-apt.timer
sudo systemctl list-timers --all \
  '*auto-*' '*security-monitor*' '*cache-maintenance*' '*codex-auth*'
sudo journalctl -u auto-update-apt.service -n 100 --no-pager
```

Scheduled services use systemd's journal as their console sink. When systemd
provides a journal stream, Basaltwater suppresses the additional syslog sink so
each event appears once in `journalctl`.

Required host timers are verified during setup. Failure to reload, enable,
start, or confirm the security-monitor, APT-update, cleanup, user-cache,
restart, uv-update, or Gogs-update timer stops setup. Missing uv or Gogs
prerequisites and dry runs still skip unit installation successfully.
When the replacement APT timer cannot be verified,
Debian's existing APT timers and legacy unattended-upgrades policy remain
unchanged. After replacement verification, setup inspects all competing units,
then stops and disables the distro timers before the unattended-upgrades service.
Failed inspection, retirement, or final inactive/disabled verification stops
setup and retains the legacy policy files. Earlier successful stops are not
rolled back, and the verified replacement remains available. Inspect and repair
the named unit, then rerun setup; only a verified handoff removes legacy policy.
`basaltw agent doctor --capability host` also reports installed Node,
Godot, and Codex authentication timers, while omitting those optional jobs on
VMs where their tools were not selected.
The doctor and the web panel's scheduled-jobs view report both calendar and
boot or interval deadlines when a timer has both. The earlier deadline triggers
the job; interval estimates include systemd's reported randomized delay. Use
`systemctl list-timers --all` for the exact next-run timestamp.

## Update Policy

Kernel-capable setups install `/etc/kernel/postinst.d/basaltwater-reboot-required`.
The standalone hook records `/run/reboot-required` and the package name in
`/run/reboot-required.pkgs` when a different kernel is configured. It does not
depend on unattended-upgrades and preserves existing package hooks and markers.
Rerun setup to install or repair it; installing the hook does not itself reboot
the machine or recreate a marker for an earlier update.
Proxmox host setup installs this hook before its package upgrade so even a first
setup records newly installed kernels for `--restart-if-needed`.

For hosts that already have an update without a marker, setup reports
newer fully configured Debian/Ubuntu `linux-image-*` or Proxmox kernel packages
with boot images matching the running kernel flavour (for example `amd64`,
`cloud-amd64`, `generic`, or `pve`). Versions use Debian's comparison rules.
The scheduled
restart check also sends a warning to review boot selection and kernel pins.
This package comparison is advisory only and cannot trigger an automatic or
forced restart: an installed image does not establish the next boot selection.
It continues to warn while a newer installed kernel is present, including when
the older running kernel is deliberately pinned. Custom kernels without a
recognized version/flavour package name do not use this fallback. Marker-based
restarts retain the saved restart policy.

Restart reminders are eligible once per local calendar day so timer jitter
does not skip alternate days. Failed deliveries remain eligible on the next
check; with multiple targets, successful targets can receive a duplicate when
another target failed. Empty or level-filtered target lists are logged and do
not count as delivery. Delivery failures remain best effort and do not change
the restart policy. Inspect the restart service journal for the deferral reason
and delivery result; service success alone does not prove notification delivery.

Restart checks require a saved setup policy and refuse missing or malformed
policy instead of coercing strings or booleans
into restart authorization or deadlines. Deferral history in
`/var/lib/basaltwater/auto_restart_state.json` is read as a bounded regular JSON
file. Invalid timestamps, symlinks, special files, unreadable files, and files
over 1 MiB stop the check, report failure, and retain the file without scheduling
a restart or resetting history. Restore a verified backup or explicitly
quarantine it after reviewing the force deadline and notification history.
Only a missing history file starts fresh.

The restart job takes the same local lock as target setup. If setup or another
protected maintenance job is running, it defers without scheduling a restart,
even when a forced deadline has passed. This also protects first setup: timers
may become due immediately when activated, but restart authorization is not
available until successful setup has saved its policy.

[HomeBox](../services/homebox.md) retains its installed version on ordinary setup reruns.
Select an immediate reviewed upgrade with `--homebox-version TAG`; the weekly
HomeBox timer also resolves the newest stable upstream release. Both paths use
the same stopped-service recovery archive, activation, health checks, and
rollback before reopening ingress. HomeBox retains the newest four automatic
pre-update archives without touching manual backups. Keep a complete backup
off-host for recovery from host loss or a failed rollback.

APT uses the Basaltwater updater instead of competing distro unattended-upgrade
timers. On Debian outside Proxmox, it first refreshes the installed release's
official security suite in a temporary APT index directory using the Debian
archive keyring. It installs security updates and any required dependencies
available in that suite without removing packages. Updates requiring packages
from another suite can wait for the regular upgrade. An auxiliary repository
outage or a proposed removal in the regular upgrade cannot block this security
pass.

The regular pass repairs CD-ROM-only entries and stale official Debian suites
before refreshing all configured sources and running `dist-upgrade --no-remove`.
Each pass requires a successful index refresh before upgrading and preserves
existing local dpkg configuration files. The job reports a failure in either
pass even if the other succeeds. It does not run `autoremove`. Debian's timers
are disabled only after the replacement timer is enabled, started, and verified.
On Proxmox hosts the job validates supported stable repositories without rewriting them,
and checks local node health before and after upgrading. Proxmox VE 9.x on
Debian trixie accepts stable point updates, including new kernel dependencies;
the installed version and refreshed `pve-manager` candidate must remain within
9.x. A minor-version change does not stop the job, while major release upgrades
remain operator-managed. Update Basaltwater on the controller and rerun host
setup to deploy this policy to existing nodes. Each Proxmox setup rerun also
performs the upgrade immediately, without needing `--refresh-packages` or
waiting for the daily timer. Setup runs under its existing target lock rather
than starting the scheduled updater, which would defer for that same lock.
Both paths verify that the installed `pve-manager` reached APT's candidate and
report a failure if it did not. An upgrade failure stops setup before its
optional `--restart-if-needed` workflow. HA and Ceph require
operator-managed updates; see [Proxmox host safety](proxmox.md#host-safety-defaults).
Inactive guest storage is logged as a warning and does not block package updates
or kernel-only cleanup. Storage inventory failures, root/boot capacity problems,
core-service failures, quorum loss, active tasks, and locked guests still block
those jobs. Scheduled reboot checks and ordinary maintenance audits retain
strict storage checks.
APT updates, restart checks, and cleanup's APT phase acquire the local setup
lock, deferring when setup or another maintenance job already holds it.

Each uv self-update or tool-upgrade command has a 30-minute limit. A timeout
or executable launch failure is logged and notified just like a nonzero exit,
and a failed self-update prevents the tool-upgrade phase from running.

Node.js and uv use a conservative default policy:

- Node.js selection, explicit installation, setup and maintenance require
  `^24.22.1 || ^26.12.1`. Setup and maintenance select the newest allowed LTS
  release from the bounded official Node release index and pin NVM's default
  to that exact installed version. Maintenance also installs the newest
  eligible release on each older supported LTS major already present in NVM.
  The NVM default still follows the newest allowed LTS and project pins remain
  installed. An installed release below the policy floor can trigger a newer
  install on its major. Global tools migrate only from policy-eligible sources.
  Discovery failure stops before runtime,
  alias, package or cleanup changes; an upstream odd or future major does not
  hide updates on the allowed lines. Explicit aliases and partial pins resolve
  to an exact allowed release intersecting project engines before installation.
  Updating an already-installed eligible Current track requires
  `BASALTWATER_NODE_UPDATE_LATEST=1` in the Node updater's
  service environment; a project's newer installed major never opts the host
  into that track. Only supported installed sources on the target's major
  qualify, and maintenance refuses to downgrade a newer installed source.
  When all allowed majors are LTS there is no separate Current track.
  Global npm package versions are preserved when a supported runtime on the
  same major changes. Legacy runtimes on other lines are retained without
  migrating their global tools. Cleanup and rollback may remove only runtimes
  created and marked by automatic maintenance. Preexisting versions, missing or invalid ownership
  records, and project runtimes protected by `basaltw node install` are retained.
  If package migration fails (including failed npm inventory commands or
  malformed inventory), the updater restores the previous default and removes
  the incomplete target only when it owns that runtime. Project protection and
  removal/migration share a per-user lock; a runtime claimed by a project during
  an update keeps its existing global tools. A busy or unsafe lock defers the
  operation with an error instead of waiting or removing an unprotected target.
  Maintenance loads nvm without activating the default runtime, so an LTS
  alias pointing to a newly released, not-yet-installed version cannot block
  installation or rollback. Opted-in global upgrades explicitly select the
  validated exact LTS target under the ownership lock; project and preexisting
  targets retain their tools even with that opt-in. Nonzero nvm commands follow
  the updater's error reporting and rollback paths rather than aborting with an
  uncaught command exception.
  A later setup rerun verifies the promised
  Node/npm/PNPM baseline by running each version command and repairs it if a
  manual change left one unavailable or unusable. Setup fails visibly when the
  repaired baseline still cannot run instead of leaving a false-success state.
  Setup verifies exact runtime activation before alias or npm changes.
  Deployment preparation and build execution use the shared Node CLI as the
  existing build user; old or odd pins fail before project code runs. Host
  development diagnosis checks the observed active/default Node version and
  reports unsupported defaults as unhealthy even when another installed
  runtime satisfies policy. Ownership inventory still recognizes older
  releases so they remain protected.
- uv updates the uv executable itself, but uv-managed tools are upgraded only
  when ecosystem upgrades are enabled.
- Gogs validates a downloaded release before activation and rolls back the
  previous release when post-update commands or restart fail.
- Godot follows the newest stable upstream release without the general
  seven-day freshness delay. The publisher-provided SHA-256 is verified before
  a versioned release is activated through the system-wide launchers. Its web
  bundle installs matching verified export templates for registered users;
  the publishing bundle updates verified Butler releases and invokes the
  user-owned SteamCMD self-updater.

GitHub CLI is installed from its APT repository and therefore follows the APT
job. Explicitly selected Codex CLI, Claude Code, and OpenCode installations
are reconciled by each setup run as the target user; their rebuildable caches
are covered by the separate user maintenance job. T3 Code's per-user service
is also checked and updated whenever its interface is selected. Use the
client's **Update server** action or the npm 12-safe host command in
[`t3-code.md`](../agents/t3-code.md) when an update should be performed outside setup.
Run
`basaltw agent update --dry-run` and then `basaltw agent update` as the
account that owns the terminal tools (for example,
`sudo -u agent -H basaltw agent update --tool codex`) for a deliberate
terminal-agent vendor update with before/after verification, a retained prior
executable, automatic rollback after a broken update, and a private audit
record. After a non-dry-run update, Basaltwater also records a redacted
tools-and-host readiness result, including T3 Code when it is installed. The
update command exits nonzero if that audit is unhealthy or cannot be saved.
The update environment is reset to that account's home so a caller's working
directory and PATH cannot redirect the vendor installer. Setup still uses the
official installer when a selected command is missing, then runs the same
verified update path on subsequent setup steps.

Codex-enabled setups separately install a non-root authentication maintenance
timer. It inspects only redacted freshness metadata from `~/.codex/auth.json`.
A current login, an API-key login, or a machine that has not signed in is a
no-op. When a ChatGPT access token is near expiry, its last refresh is overdue,
or its freshness cannot be established, the job asks Codex's own app server to
run the managed refresh-token flow and then verifies the file is current. It
does not start a model turn, copy tokens, or log account data. Invalid files,
unsafe ownership or permissions, missing refresh tokens, protocol failures,
and credentials that remain stale make the service fail visibly. File-backed
ChatGPT authentication remains per-machine state; the timer does not make a
copied login safe to share across concurrently running VMs.
The service gets a private temporary directory, no privilege-gaining path, a
read-only home and system view, and write access only to that user's `.codex`
directory. It still needs outbound network access to refresh with the provider.
An explicit setup rerun also invokes this check once before updating the
managed agent tools, so an infrequently started VM can refresh renewable
credentials without waiting for the next timer trigger.

From the control system, the equivalent remote workflow is
`basaltw agent update HOST USER --dry-run` followed by the same command
without `--dry-run`; use `--tool` to narrow either operation. Agent-enabled
setups install the target-user launcher needed for local VM maintenance as
part of the normal reconciliation.

After a deliberate T3 Code update or host reboot, run and persist the
composite readiness check as the target account:

```bash
basaltw agent doctor --capability t3code --capability host --record
basaltw agent doctor --last-record --json
```

The private mode-`0600` record contains the boot ID and redacted aggregate
results, not paths, Git identity, credential contents, repository contents, or
process details. Reading a healthy record from a previous boot still exits
nonzero, which prevents old evidence from being mistaken for a post-reboot
check. The same `--record` and `--last-record` options work with the remote
`HOST USER` doctor form.

For manual terminal-agent upgrades, use the managed update command above;
its vendor mechanism and account boundaries are described in the
[CLI reference](../reference/command-line.md#agent-host-flags).

Set `BASALTWATER_ECOSYSTEM_AUTO_UPGRADE=1` in the relevant service environment
to allow global npm packages and uv-managed tools to advance. The default
is `0`.

Dependency-resolving npm and uv commands, and GitHub release selection, prefer
artifacts at least seven days old. Change the policy with
`BASALTWATER_DEPENDENCY_MIN_AGE_DAYS`; use `0` to disable the freshness delay.
The deployment flag `--deploy-latest DOMAIN_OR_PATH GIT_URL` explicitly bypasses
the deployment freshness policy for that repository.

Privileged Go, Godot, Butler, Gogs, HomeBox, and managed binary downloads use private,
randomly named temporary directories. They never download through a
predictable public `/tmp` filename that another local account could replace
with a symbolic link.
Release tags are restricted to safe path components, release assets require
credential-free HTTPS URLs without protocol-downgrade redirects, and Go
archives require the official feed's full SHA-256 digest before extraction.

Godot, Butler, Gogs, and Antistatic release-state reads refuse malformed JSON,
empty objects, symlinks, special files, and files over 1 MiB. Invalid metadata is
retained for recovery rather than treated as a fresh installation. Antistatic
binary downloads are staged on the destination filesystem so publication is
an atomic rename even when `/tmp` is on another filesystem.

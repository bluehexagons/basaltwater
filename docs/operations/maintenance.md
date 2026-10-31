# Recurring Maintenance

Setup flows install recurring jobs as managed systemd units. The jobs are
enabled and restarted after their unit files are written, so rerunning setup
immediately applies a changed schedule. Replacement units are staged atomically
so an existing working timer is not removed before its replacement is ready.
Randomized delays are used where appropriate to avoid many hosts running the
same job at the same instant.

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
timers. It runs `apt-get update` and a non-removing distribution upgrade; it
does not run `autoremove` or automatically remove packages. Before each
scheduled update, Basaltwater repairs CD-ROM-only entries and stale official
Debian suites using the installed release codename. The distro timers
are disabled only after the replacement timer is enabled, started, and verified.
Setup and scheduled APT refreshes treat any repository-index error, including
transient failures, as a failure and do not proceed to the upgrade with stale
indexes. The scheduled job reports the failure through its notification targets.
An APT executable launch failure also follows that reporting path. On Proxmox
hosts the job validates supported stable repositories without rewriting them,
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
  to that exact installed version. Discovery failure stops before runtime,
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

## Cleanup and State Safety

An explicit setup rerun runs the same bounded system cleanup and user-cache
cleanup jobs once, after the selected installation and reconciliation steps.
This gives an infrequently started VM a maintenance opportunity without
waiting for its next timer trigger. Successful cleanup stays compact in setup
output; failures remain visible and the persistent timer retries them later.
Dry-run setup only shows the planned step and does not execute cleanup.

`cleanup-maintenance` removes disposable APT caches, rotates journals before
enforcing both the `100M` size ceiling and a 30-day age ceiling, invokes the
system logrotate policy, removes recognized crash-report files older than 30
days, and removes only exact basaltwater-owned temporary artifact names older
than seven days in `/tmp` and `/var/tmp`.

Cleanup commands use the shared process-group streaming supervisor with a
ten-minute deadline and a combined 256 KiB stdout/stderr capture limit, while
individual inventory checks can use shorter deadlines. Timeout, output overflow
or interruption terminates the owned group, including pipe-holding descendants
after the leader exits. Group cleanup can add two five-second grace periods;
descendants that create their own sessions require the managed service's
control-group stop semantics. Failed commands are reported, never immediately
replayed to recover output. The maintenance-review wrapper separately bounds
its protocol to 16 KiB and its existing model runtime plus 30 seconds; it keeps
failed/interrupted attempts charged and omits raw worker errors.

The cleanup job also runs noninteractive `apt-get autoremove --purge` on APT
hosts other than Proxmox. On those hosts this removes packages APT has marked
as unused, including superseded kernels, while APT's kernel-retention policy
protects kernels it considers required. It deliberately retains configuration
remnants for packages that were already removed: blanket residual purges can
run maintainer hooks for service names now owned by installed replacement
packages. The APT phase holds the local setup/maintenance lock and first audits
`dpkg` state; incomplete packages prevent its package cleanup without an
automatic repair. When the lock is busy, including setup's child cleanup run,
the job defers APT and continues non-package cleanup and capacity checks.
On physical machines, VMs, and Proxmox hosts, cleanup returns unused blocks after
deletion when discard is supported. It defers to an active native `fstrim.timer`
instead of running a duplicate trim; containers skip this host-level operation.

Before autoremove, kernel-capable non-container hosts other than Proxmox return
obsolete manual kernel selections to automatic APT management. This covers
versioned Debian/Ubuntu kernel images matching the running flavour. Only versions
older than the running kernel qualify; the newest older image is also excluded
from these metadata changes as a fallback.
APT holds and `NeverAutoRemove` rules remain respected. Default-kernel and
helper packages, unversioned Debian/Ubuntu tracking metapackages, other flavours,
and unknown custom kernel names are left alone. APT determines actual removals
from its dependency and retention rules; Basaltwater never deletes boot images
directly.

Proxmox has automatic kernel-only cleanup instead of broad autoremove. It
selects obsolete installed kernel images and old series metapackages, whether
manually or automatically installed. It preserves the running kernel, all newer
kernels, the newest older image as a fallback, package holds, and every APT
`NeverAutoRemove` protection, including Proxmox's generated boot-selection rules.
It also reads the current native boot list to preserve manual selections and
permanent or next-boot pins even before those APT rules are refreshed.
Default-kernel and helper packages are never selected. The job requires a
supported healthy node under the same local maintenance lock used by updates;
HA, Ceph, core package holds, and failing health checks defer removal to operators.
It simulates an explicit kernel purge and refuses any dependency removal,
installation, or upgrade outside the selected kernels. Before dpkg runs, an
APT hook rechecks both the actual package actions and kernel-retention policy
under APT's locks. It leaves kernel APT marks and explicit boot-tool selections
alone; standard kernel package hooks maintain the boot files.

Rerun setup to deploy this behavior. Setup's child cleanup defers the APT phase
while setup owns the maintenance lock; the weekly timer handles later runs.
Preview the manual selections on other hosts without changing anything:

```bash
cd /opt/basaltwater
sudo /usr/bin/python3 -m lib.kernel_cleanup
```

On Proxmox, include automatic packages to preview the kernel-only removal
candidates:

```bash
sudo /usr/bin/python3 -m lib.kernel_cleanup --all
```

After setup completes, run `sudo systemctl start cleanup-maintenance.service`
to perform cleanup immediately instead of waiting for the weekly timer. The
same safety checks apply.

To deliberately retain an additional obsolete image, use `apt-mark hold PACKAGE`
(which also blocks package upgrades), or the platform's kernel retention rules.
On hosts with automated kernel cleanup, a manual-install flag alone no longer
reserves an obsolete kernel. Failed inventory, retention inspection, package
simulation, or metadata updates stop package removal and are reported through
the cleanup job's failure notification.

`user-cache-maintenance` starts a root observer and a permanently demoted worker
for the configured non-root account, daily after the scheduled update windows.
Its fixed command is `/usr/bin/python3 -I
/opt/basaltwater/common/service_tools/user_cache_launcher.py --user USER`.
Root setup invokes the same launcher directly, including setup's
`--agent-storage-only` pass. The launcher validates the target account and uses
`setpriv` to clear supplementary groups and capabilities and enable
`no-new-privs` before starting the worker. All cache inspection and deletion
remain unprivileged.

Full user-cache maintenance also applies the rootless Podman image policy
described in [storage cleanup](../agents/storage-cleanup.md#podman-image-cleanup).
On first setup or after reboot, the restricted worker may be unable to initialize
Podman's user namespace. Only an initial `podman info` failure with matching
`newuidmap`/`newgidmap` map-write `Operation not permitted` and namespace-helper
diagnostics, together with a bounded kernel observation of `NoNewPrivs=1` and an
empty capability bounding set, returns `deferred_restricted` with fixed issue
`namespace_helper_restricted`. Maintenance logs this as informational and
explicitly says Podman cleanup was skipped. Both cleanup flags remain false;
there is no inventory, removal or immediate retry after that failed probe.
Other unknown or failed Podman observations still fail maintenance. Diagnostic
logs and failure details include only allowlisted issue codes, without raw vendor
output or metadata. The direct cleanup CLI still exits nonzero for every deferral.

A later scheduled pass may succeed after normal Podman use by the same account
has initialized its user namespace. This is conditional: initialization is not
performed by maintenance, and the namespace disappears across reboot. Repeating
the same restricted pass alone does not guarantee recovery. Successful restricted
probes proceed through the normal cleanup checks. Keep launcher restrictions and
the privileged observer's fixed protocol intact.

The worker inherits a private observer socket. The privileged parent reads only
fixed `/proc` executable metadata for that account, freshly collected for each
inventory and disposal check; worker requests cannot select paths, PIDs, or
UIDs. This lets release retention inspect protected account daemons without
weakening ptrace permissions or trusting a process-name (`comm`) allowlist.
An unavailable or incomplete observation still defers removal.

The worker inventories tool-reported
cache paths before acting. Tool commands receive the account's home-scoped
environment without loading interactive login profiles, and apply these
bounded policies:

- npm runs its supported verification and garbage collection, then uses a
  forced clean if the cache still exceeds 2 GiB or disk space is low. Rebuildable
  `_npx` workspaces are removed when they exceed 1 GiB or have had
  no activity for 30 days, and only while npm and npx are idle;
- pip purges above 2 GiB or under disk pressure. uv runs its supported prune
  operation. Go cleans build and module caches above 2 GiB and 5 GiB
  respectively, or under disk pressure;
- OpenCode and Codex cleanup is restricted to rebuildable cache directories.
  The OpenCode cache limit is 2 GiB and the Codex cache limit is 1 GiB; either
  cache may also be removed after 90 days without activity. Codex temporary
  entries older than seven days are removed individually. Standalone Codex
  installations retain the current release, the newest prior release for
  rollback, the release selected by `~/.local/bin/codex` (which can differ from
  vendor `current` after a rollback), and every release currently executing.
  An existing launcher that cannot be verified prevents removal; a missing
  launcher selects no additional release. Older releases are removed
  only after their vendor manifest, entrypoint, ownership, and directory layout
  have been validated; unfamiliar entries are left unchanged.
  When extras could be removed, an unreadable process executable or incomplete
  account process inventory defers removal. This is an informational safety skip,
  not a setup or scheduled-job failure: all unremoved candidates are retained
  for retry, and the log identifies the blocking PID when available. Protected
  account daemons can prevent inspection in the direct-user fallback; the
  managed root observer supplies the bounded executable inventory for release
  retention. Do not weaken process permissions or infer inactivity from names.
  Release and backup inventory logs use `known_active_count` for confirmed
  running matches and `cleanup_deferred` for safety skips. Zero confirmed
  matches does not establish inactivity when inspection was skipped or
  incomplete. Direct-user retries can keep retaining extra snapshots while a
  long-lived protected daemon remains uninspectable.
  Current and rollback releases alone need no process scan. Process activity
  and launcher selection are checked again before each removal; a failed check
  preserves remaining candidates for a later retry. Failure during process
  directory enumeration is also an informational visibility deferral.
  The host doctor checks this retention policy when more than two releases
  remain: active releases and rollback copies do not trigger an excess-release
  warning. Unused extras, unfamiliar entries, or failed inspection still warn;
  the doctor never removes files.
- Codex package snapshots under `~/.local/state/basaltwater/agent-backups`
  are reconciled during daily maintenance and setup's agent-storage pass. This
  retries obsolete snapshots retained by an update while processes were running.
  Cleanup preserves the rollback executable recorded in `agent-tools.json`,
  selected launchers, running snapshots, and unfamiliar entries. It acquires
  the updater's existing `agent-update.lock` with a nonblocking exclusive flock
  throughout inspection and disposal; it never creates or replaces that lock.
  A busy update lock or incomplete process inventory when extra snapshots exist
  produces the same informational deferral, preserving snapshots for retry.
  Missing or unsafe locks, missing/corrupt/unfinished update state, unverifiable
  rollback copies, and filesystem or disposal errors still report maintenance
  failures. Readiness inspections and update records continue to expose
  incomplete cleanup; a successful maintenance pass does not mean every
  snapshot could be inspected or removed.
  A verified rollback snapshot alone requires no process scan because
  nothing can be deleted. Dry runs use the same
  checks and report eligible snapshot counts without writing files. The legacy
  `codex.previous` recovery executable is retained: migration records do not
  establish that its bytes duplicate a retained validated package.
- T3 Code numbered provider and trace log rotations are retained up to 256 MiB
  and 14 days. Current logs, terminal logs, non-numbered files, and symbolic
  links are never selected by this policy. Agent setup reruns apply both the
  Codex release policy and T3 rotation policy immediately, in addition to the
  daily maintenance job. Setup starts the root launcher directly; its demoted
  worker performs reconciliation as the target account.
- T3 versioned application runtimes retain the active version, the immediately
  preceding installed version, all newer candidates, and versions referenced
  by service update/recovery state or running processes. Other validated older
  runtimes qualify after seven days without modifications. Pending updates,
  unreadable or unknown state, and changes to state during cleanup prevent
  removal. Staging directories and the legacy installation are left alone.
- Recognized Linux Electron download ZIPs under `~/.cache/electron` expire
  after 30 days, or after one day under disk pressure. Installed Electron apps,
  browser profiles, and other archives are not selected.

Disk pressure means the cache's own filesystem has less than 20% available,
with that free-space target bounded between 2 and 8 GiB. Reserved filesystem
blocks do not count as available. Under pressure, npm, pip, Go, npx, and the
allowlisted agent caches can be evicted below their normal size limits, but
caches smaller than 64 MiB are retained unless their ordinary age/size policy
selects them. Cleanup rechecks space before each eviction, so later caches
remain available once the target is met. Agent retention and disposable
Electron downloads run before package-cache eviction; Go module downloads
come after build-cache cleanup. Downloads and subsequent builds can be slower
after eviction.

Logs show cache inventories, active-use deferrals, selected removals, and
measured file-byte reductions for npm, pip, and Go evictions. A dry run reports
eligibility at the current free-space level; it cannot predict which later
evictions a real run would avoid after recovering space. Cleanup cannot
guarantee free space when active tools or persistent project data occupy it.
On a 32 GiB desktop, keep only a few small projects locally and check
`df -h / "$HOME"` before a large build or update. See the
[hardware sizing tiers](../../README.md#hardware-sizing) for larger workloads.

Cache eviction is deferred while a matching tool or referencing process is
running, including processes that reference the cache only through an open file.
Descriptor inspection is bounded; unavailable coverage, including protected
account processes, produces an informational deferral rather than authorizing
eviction or failing maintenance. Symbolic links and paths outside the configured user's home are never followed or
removed. OpenCode data under `.local/share/opencode` and Codex sessions,
memories, credentials, plugins, and unrecognized package layouts are persistent
state and are not cleanup targets. Root-only setups retain system cleanup but
intentionally skip the user-cache timer.

Standalone Codex release, T3 runtime, numbered T3 log rotation, Electron download,
generic rebuildable-cache, stale-tool-temp, named Basaltwater-temp and crash-file
disposal rechecks device/inode, ownership, type and modification time immediately
before acting, along with each policy's state and active-use checks. T3 runtime
disposal rechecks service state and updater/process activity; numbered log
rotations retain their age/size policy without a process scan.
Parent directories are opened without
following links. Selected entries move into a private `0700` sibling disposal
directory before deletion, so a recreated original pathname is preserved. Dry
runs validate without moving or removing data. A changed entry is refused; a
race during isolation or failed/interrupted deletion retains remaining data
privately, with the recovery location in local maintenance logs. Disposal
directories are excluded from automatic retention, including stale-tool-temp
scans regardless of age, and require explicit recovery;
inspect their owner/identity and any recreated original before restoring data.
Never overwrite a new original path or treat a retained disposal as proof of
successful cleanup. Project reports, captures and SDK/image caches do not become
cleanup candidates because of their age.

On CachyOS, setup's pacman/paccache inventory uses the same bounded capture
owner with a 1 MiB output ceiling per query. An incomplete, timed-out or oversized
inventory stops before deletion. Its package transaction, file identity and
installed-version checks remain separate from generic user-cache disposal.

Preview the user job without changing files:

```bash
sudo /usr/bin/python3 -I \
  /opt/basaltwater/common/service_tools/user_cache_launcher.py --user USER --dry-run
```

Replace `USER` with the setup account. Omit `--dry-run` to run cleanup now.
Rerun setup to install this helper and daily timer on an existing host;
updating the controller checkout alone does not change the target's timer.
Unprivileged setup and direct-user invocations retain
`user_cache_maintenance.py` as their fallback. They keep the same conservative
deferral when protected `/proc` metadata cannot be inspected and do not gain
observer privileges. The host supervisor verifies the exact launcher command
and binds `--user` to its trusted baseline account before dispatch; only the
fixed legacy worker commands remain accepted during migration.

Cleanup never removes installed language runtime versions, Proxmox backups,
templates, ISOs, guest volumes, container/image stores, crash-report
directories, or arbitrary files. Proxmox boot entries and pinned kernels remain
under APT and `proxmox-boot-tool` retention policy.

After cleanup, capacity checks cover each distinct real local filesystem rather
than only `/`. Both block usage and inode usage trigger warnings at 80% and
errors at 90%, so a separate Proxmox storage mount cannot fill silently while
the root filesystem remains healthy. Network and FUSE mounts are excluded to
avoid blocking maintenance on unavailable remote storage.

Storage synchronization and scrub jobs write scheduling state atomically and
use current-user-owned, mode-`0700` lock directories under
`/run/lock/basaltwater`; lock files are regular mode-`0600` files and retain a
persistent inode to prevent overlapping runs. Invalid specifications or
unavailable mounts fail visibly so the next scheduled run can retry them.

## Agent Maintenance Holds

Before starting agent work that must survive the normal automatic-restart
window, create a bounded hold as the account that owns the agent tools:

```bash
basaltw agent maintenance hold --hours 8
basaltw agent maintenance status
basaltw agent maintenance release
```

The hold is a private, atomic file below the account's home and expires without
manual cleanup. Durations are limited to 1–72 hours. Creating another hold
renews it; release is idempotent. The same operation can be requested from a
control system with `basaltw agent maintenance hold HOST USER --hours 8`.

When a restart is pending, the restart job checks live agent work owned by the
configured setup account. T3 Code's open orchestration database supplies run
and provider-turn status; queued work, requests waiting for human input, and
activity predating the current server process do not block a restart. This
applies to all providers managed through T3, including native subagents and
background commands that outlive their parent turn. A request waiting for human
input does not hide work in another provider turn or child. Passive monitors
and abandoned rolled-back work do not count as executing background tasks.
Standalone Codex turns are detected
from lifecycle events in rollouts held open by the provider. Executing Codex
`exec`/`review`, including scheduled tasks with global options before the
subcommand and no saved rollout, Claude `--print`, and OpenCode `run` commands
also count as work.
Standalone interactive Claude and OpenCode sessions need an explicit hold when
their work must cross the restart window.

Lingering login managers, background sessions, idle logins, idle provider
servers, open terminals, tmux/screen, build/Git processes, and processes merely
sitting in worktrees do not count as agent work. SSH/desktop logins alone do not
defer restarts on hosts configured with coding agents; hosts without coding
agents still protect non-idle human logins. Use a bounded hold for independent
work that needs protection, such as an unattended build.

The job records only workload categories. It reads T3 status/time metadata and
background-task kinds and persistence flags,
bounded provider command arguments and Codex rollout tails locally; it does not
log their contents or scan closed session history. An unreadable activity
source defers the restart rather than reporting the host idle. An invalid hold
also fails safe and is visible in `basaltw agent doctor --capability host`;
release and recreate it. The configured forced-restart deadline still overrides
known sessions, holds, and workloads after its maximum deferral period.

These changes take effect on managed systems after their setup is refreshed;
updating the orchestration checkout alone does not replace installed services.

## Related Configuration

- `--auto-restart` / `--no-auto-restart` controls normal automatic restarts.
- `--auto-restart-force-days N` sets the maximum deferral period; `0` disables
  forced restarts.
- `--auto-restart-grace N` sets the warning period before a restart.
- `basaltw agent maintenance hold|status|release` manages a temporary,
  per-user automatic-restart hold without changing the host's saved policy.
- Notification targets configured with `--notify` receive important maintenance
  failures and successes where the service supports notifications.
- Security monitoring always collects and logs locally. Without `--notify`, it
  does not send events off-host.

On XRDP hosts, the security monitor validates certificate/key syntax, match,
expiry, private-key permissions, and daemon readability. It notifies only when
health changes (including recovery or certificate fingerprint rotation), while
an unusable pair keeps the service result failed until repaired. The warning
window is 30 days. The monitor never logs or records private-key contents.

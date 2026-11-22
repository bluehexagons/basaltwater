# Command-line reference

Use `basaltw --help` and `basaltw COMMAND --help` for the installed command
surface. For common command shapes, see the [quick reference](quick-reference.md).
The [documentation index](../README.md) routes to feature procedures.

## Choose a topic

<a id="project-manifests-and-node-runtimes"></a>
<a id="vm-game-publishing"></a>
- [Project tooling and game publishing](cli-project-workflows.md) Node runtimes, manifests, and release uploads.

<a id="setup-at-a-glance"></a>
<a id="system-types"></a>
<a id="core-flags"></a>
<a id="maintain-offline-vms"></a>
<a id="restart-after-setup"></a>
- [Setup commands and core flags](cli-setup.md) Machine profiles, core options, and reboot behavior.

<a id="common-setup-flags"></a>
- [Common setup flags](cli-setup-common.md) Machine and service configuration.

<a id="development-flags"></a>
<a id="cachyos-ssh-flags"></a>
- [Development and CachyOS SSH flags](cli-setup-development.md) Runtime and access options.

<a id="agent-host-flags"></a>
- [Agent host setup flags](cli-agent-hosts.md) Coding tools, credentials, and managed agent options.

<a id="setup-completion-access-details"></a>
- [Setup access and credentials](cli-setup-services.md) Completion receipts, pairing, and provider access.
- [Agent diagnostics and storage](cli-agent-diagnostics.md) Manifest, doctor, visuals, capacity, and cleanup.
- [Agent operations](cli-agent-operations.md) Worktrees, models, tasks, account state, and updates.

<a id="proxmox-provisioning-flags"></a>
<a id="swap-configuration-flags"></a>
- [Proxmox and swap setup flags](cli-proxmox-setup.md) VM and LXC provisioning and swap configuration.

<a id="deployment-flags"></a>
<a id="managed-builds-webhooks-and-github-runners"></a>
<a id="antistatic"></a>
<a id="gogs"></a>
<a id="homebox"></a>
- [Deployment, managed builds, and services](cli-services.md) Deployment flags, runners, webhooks, Antistatic, Gogs, and HomeBox.

<a id="storage-and-data-movement"></a>
- [Storage and data movement](cli-storage.md) Shares and storage command examples.

<a id="maintenance-and-utilities"></a>
<a id="local-proxmox-kiosk"></a>
<a id="firmware-audit-and-updates"></a>
<a id="cleaning-obsolete-local-configuration"></a>
<a id="github-maintenance"></a>
<a id="recurring-host-maintenance"></a>
<a id="network-inventory"></a>
<a id="proxmox-management"></a>
<a id="interactive-shell"></a>
<a id="sysadmin-shortcuts"></a>
<a id="testing-and-bootstrap"></a>
- [Maintenance and host utilities](cli-maintenance.md) Firmware, network inventory, Proxmox, admin shortcuts, and testing.

## Commands

`migrate` is retired and exits with an intermediate-version instruction without
changing files or services. Use the [migration guide](../migration-guide.md)
for any remaining infra-tools installation or interrupted historical cutover.

```text
basaltw --version
basaltw batch start [TITLE]
basaltw batch run [--label LABEL] -- COMMAND [ARG ...]
basaltw batch note TEXT
basaltw batch status
basaltw batch end [--force]
basaltw migrate [--system] [--installation /path/to/old-source] [--apply | --recover]
basaltw setup <system_type> <host> [username] [options]
basaltw patch <host> [username] [options]
basaltw shares <host> [username] [options]
basaltw scrub <status|inspect|verify|repair|restore|accept> HOST [options]
basaltw recall <host> [username] [options]
basaltw reconstruct [--compact]
basaltw list [pattern] [--json]
basaltw info [pattern] [--compact]
basaltw cmd [pattern]
basaltw rm <pattern>
basaltw cleanup [host] [options]
basaltw deploy <pattern> [--yes]
basaltw credentials set <username> [password] --service SERVICE --destination DESTINATION
basaltw credentials list
basaltw credentials remove <username> --service SERVICE --destination DESTINATION
basaltw credentials migrate <username> --service SERVICE --destination DESTINATION
basaltw completions [options]
basaltw python-tools [options]
basaltw bootstrap [options]
basaltw self-setup [options]
basaltw local [subcommand]
basaltw manifest init [REPOSITORY] [--kind KIND] [--dry-run] [--json]
basaltw manifest validate [REPOSITORY] [--json]
basaltw manifest check-t3-worktree [REPOSITORY] [--ref REF] [--require-file PATH]... [--json]
basaltw node status [--project PATH] [--version VERSION_OR_RANGE] [--json]
basaltw node env [--project PATH] [--version VERSION_OR_RANGE]
basaltw node exec [--project PATH] [--version VERSION_OR_RANGE] -- COMMAND...
basaltw node resolve [--project PATH] [--version VERSION]
basaltw node install [--project PATH] [--version VERSION] [--package-manager NAME@VERSION]...
basaltw publish <command> ... [--json]
basaltw headscale <add|list|show|rename|remove|onboarding> ...
basaltw headscale-admin <users-list|users-create|users-delete|nodes-list|approve|expire|revoke> ... [--dry-run]
basaltw headscale-client <status|enroll-personal|enroll-machine|reconcile|adopt|disable|leave|migrate|abandon> --url HTTPS_URL [options] [--dry-run]
basaltw headscale-client reconcile --url HTTPS_URL [--confirm-logged-out] [--dry-run]
basaltw headscale-client reconcile-selection HOST --expected-url HTTPS_URL [--expected-server-id ID] --server REFERENCE --new-url HTTPS_URL --node-id ID --target-host HOST --target-key PATH --target-known-hosts PATH [options] [--dry-run]
basaltw headscale-manage <bind|show|remove|enroll|inspect|reconcile|dns-publish|dns-withdraw|dns-inspect|dns-reconcile> REFERENCE [options] [--dry-run]
basaltw headscale-dns <publish|withdraw|inspect|reconcile> [--input PRIVATE_JSON] [--dry-run]
basaltw headscale-maintenance <backup|validate|restore|upgrade|unbound-backup|unbound-validate|unbound-restore|unbound-recover-fresh|unbound-records-inspect|unbound-records-replace|adopt-inspect|adopt> [options] [--dry-run]
basaltw desktop <command> ...
basaltw kiosk <status|doctor|start|stop|restart> [--json]
basaltw firmware <audit|update> [options]
basaltw channel [CHANNEL]
basaltw upgrade
basaltw refresh [--dry-run] [CACHYOS_SETUP_FLAGS...]
basaltw user rename <host> <new_username> [options]
basaltw agent manifest [REPOSITORY] [--json]
basaltw agent blender smoke [--output NEW_DIRECTORY] [--timeout SECONDS] [--json]
basaltw agent visuals compare BEFORE.png AFTER.png [options]
basaltw agent visuals capture --before REV --after REV --settings JSON [options] -- COMMAND...
basaltw agent doctor [HOST USER] [options]
basaltw agent resources [--json]
basaltw agent storage inspect [--path ABSOLUTE_DIRECTORY]... [--max-entries N] [--timeout SECONDS] [--json]
basaltw agent storage project ABSOLUTE_PROJECT [--policy RELATIVE_JSON] [--max-entries N] [--timeout SECONDS] [--json]
basaltw agent storage clean-generated ABSOLUTE_PROJECT --rule NAME [--policy RELATIVE_JSON] [--dry-run | --apply]
basaltw agent storage clean-podman [--dry-run | --apply] [--json]
basaltw agent containers inspect [--engine podman|docker]... [--json]
basaltw agent containers preflight --engine podman|docker --image-bytes N [options]
basaltw agent graphics doctor --context xvfb|display [--json]
basaltw agent update [HOST USER] [options]
basaltw agent auth set HOST USER --tool TOOL --file PATH
basaltw agent auth status HOST USER [--tool TOOL]
basaltw agent auth login HOST USER [--open-browser] [--method subscription|api-key]
basaltw agent privilege <request|status|wait|password-hash> ...
basaltw agent web pair HOST USER [-k PATH]
basaltw agent workspace <create|adopt|group|list|status|remove> ...
basaltw agent models <init|recommend|record|report> ...
basaltw agent tasks <capabilities|list|show|history|receipt|create|update|run|pause|resume|cancel|delete|reset-context|notify|worker|mcp> ...
basaltw agent memory <scope|list|get|put|remove|claim|renew|finish|reopen> ...
basaltw agent maintenance <hold|status|release> [HOST USER] [options]
basaltw agent support-bundle [--output PATH] [--browser-smoke] [--container-storage]
basaltw gogs health HOST [--json] [--min-free-bytes N] [--min-free-inodes N]
basaltw gogs repo-configure [REPOSITORY] --github-url URL --gogs-url URL [options]
basaltw homebox health HOST [--json]
basaltw homebox backup HOST /absolute/target/archive.tar.gz [--dry-run]
basaltw homebox restore HOST /absolute/target/archive.tar.gz --yes [--dry-run]
basaltw maintenance github [--root PATH] <audit|prune> [options]
basaltw shell
basaltw network ...
basaltw proxmox ...
basaltw vm list <host> [--json]
basaltw ssh-key enroll <host> [--port PORT] [--fingerprint SHA256:FINGERPRINT | --yes]
basaltw ssh <host> [connection-options] [-- <command> ...]
basaltw fan <host> [<host> ...] [connection-options] -- <command> ...
```

`basaltw --version` prints one stable line containing the installed project
version, suitable for feedback and support records.

`headscale-admin` provides root local native administration over independent SSH
recovery; see [administration](../services/headscale-administration.md).
`headscale-client` provides explicit root local enrollment and lifecycle actions;
see [client commands](../services/headscale-clients.md#local-recovery-commands).
`reconcile --confirm-logged-out` confirms an independently reviewed logout;
`reconcile-selection` reconciles a controller's saved binding after migration.
See the [multi-VM Headscale walkthrough](../services/headscale-vms.md) for
separate control, client, and DNS VMs. Fresh service VMs can run root-local
commands as root with `python3 /opt/basaltwater/basaltwater.py` even when no
`basaltw` launcher is installed.
Saved `headscale` references provide discovery only, without management authority.
`headscale-manage` uses explicit pinned SSH authority in the selected workspace;
see [management and tagged enrollment](../services/headscale-management.md).
Its `dns-publish` and `dns-withdraw` actions also require `--resolver NAME`
and `--peer NAME`; `dns-inspect` inspects the saved publication state. See the
[Headscale VM guide](../services/headscale-vms.md) for the separated control,
client, and DNS roles.
For interrupted DNS operations, `dns-reconcile` requires
`--expected-attempt /private/reviewed-attempt.json` and verifies the exact
retained controller attempt against native settled state and the peer; it never
replays activation. See [interrupted DNS operations](../services/headscale-dns.md#interrupted-operations).
`headscale-dns` performs explicit root local DNS publication and recovery.
Its mutating actions require a private root-owned JSON request; `inspect`
rejects `--input`. Dry runs retain handler input and privilege validation.
`headscale-maintenance` provides explicit local backup, restore and reviewed
upgrade operations; see [recovery commands](../services/headscale-recovery.md).
Its dry run validates paths and private metadata without native calls or locks.
Setup and patch validate the complete merged network configuration and ingress
before provisioning or uploading source. Active named selections follow stable
IDs across renames and reject removed/replaced records or changed endpoints.
`--no-tailscale` preserves the endpoint binding without requiring the reference
to remain registered. Omitted network flags retain saved settings on reruns.
`--tailscale NAME` completion reads names from the selected workspace.
For managed hosting, `setup --headscale --headscale-name NAME` installs
Headscale and registers/binds that server. For a named client setup, repeat
`--tailscale-tag tag:service` as needed to privately enroll the client before
its final profile and Unbound configuration are applied. Without tags, setup
with a named network or a direct `--tailscale-login-server URL` keeps enrollment
manual. Routine configuration changes use setup reruns.

`basaltw refresh` upgrades the installed source on its selected channel and
repeats this machine's last successful setup using the updated code. It supports
Debian and CachyOS. See [refresh behavior](#refresh-this-machine) below and
[CachyOS first use](../workstations/cachyos.md#upgrade-and-repeat-your-last-setup).
The separate [native Windows launcher](../workstations/windows-maintenance.md#native-basaltw-commands)
also provides `refresh` for its saved workstation installation.

For the desktop command catalog, including accessibility, window operations,
waits, and live smoke checks, see [Desktop automation](../workstations/desktop-automation.md).

Use `basaltw agent doctor --capability t3code` to check the managed T3
service, native runtime, provider authentication, Git identity, pairing helper,
endpoint, and agent skill. Add `--fix` to rebuild missing native dependencies,
configure the GitHub HTTPS credential helper after a successful login, enable
the service at boot, and restart it when inactive.

### Global workspace selection

Use [command batches](../operations/batches.md) to collect command results across
a script and print one summary with `basaltw batch end`.

Place `--workspace PATH` before the command to select the workspace for saved
setups, credentials, `known_hosts`, and history. The selection carries through
nested command groups:

```bash
basaltw --workspace /srv/basaltwater list
basaltw --workspace /srv/basaltwater vm status saved-vm --json
basaltw --workspace /srv/basaltwater vm snapshot list saved-vm --json
basaltw --workspace /srv/basaltwater proxmox hosts
```

Commands that expose their own `--workspace` still accept it in the positions
shown by their `--help`. An explicitly supplied command-local workspace
overrides the global selection; omitting it preserves the global selection.
For example, this command uses `/srv/other`:

```bash
basaltw --workspace /srv/basaltwater vm --workspace /srv/other status saved-vm --json
```

With no `--workspace`, Basaltwater uses `BASALTWATER_WORKSPACE` when set,
otherwise `~/.config/basaltwater`.

### Refresh this machine

On Debian, run:

```bash
sudo basaltw refresh --dry-run
sudo basaltw refresh
```

Refresh reads the successful target configuration from
`/opt/basaltwater/state/setup.json` (the standard path may link to durable state).
Existing Debian setups already have this record. On a Debian target with systemd
service management, refresh validates the saved options and dispatches a root
systemd service before upgrading source or starting the upgraded target setup
runner. The service runs in an independent cgroup, so
refresh can finish when launched inside a local T3 terminal or through SSH from
a remote T3 terminal, even when setup restarts T3. An already independent,
approved administrator job can run refresh directly. OCI targets retain
synchronous foreground refresh and do not manage or restart a T3 service.
Dry runs stay synchronous on every target.

For queued Debian refresh, a caller exit code of zero means the job was accepted.
Read the printed service name and log path to determine completion. The worker's
upgrade/setup exit code is retained by `basaltwater-refresh-HEX.service` with
`RemainAfterExit=yes`; inspect its `ActiveState`, `Result` and `ExecMainStatus`:

```bash
sudo systemctl show basaltwater-refresh-HEX.service -p ActiveState -p Result -p ExecMainStatus
sudo tail -n 100 /var/log/basaltwater-refresh/HEX.log
```

Replace `HEX` with the printed job identifier. Logs are mode `0600` in a
root-owned mode-`0700` directory. Do not treat successful dispatch or loss of a
T3/SSH terminal as successful setup; inspect the job before retrying.

The target account, packages, services, and other
saved settings are reused; it does not infer a replacement profile or
re-provision the machine. Each Debian refresh forces an APT update and upgrade
and rechecks selected versioned runtimes, even after an earlier successful setup.
APT can install new dependencies required by the upgraded packages.
The normal service reconciliation steps still apply. Existing staged deployment
sources are reused; refreshing does not fetch new application repositories from
a controller. Provider credentials and existing local account passwords are
retained; transient credential-copy, network-activation, and swap-initialization
requests are not replayed.

Artifact-mode targets reconcile their installed release descriptors and retained
bundles without fetching application source, contacting a builder, or running a
compiler. Missing or unsafe retained inputs refuse with controller deployment
guidance; refresh does not create a replacement build. See
[selected-builder deployments](../services/deployments.md#build-on-a-saved-builder).

On CachyOS, run `basaltw refresh` **without sudo** as the original desktop user.
Its private successful selection is saved by new `agent_cachyos` setup runs.
After upgrading an older checkout, run your full setup command once to create
that record. See the [CachyOS guide](../workstations/cachyos.md#upgrade-and-repeat-your-last-setup).
Additional supported CachyOS setup flags merge into the saved selection, for
example `basaltw refresh --material-maker --etcher --butler --steamcmd --dry-run`.
Unspecified settings are preserved; scalar/boolean values override saved values,
repeatable repositories/sources add to them, and agent selections override their
saved exclusions. Only a successful setup saves the combined selection. Upgrade
an older CLI first with `basaltw upgrade` to obtain these new flags. Debian does
not accept refresh overrides; use its explicit setup workflow instead.
CachyOS refresh keeps package-review prompts in the foreground. Disruptive T3
activation is queued after all setup steps, saved state and final notifications
finish, allowing refresh to complete from its own T3 terminal.

On both systems, `--dry-run` shows the saved setup plan without fetching or
changing source or applying setup. `dev` follows `main`, `stable` follows the
latest release, and version/commit channels stay pinned. Setup runs even when
source was already current. Upgrade failure stops before setup; setup failure
leaves Basaltwater upgraded and the previous successful record available for
retry. A foreground refresh returns that nonzero result; an independent Debian
worker records it in the service result and log above. Finish active work before
applying a full setup.

Debian refresh also accepts controller-installed source snapshots. It downloads
the official repository and automatically creates a managed checkout, preserving
durable setup state and staged application data. Snapshots follow their recorded
branch; `main` and snapshots without a branch use `dev`. The previous source is
retained in a private sibling backup. Download or activation failure leaves the
previous snapshot active; setup failure leaves the upgraded source available for
retry. Refresh holds the local setup/maintenance lock throughout the mutation.
`basaltw upgrade` alone still requires a managed Git installation.
The command accepts no remote host argument and never guesses which saved
remote host to deploy. An explicit setup with your full desired selection
replaces the saved options; CachyOS refresh flags instead merge into them.

### Bootstrap and self-setup flags

`bootstrap` and its `self-setup` alias prepare the local orchestration host.
On an unsupported host, bootstrap asks for `[y/N]` confirmation before any
local system-package operation. Use `--skip-system-packages` after manually
installing the controller prerequisites when the host uses a non-APT package
manager.
When that host is a Proxmox VM, install and activate the guest agent with:

```bash
sudo basaltw self-setup --qemu-guest-agent
```

| Flag | Description |
|------|-------------|
| `--qemu-guest-agent` | Install `qemu-guest-agent`, then start and enable its systemd service; requires root and cannot be combined with `--skip-system-packages` |

The same option is available on `install.sh`; place it before
`--setup` or `--local-setup` so the installer forwards it to its internal
self-setup step. It is intended for a Debian VM running the orchestration
tools, not for an LXC container.

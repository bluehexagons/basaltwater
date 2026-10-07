# CachyOS maintenance and recovery

For a first installation, follow the [CachyOS setup guide](CACHYOS.md).
This reference covers an established Basaltwater installation. Run commands
in fish from the KDE session as the same desktop account, without `sudo`
unless a specific command requires elevation.

## Privileged checks from an agent

On an existing KDE desktop with a registered polkit authentication agent, an
agent may request an authorized privileged command through the normal desktop
password dialog when its execution policy permits. A failed `sudo -n` check
alone does not establish that desktop authentication is unavailable. Announce
the specific check and run the executable directly, for example:

```fish
/usr/bin/pkexec --disable-internal-agent /usr/bin/ufw status verbose
```

The owner enters the password only in KDE's dialog; the agent receives the
command's output. `--disable-internal-agent` prevents a fallback password prompt
in the agent's terminal. If no graphical agent is available or authentication
is rejected, leave the check pending and use the owner's KDE terminal. Do not
collect passwords in chat, tool stdin, scripts, or environment variables.

For the mirror recovery described below, the scoped elevated command is:

```fish
/usr/bin/pkexec --disable-internal-agent /usr/bin/systemctl start cachyos-rate-mirrors.service
```

For effective firewall inspection, use the same invocation with
`/usr/bin/ufw show raw` and inspect the live IPv4/IPv6 input chains and rule order.
Authentication applies to the requested command; it does not authorize other
system changes. This is an interactive audit/recovery procedure, separate from
the unprivileged, read-only doctor.

When copying commands into a terminal, copy only the command text. `fish` is
the code block's language label, not a command prefix. Run each command
separately; joining several lines with spaces passes later commands as arguments
to the first command.

## Installer data and older installations

The installer no longer migrates `infra_tools` user data. Any remaining old
installation must first use the [intermediate version](BASALTWATER_MIGRATION.md).
An existing `cachyos-t3` data directory at the default Basaltwater install path
is retained during reinstall.
Installer updates also carry forward managed `state`, `deployments`, and
`worktrees` directories.
If that path also contains other unmanaged files, move or resolve them before
rerunning the installer.

## Mirror refresh failures

If `health.mirrors` reports failure, inspect the distro service before changing
package or network settings:

```fish
systemctl status cachyos-rate-mirrors.service --no-pager
journalctl -b -u cachyos-rate-mirrors.service -n 60 --no-pager
systemctl list-timers cachyos-rate-mirrors.timer --no-pager
getent hosts geoip.kde.org
getent hosts archlinux.org
```

Use the hosts named in the actual error when they differ. A successful lookup
now does not establish that DNS worked during the failed run or that HTTPS to
every mirror works. On the audited laptop, refresh failed during a network
reconnection with `Could not resolve host: geoip.kde.org`; the timer's next
scheduled attempt was nine days away. The installed
[CachyOS service](https://github.com/CachyOS/CachyOS-PKGBUILDS/blob/master/cachyos-rate-mirrors/cachyos-rate-mirrors.service)
has no automatic failure retry. `network-online.target` orders startup;
[NetworkManager's wait service](https://networkmanager.pages.freedesktop.org/NetworkManager/NetworkManager/NetworkManager-wait-online.service.html)
does not guarantee continuous Internet or DNS availability.

The follow-up journal comparison established that this run began at 11:49:09
on October 7, immediately after resume. NetworkManager installed DNS servers
and completed Wi-Fi activation at 11:49:13; Internet connectivity was reported
at 11:49:14. Both the Arch mirror-status and KDE GeoIP HTTPS endpoints returned
HTTP 200 when checked afterward. This supports a wake-time connectivity race,
with no evidence of a continuing DNS outage. Installed version `24-1` already
tolerates a failed GeoIP lookup; the fatal error was the subsequent Arch
mirror-status fetch. Setting a country would not repair that fetch.

The missing retry policy is reported upstream in
[CachyOS issue #1739](https://github.com/CachyOS/CachyOS-PKGBUILDS/issues/1739).
[PR #1741](https://github.com/CachyOS/CachyOS-PKGBUILDS/pull/1741) proposes delayed,
bounded retries. At the October 7 investigation, the installed service still
had no `Restart=` setting. Track the distro fix through normal updates; local
retry drop-ins are a separate administrator policy, outside this tooling
profile's setup. The loaded local unit remains the authority for what actually
runs on a workstation.

Once connectivity is restored, retry the existing distro service from your
terminal, completing its normal sudo prompt:

```fish
sudo systemctl start cachyos-rate-mirrors.service
systemctl show cachyos-rate-mirrors.service -p ActiveState -p Result
basaltw local cachyos-doctor
```

This reranks system mirror lists. A successful oneshot normally returns to
`ActiveState=inactive` with `Result=success`; merely clearing the failed state
does not rerun mirror refresh. Resolve any new error in its journal. Then use
the normal full CachyOS update workflow if needed. Do not use `pacman -Sy` or
replace DNS servers to conceal a transient failure. Basaltwater observes the
service without installing retries, changing distro units, or refreshing
package databases. A service with no recorded failure does not prove that its
mirrors or pacman sync metadata are current.

## Surface resume and touchpad warnings

The Surface Laptop 6's October 5 warning occurred during resume, in the call
chain `spwr_notify_bat` → `spwr_battery_recheck_full` →
`ssam_request_do_sync_with_buffer`. The assertion said the Surface Aggregator
controller was not started; the battery event returned `-19` (`ENODEV`). The
[upstream controller](https://github.com/torvalds/linux/blob/v7.2/drivers/platform/surface/aggregator/controller.c)
rejects requests outside its started state. Together with the resume timing,
this suggests a driver/event ordering race; it does not establish its exact
trigger or an applicable fixed kernel version. Battery state was readable and
reported present/full during the follow-up. The two later resumes in the same
boot completed without another recorded instance of this assertion.
The desktop owner reported no noticeable touchpad, battery, or wake problems.

The touchpad messages were separate: five jumps were reported shortly after
the October 4 boot, followed by libinput's log rate limit. Absence of later
messages does not prove absence of later jumps. Upstream
[libinput guidance](https://wayland.freedesktop.org/libinput/doc/latest/touchpad-jumping-cursors.html)
explains that these messages report implausible touch coordinates that libinput
discards. If the only symptom is the log warning, the filter is functioning as
intended. A visible pointer jump, freeze, or broken gesture needs a focused
device investigation.
For this symptom-free workstation, monitoring through normal distro maintenance
is appropriate; no hardware-specific override was justified by this audit.

The installed `libinput` package already included the `045e:09af` touchpad
quirk with pressure range `25:10` and palm threshold `500` in
`/usr/share/libinput/30-vendor-microsoft.quirks`. That device was tagged as a
touchpad by udev. Do not duplicate those settings in a local override just
because older Surface guides suggest adding them. Their presence does not
prove that pressure calibration is correct or explain coordinate jumps.

Read-only follow-up commands:

```fish
journalctl -b -k -g 'PM: suspend|surface_aggregator|surface_serial_hub' --no-pager
journalctl --user -b -g 'Touch jump|Libinput' --no-pager
ls /sys/class/power_supply
```

Check battery/wake behavior and physical touchpad behavior with the desktop
owner before changing kernels, firmware, or quirks. The `libinput` command-line
debugging tools are in the separate `libinput-tools` package on this CachyOS
installation; the missing command does not mean KWin lacks the libinput
library. If a touchpad problem is reproducible, install those tools through
normal pacman maintenance, identify the actual touchpad event node (numbers
can change), and follow libinput's device-specific recording/report procedure.
Input recording is a deliberate owner action, separate from the read-only
doctor. Preserve the relevant journal and check distro/Surface support for the
installed kernel and firmware instead of treating these messages as evidence
that every Surface needs a replacement kernel.

## Direct T3 access across LAN subnets

`--lan-access` allows the single private default-route LAN, not every private
address range. On the audited laptop, address `192.168.68.57/22` produced an
allow for `192.168.68.0/22`. A new T3 connection arriving from `192.168.0.x`
would therefore hit the covering deny on TCP 3773. The desktop backend was
listening on `0.0.0.0:3773` and responded locally; it was not confined to
loopback. This is the configured firewall scope, not evidence of a broken T3
listener.

For a trusted client on another subnet, confirm its actual source IP/mask and
the destination used for direct pairing. Explicit `--access-source` values
can add a private host or subnet to the saved setup selection. For example,
`--access-source 192.168.0.0/24` would add that network if `/24` is its confirmed
scope. Preview through `basaltw refresh --dry-run` with the access flag, retain
the existing selection, and finish active work before applying refresh. Access
sources apply to all selected managed services, including Sunshine; they are
not a T3-only allowance.

The router must also route the client to the laptop and permit traffic between
the networks. A firewall allow on the laptop cannot create that route or bypass
router isolation/NAT. Check for a new connection from the intended client after
applying the desired policy. T3 Connect is separate from direct LAN pairing;
use desktop Settings → Connections for the desktop environment rather than
starting another backend. See the upstream
[remote-access guide](https://github.com/pingdotgg/t3code/blob/main/docs/user/remote-access.md)
for connection modes and route selection.

## Codex updates

For deliberate standalone updates outside setup:

```fish
basaltw agent update --tool codex --dry-run
basaltw agent update --tool codex --tools-only-readiness
```

The update checks successful version/help commands and keeps a complete package
snapshot for rollback, including adjacent sandbox and other runtime resources.
It retains the latest snapshot plus any selected or running snapshots; older
validated snapshots are pruned. Update records and backups are private under
`~/.local/state/basaltwater`. Concurrent Basaltwater updates are refused until
the current update finishes. `--tools-only-readiness` avoids gating CLI updates
on the generic VM host/T3 checks, which do not qualify this desktop profile.
Restart Codex/T3 provider sessions when convenient to use the new executable.
Verify a real Codex task in T3 after setup; local login status does not test API
access or the GUI's provider environment. If needed, set T3's Codex **Binary
path** to the absolute launcher path printed by setup, typically
`/home/USER/.local/bin/codex`; keep that stable path across updates.

## T3 desktop updates

The desktop's embedded server uses the installed `t3code-bin` version. A newer
client can report a server update even when Basaltwater setup succeeded:
`setup` and `refresh` retain an installed desktop package. `pacman -Qu` checks
repository sync metadata and does not check AUR releases.

Update through the same AUR helper that installed the package. On a workstation
using CachyOS's default Shelly, run as your desktop user and complete its normal
package-review and sudo prompts:

```fish
pacman -Q t3code-bin
shelly update aur t3code-bin
pacman -Q t3code-bin
```

Finish active work and quit/reopen T3 Code afterward to load the updated embedded
server. Installing a package does not update an already running process. If
the warning remains, check which environment the other PC is connected to;
desktop and managed web runtimes have separate update workflows. If the AUR
recipe lags upstream, keep the package manager's ownership and review policy.

Desktop setup creates or protects the default `~/.t3` and `~/.t3/userdata`
directories with mode `0700`, and an existing `clerk-tokens.json` with `0600`.
It retains their contents and refuses symlinks, foreign ownership, and unexpected
file types. Private parent directories protect credentials and session databases
even if the app later creates a file with permissive settings. The read-only
`basaltw local cachyos-doctor` checks these permissions when desktop mode is saved;
it never opens tokens or databases. A permissive token file inside private parents
is advisory; exposed directories or unsafe ownership/types are failures.
Custom T3 state locations require separate inspection.

## AUR download failures

Shelly's generic source-download failure can hide the underlying Git error:
its [3.1.6 download implementation](https://github.com/Seafoam-Labs/Shelly-ALPM/blob/v3.1.6/Shelly.PackageManager/src/aur/manager.zig)
discards failed clone/pull output. This message alone does not establish a
network problem. Basaltwater now includes the helper's exit status, retry
command, and diagnostic pointers when installation fails.

First inspect the version, configured source, and cache ownership as your normal
desktop user:

```fish
pacman -Q shelly
shelly config get AurUrl
ls -ld -- "$HOME/.cache" "$HOME/.cache/Shelly"
# If you configured an absolute XDG_CACHE_HOME, inspect its Shelly directory too.
```

If the reported **Shelly directory itself** is root-owned, and it is your normal
cache directory rather than a symlink or shared location, repair just that
directory and retry setup:

```fish
sudo chown -- (id -u):(id -g) "$HOME/.cache/Shelly"
```

Use the actual path reported by preflight for a custom cache. Do not recursively
chown your home or delete the AUR cache; existing checkouts may contain edits.
If another parent directory or checkout has incorrect permissions, inspect it
separately. Setup does not assume every download failure is an ownership issue.

To expose Git's own network/TLS/proxy error without building or installing,
clone into a new temporary directory as your normal user. The URL below is for
the default Arch AUR: replace its base with your configured `AurUrl` if different,
so the check tests the same service as Shelly.

```fish
set -l aur_probe (mktemp -d)
and git clone -- https://aur.archlinux.org/t3code-bin.git "$aur_probe/t3code-bin"
```

This only downloads packaging files; do not execute them for diagnosis. A
successful clone tests access as your user, but does not rule out a Shelly cache,
elevation, dependency, or later application-download failure. Inspect Shelly's
session log at `/var/log/shelly.log` (may require sudo) or its unprivileged
fallback `$XDG_STATE_HOME/shelly/shelly.log`, or
`~/.local/state/shelly/shelly.log` when that variable is unset. The log may still
omit Git's discarded error. Resolve the reported cause, then rerun the
same full setup command. The existing managed web service stays running if
desktop installation fails.

## Sunshine service and encoder checks

Sunshine supports Intel, AMD, and NVIDIA GPUs. On Linux, Intel/AMD hardware
encoding can use VA-API; a missing CUDA library during automatic probing does
not establish a streaming failure on those GPUs. See the upstream
[encoder settings](https://github.com/LizardByte/Sunshine/blob/master/docs/configuration.md#encoder).

`--sunshine` installs the native package and `libva-utils`, enables Sunshine at
KDE login, and starts its user service. Setup verifies that it remains active
across consecutive checks. The package's `Restart=on-failure` policy allows a
normal tray-menu quit or `systemctl --user stop` to leave it stopped for that
session. It starts again at the next KDE login; `disable --now` stops it
persistently until setup selects Sunshine again. An already active service is
retained without restart.

This requires an active graphical desktop. Setup does not configure automatic
login or create a display before login; remote-first machines still need KDE
running to stream it. Setup refuses masked/custom units or drop-ins instead of
overwriting them. Sunshine pre-login streaming needs a separate greeter-to-user
handoff. LizardByte's [pre-login guide](https://app.lizardbyte.dev/2024-10-16-autostart-sunshine-on-boot-without-auto-login/)
was tested on Debian with SDDM's X11 greeter; it does not qualify current
CachyOS's Plasma Login Manager/Wayland greeter. Enabling user lingering alone
does not create that display/session integration. Basaltwater does not configure
pre-login streaming in this profile. Check the existing session from KDE:

```fish
basaltw local cachyos-doctor
systemctl --user status app-dev.lizardbyte.app.Sunshine.service --no-pager
journalctl --user -u app-dev.lizardbyte.app.Sunshine.service -n 100 --no-pager
```

With Sunshine selected in the saved setup, the doctor distinguishes an
inactive service from failures without restarting it. It also
checks H.264 High VA-API encoding profiles; older setups may need the diagnostic
tool installed with `sudo pacman -S --needed libva-utils`. Select an actual render
node from `/dev/dri` and inspect it without starting screen capture:

```fish
ls /dev/dri/renderD*
vainfo --display drm --device /dev/dri/renderD128
```

Use your machine's node, which may differ from the example. An encoding profile
uses `VAEntrypointEncSlice` or `VAEntrypointEncSliceLP`; `VAEntrypointVLD` is
decoding. Profiles alone do not prove live encoding, capture, input, or Moonlight
streaming. VA-API results do not qualify NVIDIA's separate NVENC path.

For an Intel-only render-device layout with verified H.264 VA-API encoding,
setup supplies `encoder = vaapi` when no explicit encoder setting exists and
Sunshine is stopped. This avoids the Vulkan encoder probe that crashed on the
audited Intel workstation. Other settings, explicit encoder selections, and
active services are retained. Unknown/mixed GPU layouts retain automatic
selection; setup does not install or modify graphics drivers.

If logs stop at `Trying encoder [vulkan]` or `Creating encoder [h264_vulkan]`
and the crash stack points into the Intel Mesa Vulkan driver, try the documented
`encoder = vaapi` setting in your existing Sunshine configuration, after
confirming VA-API support. Preserve other settings. This avoids the Vulkan
encoder probe; it does not repair the driver or prove the VA-API path works.
Keep capture selection separate: KDE Wayland is not a wlroots compositor,
so a missing `wlr-export-dmabuf` interface is not an NVIDIA requirement.

After addressing the reported error, the desktop owner can reset the failed
state and retry the service:

```fish
systemctl --user reset-failed app-dev.lizardbyte.app.Sunshine.service
systemctl --user start app-dev.lizardbyte.app.Sunshine.service
```

Keep Sunshine administration local at `https://localhost:47990`, pair a trusted
Moonlight client, and verify video, audio, and input. Include `--lan-access` or
explicit `--access-source` values in the full setup selection before remote
streaming. The read-only doctor does not start capture or change encoder settings.

## Switch modes on a later setup

`--t3code-desktop` and `--web-interface t3code` cannot be selected together.
Finish active work before switching:

```fish
# Web → desktop: install/verify the package, then disable the managed web service
basaltw setup agent_cachyos localhost --t3code-desktop

# Desktop → web: quit the desktop app first to free its listening port
basaltw setup agent_cachyos localhost --web-interface t3code
```

Desktop mode disables only the Basaltwater-owned user service and retains its
unit, runtime, and data. Web mode stages and starts the managed service again;
the desktop package stays installed. Omitting both flags leaves the current
mode alone. Setup never kills the desktop app or takes over an upstream service.

The web service uses `~/.local/share/basaltwater/cachyos-t3/data` explicitly,
separate from the desktop's default `~/.t3`. Switching modes does not copy,
delete, or merge their databases, projects, pairing identities, or Connect
credentials. Repositories can be opened in either environment.
Older Basaltwater web units used `~/.t3`: their next web setup starts a separate
environment in the new directory and reports this change. The old data remains
untouched in `~/.t3`; back it up and plan any history migration separately.
This separation also prevents database contention if the desktop is later opened
while the web service is running; see the
[upstream duplicate-backend report](https://github.com/pingdotgg/t3code/issues/6097).
A port conflict still requires quitting the desktop or choosing another web port.

## Web service updates and recovery

If a previous checkout produced `has a bad unit file setting`, update
Basaltwater and rerun setup. Validate the generated unit with:

```fish
systemd-analyze verify "$HOME/.config/systemd/user/basaltwater-cachyos-t3.service"
```

The service follows the user session; lingering is not enabled. An existing
upstream `t3code.service` is refused without being stopped or adopted. Manage
that unit with its original installer before selecting either T3 mode. Preflight
checks effective systemd units in all user-unit search locations, as well as
local files, and refuses masks or unmanaged drop-ins on the Basaltwater unit.
Resolve those overrides before switching or updating. To stop this managed
service persistently:

```fish
systemctl --user disable --now basaltwater-cachyos-t3.service
```

Use `journalctl --user -u basaltwater-cachyos-t3.service` for startup errors.
The generic VM T3 pairing and update commands do not manage this unit. For a
deliberate runtime update, finish active work and rerun setup as yourself:

```fish
basaltw setup agent_cachyos localhost --web-interface t3code
```

Keep your selected host, port, and workspace options when rerunning. Each T3
setup stages `t3@latest` in a separate release directory, checks its CLI and
native terminal dependency, then validates the unit before stopping the old
service. Activation switches the CLI link and unit and starts the new runtime.
Even an unchanged T3 version is rebuilt and restarted so a Node upgrade does
not leave an incompatible native addon. Do not use `npm install --prefix` on
the managed root; it bypasses staging and can replace the stable CLI link.

Installation/validation failures leave the old service untouched. Activation
failures restore the previous runtime, unit permissions, and enabled/running
state. An interrupted activation leaves private recovery snapshots in
`~/.local/share/basaltwater/cachyos-t3/.activation`; the next setup retries
recovery before installing. If recovery is incomplete, retain that directory
and both runtimes while resolving the reported service error. Changes or
removals made to the unit or CLI link outside setup stop recovery and preserve
the snapshots for manual inspection.

Rollback covers runtime and service configuration, **not T3 database migrations**.
Back up application data before updates that may change its schema. Successful
updates retain the current and previous managed release, prune older marked
releases, and leave legacy npm files and unmarked directories alone. New files
created by the service use `UMask=0077`; existing personal data permissions are
not changed. Missing optional-provider warnings (for example, Claude on a
Codex-only installation) do not by themselves mean the selected provider failed.

See the upstream [T3 installation guide](https://github.com/pingdotgg/t3code/blob/main/docs/user/install.md)
and [remote-access guide](https://github.com/pingdotgg/t3code/blob/main/docs/user/remote-access.md)
for current provider, client, and T3 Connect requirements.

## Webhook notifications

To add setup-result webhooks while keeping your saved software and access
selection, first upgrade the installed CLI so it recognizes these new flags:

```fish
basaltw upgrade
basaltw refresh --notify webhook 'https://hooks.example.net/infra' \
  --notification-level normal --notification-strict-https --dry-run
basaltw refresh --notify webhook 'https://hooks.example.net/infra' \
  --notification-level normal --notification-strict-https
```

Replace the example URL with your receiver's full link. Webhooks send setup
success/failure results; no background notification jobs are installed.
`mailbox` is unsupported. The preview sends nothing and hides webhook URLs in
the displayed command. The actual refresh retains the full URL for delivery
and saves it privately only after setup succeeds. A failed run can notify the
targets provided for that attempt, while retaining the previous saved selection.

Repeatable `--notify` adds targets without exact duplicates. Omitted options
preserve saved targets, level, and HTTPS policy. Use
`basaltw refresh --notification-level off` to pause delivery; restore `normal`
to receive completion/failure results again. `warning` or `error` keeps only
failures. `--no-notification-strict-https` restores self-signed compatibility.
To replace or remove targets, run a full explicit `setup` with the desired
software/access selection and new targets, or omit `--notify` to remove them.

Targets, including any credentials in the URL, are retained in the user-owned
`0600` `last-setup.json` and `last-report.json`; keep both private. A receiver
failure warns without failing an otherwise successful setup.

The separate `0600` `last-notification.json` records aggregate delivery status,
target count, timestamp, and a digest of the target selection and policy. It
contains no endpoint URLs, tokens, or exception text. Run
`basaltw local cachyos-doctor` to inspect this evidence without contacting
receivers. Delivered means every configured target accepted the setup event;
failed can include partial delivery, and suppressed means the level required
no delivery. The doctor defers records from different selections or older
completed setups. Setups made before this record was introduced have no
historical delivery evidence. The setup report marks its own notification
pending because delivery happens after the report is saved.

Verify the event in the receiver after applying the flags; an accepted request
does not prove downstream processing. See the
[notification guide](NOTIFICATIONS.md) for payload, TLS, token, and retry details.

## Reruns, updates, and repositories

### Upgrade and repeat your last setup

After a successful `agent_cachyos` setup, use this from your KDE terminal as
the same user, without `sudo`:

```fish
basaltw refresh --dry-run
basaltw refresh
```

`refresh` upgrades Basaltwater to the latest source on the selected channel
(`dev` follows `main`), then starts the updated code with the last successful
setup selection. Version/commit channels remain pinned, just as with
`basaltw upgrade`. Setup runs even if Basaltwater was already up to date.
The dry run displays the saved command and setup plan without fetching source,
upgrading, installing, or changing the saved selection.

The private record is `~/.local/state/basaltwater/cachyos/last-setup.json`.
The adjacent private `last-report.json` records completion time, source commit
and channel (when available), selected tool/package versions, and diagnostic
observations including warnings. It is informational, not replay input. A
receipt failure warns without discarding a successful saved selection.
It saves supported setup options, including provider selections/exclusions,
T3 mode, optional tools, repositories, workspace, web bind/port, and webhook
targets/policy. It does not copy authentication files or save provider
credentials; webhook URLs may themselves contain receiver credentials. The record is
written only after all setup steps, including cleanup, succeed. A failed or
interrupted run and a dry run leave the previous successful selection intact.
Saved commands explicitly include or exclude every currently supported agent,
so changes to profile defaults do not select an unwanted agent during refresh.
Records written by older versions may omit optional-agent exclusions; repeat
your full setup command with the updated code to save those exclusions.

**First use after upgrading from an older checkout:** run `basaltw upgrade`,
then run your usual full setup command once. Older CachyOS runs did not save
their options, so refresh cannot recover them from installed packages. For
example, to establish the desktop selection:

```fish
basaltw setup agent_cachyos localhost --t3code-desktop --node --python --git-lfs
```

To add software without repeating your saved flags, use:

```fish
basaltw refresh --material-maker --etcher --butler --steamcmd --dry-run
basaltw refresh --material-maker --etcher --butler --steamcmd
```

Refresh accepts the supported CachyOS setup flags (`refresh --help` lists them).
Unspecified options are preserved. Boolean and single-value options override
saved values; repeatable repositories, access sources, and webhook targets are added without
duplicates. Agent selections/exclusions override their saved opposite for that
provider. `--no-access-source` clears saved explicit sources; combine it with
`--no-lan-access` to close managed remote access. Selecting a T3 mode replaces
the saved mode; switching to desktop clears the old web bind/port.
Conflicting or invalid new options stop before upgrade. A successful setup
saves the combined selection; after a failed attempt, repeat the added flags
when retrying. Omitting an option does not remove it; software `--no-*` flags
stop managing a selection, without uninstalling it.

An older installed CLI cannot recognize newly added flags. Run `basaltw upgrade`
once first to acquire this refresh interface and any new options.
Alternatively, rerun `setup` with all desired options. That successful run
replaces the record rather than merging with earlier selections.
Refresh follows the same update behavior as setup: managed agent CLIs update,
web mode updates/restarts its service, and an installed T3 desktop package is
retained for your normal AUR update workflow. Finish active work first.

Missing or invalid saved options stop refresh before the source upgrade.
An upgrade failure prevents setup. If setup fails after a successful upgrade,
Basaltwater stays upgraded and returns setup's failure status; resolve the
error and repeat `refresh`, or run an explicit setup to change the selection.
On CachyOS this repeats only the local `agent_cachyos` profile. It does not
upgrade CachyOS itself or redeploy saved remote hosts. Debian also supports
refresh, using its existing target-side record and root setup runner; see
[refresh on Debian](COMMAND_LINE.md#refresh-this-machine).

## Recorded workstation audit: 2026-10-07

The audited target was a physical Surface Laptop 6 running CachyOS, not a VM.
`systemd-detect-virt` returned `none`. Observations below were made from its
existing KDE Wayland desktop account; no OS update, service restart, portal
capture/input, or firewall change was performed in the initial read-only pass.
The follow-up used KDE polkit authentication for the mirror retry and privileged
firewall inspection described below.

| Area | Observation |
| --- | --- |
| OS and desktop | Kernel `7.2.9-1-cachyos`, Plasma/KWin `6.7.5`, Mesa `26.2.4`; booted kernel modules present. |
| Packages | `pacman -Dk` found no database errors; all packages required by the saved setup were installed. Selected core desktop, audio, Sunshine, and T3 packages had no missing files in `pacman -Qk`. |
| Tools | Saved agent, language, game-development, media, graphics, and desktop commands were present. The doctor previously omitted several of those command checks; this audit added them and Remmina dependency checks. |
| Graphics and media | `glxinfo -B` reported accelerated Intel Arc rendering; `vulkaninfo --summary` enumerated the Intel GPU. A 0.1-second FFmpeg synthetic-audio conversion to the null sink succeeded. These do not test GUI rendering, video encoding, or physical audio playback. |
| Audio and session | PipeWire, PipeWire Pulse, WirePlumber, KWin, portal, and accessibility prerequisites were active/observed. Native control was stopped; live portal consent and input remained unverified. |
| Capacity and clock | About 393 GiB filesystem space and 26 GiB memory available; NTP synchronized. |
| Mirror refresh | Initially failed during wake-time network reconnection. The authenticated retry completed at 12:22:43 CDT with `ActiveState=inactive`, `Result=success`; both Arch and CachyOS mirror lists were refreshed. |
| Updates | Local pacman sync metadata was dated October 7 and listed zero repository updates. No network refresh or AUR update check was performed; this does not establish that all software is current. |
| Sunshine | User service active with `Result=success` and no service restarts since October 5. Earlier Vulkan crashes are covered by the existing VA-API recovery guidance. No Moonlight stream was started or qualified. |
| Private state | Saved setup/report files were user-owned `0600`; the doctor reported private default T3 state directories and token-file permissions without reading credentials. |
| Network | T3 and Sunshine had non-loopback TCP listeners. Privileged UFW status and live IPv4/IPv6 input chains confirmed active default-deny incoming policy and managed LAN allows followed by covering deny guards. Remote client reachability remained unverified. |
| Storage encryption | Root was on a direct NVMe partition without a dm-crypt layer. The tooling profile does not configure disk encryption. |

The boot journal also contained an earlier `surface_aggregator` controller
warning and libinput touch-jump reports. The follow-up analysis above found no
owner-reported symptoms; the exact driver trigger remained unproven.
If battery, resume, or touchpad problems recur, retain the local
kernel/session journal and investigate through the distro's hardware support
workflow; do not change drivers or power policy merely to clear a diagnostic.
No user units were failed at audit time. The generic host summary checks unit
failures and kernel-module presence; it does not inspect kernel warning history,
prove hardware health, or replace application/client tests.

The mirror retry and privileged firewall inspection completed using the scoped
polkit commands above. The repository doctor then exited successfully with no
failed capabilities and zero failed system/user units. Its firewall observation
still correctly remains deferred because the doctor does not perform privileged
inspection itself. For a later recurrence, the equivalent KDE terminal checks
are:

```fish
sudo systemctl start cachyos-rate-mirrors.service
systemctl show cachyos-rate-mirrors.service -p ActiveState -p Result
sudo ufw status verbose
sudo ufw show raw
basaltw local cachyos-doctor
```

Follow-up inspection of saved rules and live UFW input chains found no additional
unrestricted accepts for new inbound connections to the managed T3/Sunshine
ports ahead of the guards. IPv4 allows for those services were restricted to
`192.168.68.0/22`, ahead of deny guards; Sunshine administration port 47990 and
IPv6 remote access had deny guards. The older broad T3 allows followed those
guards and therefore did not reopen the managed ports. Standard loopback and
established/related traffic accepts preceded user rules.
KDE Connect's separate TCP/UDP range `1714:1764` remained allowed from any
source in both families, as configured outside this profile. Decide its desired
network scope separately. These kernel-rule observations do not qualify
application access or replace a new connection test from the intended client.

The installed `basaltw` launcher used a separate source checkout at audit time.
Repository fixes become available there after `basaltw upgrade` on the `dev`
channel; an OS or full workstation refresh is unnecessary just to acquire
these read-only diagnostic improvements. A pinned channel needs its normal
deliberate channel update. The updated doctor was exercised directly from this
repository against the live saved selection.

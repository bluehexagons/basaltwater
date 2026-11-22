# Maintain T3 Code on CachyOS

[Topic index](cachyos-maintenance.md)

Use fish from the KDE session as the account that owns the installation.

## Direct T3 access across LAN subnets

`--lan-access` allows the default-route private subnet discovered on the
workstation; it does not allow every private address range. Combining it with
`--access-source` allows both the discovered LAN and the explicit sources. For
example, a workstation on `192.168.0.0/24` with
`--lan-access --access-source 192.168.68.0/22` allows both networks. Use
`--no-lan-access --access-source PRIVATE_IP_OR_CIDR` for explicit sources alone.
Include every intended routed network in the saved setup and preview changes
with `basaltw refresh --dry-run` before applying.

Access sources apply to all selected managed services, including Sunshine;
they are not a T3-only allowance. The router must route the client to the
workstation and permit traffic between networks. A host firewall allow cannot
create that route or bypass router isolation/NAT.

Test the workstation's current IPv4 address from the affected client. Check the
client's source address as seen by the workstation: a router that performs NAT
can present a source on the workstation's LAN instead of the client's original
subnet. IPv6 is outside these IPv4 source allowances. Inspect effective rules
with `sudo ufw status numbered` and `sudo ufw show raw`; saved rule files alone
do not prove that the live firewall permits a new connection.

T3 Connect and direct LAN pairing are separate connection modes. Use the T3
desktop's connection settings for Connect, or test a new connection from the
intended client after applying direct-source rules. See the upstream
[remote-access guide](https://github.com/pingdotgg/t3code/blob/main/docs/user/remote-access.md)

## T3 desktop updates

The desktop's embedded server uses the installed `t3code-bin` version. Desktop
`setup` and `refresh` check the selected AUR helper for a pending update and run
it with the helper's normal review prompts. Current packages are not rebuilt;
the helper's configured source and ignore policy determine available updates.
An update failure or unchanged package version stops setup before changing T3's
startup configuration.

`pacman -Qu` checks repository sync metadata and does not check AUR releases.
The Arch-Update version shipped as `cachy-update` on current CachyOS detects
paru, yay, and pikaur for AUR checks, but does not detect Shelly. With only Shelly
installed, an empty `cachy-update` result does not establish AUR freshness.

Update through the same AUR helper that installed the package. On a workstation
using CachyOS's default Shelly, run as your desktop user and complete its normal
package-review and sudo prompts:

```fish
pacman -Q t3code-bin
shelly list-updates aur
shelly update aur t3code-bin
pacman -Q t3code-bin
```

Run the update command when the check lists T3; a direct Shelly update can
rebuild an already-current package. Do not configure Shelly as Arch-Update's
`AURHelper`: their command-line interfaces differ.

Shelly 3.1.9's terminal question handler prints the PKGBUILD diff inline
before review approval, with a separate package-install confirmation in the
update flow. Basaltwater keeps those approvals attached to the terminal.
Shelly's `--no-confirm` also automatically accepts ordinary PKGBUILD
reviews, so it is unsuitable for removing only the final installation prompt
while retaining a chance to review and decline. `CollapsePkgbuildDiff` controls
unchanged diff context, not confirmation count. These behaviors are defined by
Shelly's [terminal question handler](https://github.com/Seafoam-Labs/Shelly-ALPM/blob/v3.1.9/Shelly.Cli.Zig/src/output/standard_single_pane.zig).
A separate preview-close input has also been reported during `basaltw refresh`
in a terminal. An isolated review-handler probe does not reproduce the entire
update flow, so it does not rule out that report. Record the helper/version,
prompt text and command used before changing review settings; do not assume
that an extra preview step came from a graphical updater.

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

## T3 desktop login startup

Desktop mode enables T3 Code at each KDE login. Existing desktop installations
acquire this behavior on their next setup or refresh:

```fish
basaltw refresh --dry-run
basaltw refresh
basaltw local cachyos-doctor
```

The entry is `~/.config/autostart/basaltwater-cachyos-t3.desktop`, or
`$XDG_CONFIG_HOME/autostart/basaltwater-cachyos-t3.desktop` for an absolute XDG
override. It uses the package-owned `/usr/bin/t3code` and KDE's normal login
environment. No user service or restart loop is added for the desktop app;
quitting it leaves it stopped for that session. Setup/refresh queues a detached
worker to gracefully close and relaunch a running app after all setup steps,
saved state, and notifications finish. It starts a stopped app when KDE's
graphical session and user-manager environment are available; otherwise its
journal reports that launch is deferred until KDE login. Inspect the printed
worker unit with `journalctl --user -u UNIT` on failure, then verify a provider
turn and terminal; queue acceptance alone does not establish readiness.
Before closing the app, the worker captures stable handles to its packaged
children, including the embedded server. It waits for both the desktop and
those children to exit within one 60-second deadline before reopening. The
backend can outlive the desktop window while releasing its data-directory
lock; reopening too early causes **This T3 home is unavailable**. On timeout,
the worker reports failure without force-killing processes or launching a
duplicate. Inspect its journal and allow shutdown to finish before opening
T3 manually. Never delete `server-owner.sqlite` to bypass a running server's
ownership lock.
When identifying desktop instances, the worker excludes Electron renderer,
GPU, utility and zygote children, including Chromium's flattened process titles.
Older workers could mistake
these for desktop instances and fail with `T3 desktop refresh failed: ValueError`
before restarting the app; upgrade Basaltwater and repeat refresh to repair this.
The entry's `TryExec` prevents login launch attempts after package removal.

Use **System Settings → Autostart** to disable T3 startup. The doctor reports a
disabled entry as advisory; the next desktop setup or refresh re-enables it.
Missing or modified entries and a missing native executable are reported as
failures. Setup refuses symlinks or unmanaged content at the managed filename;
inspect that file locally before retrying. Other personal startup entries are
retained. Remove duplicate personal T3 entries in KDE if you previously added one.

Verify startup with a later logout/login when convenient, then test a provider
thread and terminal in T3. The doctor observes startup configuration without
launching the app and cannot prove that a login launch succeeded. No automatic
login or pre-login desktop is configured.

## Switch modes on a later setup

`--t3code-desktop` and `--web-interface t3code` cannot be selected together.
Finish active work before switching:

```fish
# Web → desktop: install/verify the package, then disable the managed web service
basaltw setup agent_cachyos localhost --t3code-desktop --codex

# Desktop → web: quit the desktop app first to free its listening port
basaltw setup agent_cachyos localhost --web-interface t3code --codex
```

Desktop mode disables only the Basaltwater-owned user service and retains its
unit, runtime, and data. Web mode stages and starts the managed service again;
after successful activation it removes the managed desktop autostart entry.
A failed web activation keeps desktop login startup. The desktop package stays
installed. Omitting both flags leaves the current mode alone. Switching to web
mode requires you to quit the desktop first; desktop setup queues its graceful
reload when already running. Setup never force-kills the app or takes over an
upstream service.

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
basaltw setup agent_cachyos localhost --web-interface t3code --codex
```

Keep your selected host, port, and workspace options when rerunning. Each T3
setup stages `t3@latest` in a separate release directory, checks its CLI and
native terminal dependency, then validates the unit while the old service keeps
running. After all steps, saved state and notifications finish, an independent
user worker switches the CLI link and unit, starts the new runtime, and checks
HTTP readiness. Inspect its printed unit/journal; queue acceptance does not mean
activation succeeded. This ordering preserves local and remote T3 terminals
through setup's package-review and sudo prompts.
Even an unchanged T3 version is rebuilt and restarted so a Node upgrade does
not leave an incompatible native addon. Do not use `npm install --prefix` on
the managed root; it bypasses staging and can replace the stable CLI link.

Installation/validation failures leave the old service untouched. Activation
failures restore the previous runtime, unit permissions, and enabled/running
state. An interrupted activation leaves private recovery snapshots in
`~/.local/share/basaltwater/cachyos-t3/.activation`; the next successfully staged
setup retries recovery in its completion worker. If recovery is incomplete, retain that directory
and both runtimes while resolving the reported service error. Changes or
removals made to the unit or CLI link outside setup stop recovery and preserve
the snapshots for manual inspection.

A failed first-install rollback can remove its new unit before a systemd
reload fails. Preflight recognizes the cached, inactive unit only with an
owned, valid recovery record proving there was no previous service. Rerun
setup after resolving the reload error; its completion worker recovers before
activating web mode or switching to desktop mode. Keep `.activation` intact, and do not recreate
the removed unit manually.

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

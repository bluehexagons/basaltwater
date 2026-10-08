# Shared desktop setup audit — 2026-09-12

Status: historical diagnostic record. The resize/reconnect symptom remains a
live qualification item in the [single-session desktop plan](SINGLE_SESSION_DESKTOP.md);
the server-side cursor-cache explanation below is still a hypothesis, not a
confirmed root cause.

After another setup run, the operator confirmed that the first resize after
connection still causes reconnection. The apparent earlier improvement was
not a complete fix. The current VM has the updated configuration and a new
session (PID 325588). Xorg records a successful resize to 948×760, a connection
drop about 0.96 seconds later, and reattachment to the same X server. Subsequent
resizes to 1174×760 and 1074×760 complete without an immediate drop. A fresh
client trace is needed to confirm that this reproduction has the same cached
pointer error as the earlier captures. The source-level cursor-cache lead
below remains unconfirmed. Supporting
distribution-packaged Remmina remains the objective. No client downgrade is a
deployment requirement.

## Evidence and limits

The running VM uses Debian XRDP 0.10.6.1-2 and xorgxrdp 1:0.10.5-2, XFCE,
and software rendering. Remmina 1.4.43 runs FreeRDP 3.31.1 on CachyOS.
Native Wayland and XWayland logs both show a successful resize followed by
`Fastpath update Cached Pointer [a] failed`, then automatic reconnection.
The operator also reports reconnection with a 24-bpp profile; there is no
separate trace here verifying its negotiated depth or exact error.

The desktop remains PID 251008 with the same generation across these tests.
During this audit `desktop doctor` reports healthy, no active agent lease,
and geometry 1124×912. Neither XRDP service has a restart policy or drop-ins;
sesman has the intended managed unit with no frontend BindsTo dependency.
No desktop restart, logout, root configuration change, or packet capture was
performed for this audit. Root-only logs beyond the supplied artifacts and
live firewall state were not available for verification.

## Setup map and assessment

| Layer | Managed behavior | Assessment |
| --- | --- | --- |
| Composition | Desktop capability prepares/logout-checks the session, installs desktop/XRDP, applies XFCE/theme settings, then verifies service access | No resize-triggered setup execution |
| Packages | Debian Sid transaction, low-priority source, no recommendations, simulation blocks selected core upgrades/removals | An intentional distro departure; prefer stable/backports when both packages provide required fixes. Local metadata now offers XRDP 0.10.6.1 in Trixie backports, but not the required newer xorgxrdp. Do not blindly downgrade the pair |
| Authentication | Packaged PAM stack, Unix password for human, peer credentials for local sesrun; one owner group | No custom credential transport or resize hook |
| Session policy | MaxSessions=1, Policy=UB, retained disconnected sessions, no default idle timeout | Consistent with persistence; same PID excludes a session restart in captured failures |
| Services | Mask console display managers; decouple sesman from frontend lifetime | Necessary for one shared desktop; no periodic reconnect mechanism |
| Xorg launcher | Direct /usr/lib/xorg/Xorg, per-UID socket environment, trusted relative config path, private log path | Socket/log overrides address prior verified startup failures; preserve for now |
| AppArmor | Per-user socket and shared-memory access, selected render-node access | Prior startup/capture failures justify rules; setup currently overwrites the local include and ignores parser failure, which merits separate preservation/error-handling work |
| Xorg graphics | xrdpdev, explicit modes, optional supported DRM node, glamoregl loaded before xorgxrdp | Retain proven load-order/mode fixes; unsupported options corrected below |
| RDP transport | TLS, bitmap/bulk compression, fast-path both, new cursors, dynamic virtual channels | Fast-path/new-cursor defaults match upstream. Fixed TCP buffers removed. Clipboard remains allowed; drive/audio/RemoteApp blocked |
| Desktop startup | Private D-Bus, desktop identity environment, Python supervisor launches xfce4-session | No RDP connection or pointer-cache ownership; ordinary resize does not invoke teardown |
| Agent controls | Same-UID socket, pause/leases, explicit bounded X11 operations | Idle supervisor does not inject mouse events. Screenshot and geometry operations do not send RDP pointer-cache updates |
| XFCE | Disable Light Locker/display power management, direct notification autostart, clear saved display profiles | Some justified defaults, but overly broad file replacement and unsupported Xfwm settings need follow-up |
| Network protection | RDP ingress policy and AUTHFAIL-based fail2ban | Captured client parser error precedes server socket errors; immediate reconnect succeeds. Not evidence of a ban or listener restart |

## Corrections made in this audit

- Remove `SWCursor=true`: xorgxrdp 0.10.5 does not read that option. It did not
  disable RDP cursor caching or prevent cursor resize failures.
- Replace ignored `UseGlamor=false` with `DRMDevice=""` for software mode.
  The driver otherwise probes its default render node; an empty path makes
  that probe fail and selects software rendering. Explicit supported GPU
  selection remains unchanged. An externally supplied `XORGXRDP_DRM_DEVICE`
  environment variable can override the driver setting; the managed wrapper
  does not set it.
- Restore upstream `DefaultServerLayout="X11 Server"`, preventing another
  Xorg configuration fragment from selecting a different layout.
- Remove fixed 64-KiB TCP buffers. Upstream explicitly recommends allowing
  dynamic OS sizing; no evidence connects those buffers to this parser error.
- Remove ignored `max_idle_time` and `max_disc_time` from xrdp.ini. Actual
  session limits are already configured in sesman.ini.

The previous change removed the ignored `enable_gfx=false` setting. At the
initial audit these changes had not been applied to the live session. The
operator subsequently reran setup and reports improvement, as recorded above;
the causal change has not been isolated.

## Direct cursor-cache lead

Source inspection of upstream XRDP v0.10.6.1 shows this sequence:

1. `xrdp_wm_pointer()` assigns a dynamic cache index to `screen->pointer`.
2. Resize's `WMRZ_XRDP_CORE_RESET` calls `xrdp_cache_reset()`, which clears
   the cache structure, including dynamic pointer entries.
3. `WMRZ_XRDP_CORE_RESET_PROCESSED` reloads static pointers 1 and 0.
   This sets `current_pointer` to 0, but does not reset `screen->pointer`.
4. On a subsequent mouse move over the backend screen, `xrdp_wm_mouse_move()`
   compares those fields and can send the old dynamic index through
   `xrdp_wm_set_pointer()` before forwarding the mouse event to Xorg.
5. If that index has not been repopulated, a client with a cleared cache
   receives a cached-pointer reference without a corresponding pointer.

This is a concrete server-side invalidation path, conditional on cursor state
and event ordering. It explains a resize completing before the disconnect
and does not require GFX, native Wayland, or the infra-tools supervisor.
FreeRDP 3.31's cache recreation during reactivation can expose a stale server
reference that earlier clients happened to retain. This is a hypothesis for
the observed trace, not a packet-level confirmation or an assigned upstream
regression. The `[a]` in the error denotes the update type, not the cache index.

Next diagnostic work should instrument XRDP's reset, static-pointer reload,
pointer-definition sends and cached-pointer sends with index and resize state.
Then reproduce with the same distro client. A server regression test should
start with a dynamic screen cursor, resize, and move the mouse before a new
Xorg cursor definition arrives, asserting that every cached reference has a
definition in the new cache. A candidate server fix must reset/redefine the
screen cursor consistently; merely ignoring client errors or clearing all
cursor support would hide the problem. Do not ship a custom XRDP binary until
that regression test and live reproduction validate it.

## Additional cleanup backlog

The follow-up removes the unsupported `/general/Xfwm/Xinerama` and misplaced
`/general/Xfwm/theme` values written by earlier setup. It no longer forces a
HiDPI window-manager theme. Power and GTK settings now merge only managed
properties, with atomic writes and rejection of malformed/symlinked files.
Unrelated preferences survive reruns. Legacy displays.xml and xfsettingsd
overrides are deleted only when their contents exactly match the old generated
files; custom keyboard autostart entries are no longer deleted by filename.
Setup removes only exact known versions of the global `pm-is-supported` stub,
and no longer creates one to suppress warnings. Tests cover preservation,
idempotence, malformed XML, symlinks, and the integrated setup rerun.

Remaining items needing separate migration or live qualification:

- Xwrapper.config is not in the managed launch path: the launcher directly
  execs the real Xorg binary. Stop describing it as required for this stack;
  retiring the global override needs a migration that preserves other uses.
- The fixed Virtual 3840×2160 is not proof of a required RANDR limit. Compare
  upstream configuration in an isolated session before removing this boot-time
  difference; keep explicit startup modes and the verified module load order.

These are setup quality issues, not demonstrated causes of the cached-pointer
failure. They should not be bundled with an unverified server cursor fix.

## Live leftovers and package-source conflict

A further read-only inspection found that generic APT repair had commented out
`infra-tools-sid.sources`, despite XRDP setup deliberately creating it with a
priority-50 pin. This is a conflict between managed setup paths, not an operator
mistake. Repair now preserves the exact known source with its matching pin and
restores the exact commented form emitted by older repair runs. A matching
filename alone is insufficient. Desktop setup reconciles the source even when
packages are already installed, without an unnecessary metadata refresh.

Package installation now selects `xrdp/sid` and `xorgxrdp/sid` individually,
instead of making Sid the target release for the entire transaction. Ordinary
utilities use normal package selection. Core-upgrade checks normalize the
architecture suffix in APT's output (`libc6:amd64` was previously missed).
The guard remains a selected core-package denylist, not proof that every
possible future dependency transition is safe. Tests cover source preservation,
restoration, rejection of unpinned/altered exceptions, and package selection.

Live inspection also found:

- `dpkg -V xrdp xorgxrdp` reports only the three intentionally managed config
  files. No other package checksum differences were reported; gfx.toml has no
  local checksum modification.
- No extra XRDP command-line flags, frontend service drop-ins, or graphics
  environment overrides were found in the inspected files/user service manager.
- One Xorg/XFCE session is running. No Xvnc, competing Xwayland server, or
  Light Locker process appeared in the process check.
- `~/startwm.sh` is an old script with xset and dbus-launch tweaks, but is
  inactive: user window-manager overrides are disabled and the configured
  startup script is `/etc/xrdp/infra-tools-startwm.sh`. It was retained rather
  than deleting an operator-owned recovery artifact.
- The old global pm-is-supported stub is still present; the previous commit's
  exact-match migration will remove it on setup. Xorg's log confirms the new
  empty DRMDevice is selecting software mode. Configuration backups and two
  cached xfwm state files were retained; their presence alone is not evidence
  that they are executed or causing instability.

This inspection did not install packages, restart services, or alter the active
desktop. Root-only state beyond the available files remains outside the audit.

## Sources

- [XRDP resize state machine](https://github.com/neutrinolabs/xrdp/blob/v0.10.6.1/xrdp/xrdp_mm.c)
- [XRDP pointer and mouse handling](https://github.com/neutrinolabs/xrdp/blob/v0.10.6.1/xrdp/xrdp_wm.c)
- [XRDP cache reset and static pointer loading](https://github.com/neutrinolabs/xrdp/blob/v0.10.6.1/xrdp/xrdp_cache.c)
- [Upstream XRDP configuration](https://github.com/neutrinolabs/xrdp/blob/v0.10.6.1/xrdp/xrdp.ini.in)
- [xorgxrdp driver option parsing](https://github.com/neutrinolabs/xorgxrdp/blob/v0.10.5/xrdpdev/xrdpdev.c)
- [Upstream Xorg configuration](https://github.com/neutrinolabs/xorgxrdp/blob/v0.10.5/xrdpdev/xorg.conf)
- [FreeRDP cache recreation change](https://github.com/FreeRDP/FreeRDP/pull/13196)

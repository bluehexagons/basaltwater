# CachyOS maintenance and recovery

For a first installation, follow the [CachyOS setup guide](cachyos.md).
This reference covers an established Basaltwater installation. Run commands
in fish from the KDE session as the same desktop account, without `sudo`
unless a specific command requires elevation.

## Read-only diagnostics

Start with an attention-only inventory for routine agent diagnosis:

```fish
basaltw local cachyos-doctor --attention --json
```

It performs the full read-only inventory, returns every deferred or failed
observation, and adds `summary` counts (`total`, `available`, `deferred`,
`failed`). Available observations are omitted from `capabilities`; the retained
records keep their complete metadata. Deferred observations include expected
conditions such as unselected packages and task-scoped portal consent, so they
are not necessarily faults. The exit status still reports whether diagnosis
completed, rather than whether every capability is available.

Omit `--json` for readable counts and findings. Omit `--attention` to obtain the
complete inventory, including successful checks and versions. Neither form
starts applications, activates services, or verifies effective firewall rules.

## Choose a topic

<a id="privileged-checks-from-an-agent"></a>
<a id="installer-data-and-older-installations"></a>
<a id="mirror-refresh-failures"></a>
<a id="surface-resume-and-touchpad-warnings"></a>
<a id="aur-download-failures"></a>

- [CachyOS diagnostics and package recovery](cachyos-diagnostics.md) Privileged checks, installer data, mirrors, hardware warnings, and AUR fetches.

<a id="direct-t3-access-across-lan-subnets"></a>
<a id="t3-desktop-updates"></a>
<a id="t3-desktop-login-startup"></a>
<a id="switch-modes-on-a-later-setup"></a>
<a id="web-service-updates-and-recovery"></a>

- [Maintain T3 Code on CachyOS](cachyos-t3-maintenance.md) LAN access, desktop updates and login startup, mode changes, and web service recovery.

<a id="sunshine-service-and-encoder-checks"></a>
<a id="sleeping-monitors-and-moonlight"></a>

- [Diagnose Sunshine and Moonlight](cachyos-streaming.md) Service state, encoder checks, and display wake.

<a id="codex-updates"></a>
<a id="webhook-notifications"></a>
<a id="reruns-updates-and-repositories"></a>
<a id="upgrade-and-repeat-your-last-setup"></a>
<a id="system-restart-notices"></a>

- [Refresh the CachyOS profile](cachyos-refresh.md) Codex updates, notifications, saved setup reruns, and restart notices.

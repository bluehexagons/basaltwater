# Refresh the CachyOS profile

[Topic index](cachyos-maintenance.md)

Use fish from the KDE session as the account that owns the installation.

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
failures. HTTPS verifies the receiver by default. Supply
`--notification-ca-file PATH` for a private CA, or use
`--notification-allow-unverified-https` for explicit compatibility.
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
[notification guide](../services/notifications.md) for payload, TLS, token, and retry details.

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

`refresh` may run in a local or remote T3 terminal. CachyOS keeps the interactive
terminal for package-review and sudo prompts. Selected T3 startup, reload and
mode changes run independently after every setup step, saved result and
notification finishes. The printed worker unit provides completion status and
journal output; a queued job alone does not qualify the app or provider. The
final reload can disconnect that terminal, so save active work first. Concurrent
setup or completion work is refused until the current operation finishes.

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
basaltw setup agent_cachyos localhost --t3code-desktop --node --python --git-lfs --codex
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
Refresh runs the same `pacman -Syu` upgrade as setup. Managed agent CLIs update,
web mode updates and restarts its service, and desktop mode checks for T3 AUR
updates with interactive package review when an update is available. Finish
active work first.

Missing or invalid saved options stop refresh before the source upgrade.
An upgrade failure prevents setup. If setup fails after a successful upgrade,
Basaltwater stays upgraded and returns setup's failure status; resolve the
error and repeat `refresh`, or run an explicit setup to change the selection.
On CachyOS this repeats the local `agent_cachyos` profile. Debian also supports
refresh, using its existing target-side record and root setup runner; see
[refresh on Debian](../reference/command-line.md#refresh-this-machine).

### System restart notices

Successful setup and refresh print best-effort restart advice without rebooting
the workstation. Checks look for an existing reboot-required marker, missing
modules for the running kernel, and local package installation times newer than
this boot for that kernel, `glibc`, and `systemd`. An unused alternative kernel
alone does not trigger the package check. Install times also cover reinstalls,
so this is a conservative recommendation rather than proof that rebooting is
mandatory. Missing or unreadable metadata is reported as unknown or incomplete;
absence of a detected signal does not override CachyOS or firmware notices.
The restart observation itself is read-only and unprivileged.

The package dates use the documented
[ALPM local metadata format](https://man.archlinux.org/man/extra/alpm-db/alpm-db-descv1.5.en).
CachyOS also provides its own
[core-package reboot notification hook](https://github.com/CachyOS/cachyos-hooks/blob/master/cachyos-reboot-required.hook);
continue following its advice after system updates.

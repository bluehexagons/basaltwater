# CachyOS diagnostics and package recovery

[Topic index](cachyos-maintenance.md)

Use fish from the KDE session as the account that owns the installation.

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
installation must first use the [intermediate version](../migration-guide.md).
An existing `cachyos-t3` data directory at the default Basaltwater install path
is retained during reinstall.
Installer updates also carry forward managed `state`, `deployments`, and
`worktrees` directories.
If that path also contains other unmanaged files, move or resolve them before
rerunning the installer.

When the install directory contains `cachyos-t3/data`, stop the managed web
service before reinstalling source. Copying its live SQLite files can leave
the running service writing to the old source backup while the replacement
has an older database. Finish active work and use an independent KDE terminal,
outside T3's web terminal:

```fish
systemctl --user stop basaltwater-cachyos-t3.service
```

Then repeat your chosen installer command. The installer requires a confirmed
stopped service and an accessible systemd user session before replacing source;
it refuses running, restarting, or uncertain states. Keep it stopped during
source replacement. A requested web setup starts it afterward; otherwise, if
keeping web mode, start it after the installer finishes:

```fish
systemctl --user start basaltwater-cachyos-t3.service
```

The desktop's `~/.t3` data is outside the source directory and does not require
this service stop. If installation fails, resolve the reported error before
restarting the service against the restored source.

## Mirror refresh failures

If `health.mirrors` reports failure, inspect the distro service and the hosts
named in its journal before changing package or network settings:

```fish
systemctl status cachyos-rate-mirrors.service --no-pager
journalctl -b -u cachyos-rate-mirrors.service -n 60 --no-pager
systemctl list-timers cachyos-rate-mirrors.timer --no-pager
getent hosts HOST_FROM_ERROR
```

A successful lookup does not establish that DNS or HTTPS worked during
the failed run. Check the loaded local unit to see its current retry policy;
the service policy belongs to CachyOS and may change with normal updates.
Basaltwater observes it without installing retries, changing distro units,
or refreshing package databases.

Once connectivity is restored, retry the existing distro service from KDE:

```fish
sudo systemctl start cachyos-rate-mirrors.service
systemctl show cachyos-rate-mirrors.service -p ActiveState -p Result
basaltw local cachyos-doctor
```

A successful oneshot normally returns to `ActiveState=inactive` with
`Result=success`; clearing failed state alone does not rerun mirror refresh.
Resolve any new journal error, then use the normal full CachyOS update
workflow if needed. Do not use `pacman -Sy` or replace DNS servers to hide a
transient failure. A service with no recorded failure does not prove that its
mirror list or package sync metadata is current.

## Surface resume and touchpad warnings

Resume warnings and touchpad-jump messages are separate symptoms. A warning
after resume can indicate an ordering or device-state problem, but its timing
alone does not identify a fixed kernel or driver issue. Check whether battery,
wake, pointer, or gesture behavior is affected before changing kernel,
firmware, or device quirks.

Libinput discards touch coordinates it considers implausible. If the only
symptom is a log warning, that filter may be working as intended; visible
pointer jumps, freezes, or broken gestures need focused device investigation.
Use the current boot and user-session journals, identify the actual event
node, and follow the distribution's hardware-support procedure. Do not add
device-specific overrides based only on a warning.

## AUR download failures

A generic Shelly source-download error may omit the underlying Git failure.
Inspect the installed version, configured source, and cache ownership as the
desktop user before retrying:

```fish
pacman -Q shelly
shelly config get AurUrl
ls -ld -- "$HOME/.cache" "$HOME/.cache/Shelly"
ls -ld -- "$HOME/.cache/Shelly/t3code-bin" "$HOME/.cache/Shelly/t3code-bin/.git"
```

If the Shelly cache directory is root-owned, repair only that directory after
confirming it is the normal user cache and not a symlink or shared location.
Do not recursively change ownership across the home directory or delete
checkouts that may contain edits.

The package checkout and its `.git` directory can remain root-owned even when
the `Shelly` parent belongs to the desktop user. Git then reports **detected
dubious ownership**, which Shelly may reduce to `DownloadFailed`. Setup checks
the selected package's checkout directories before invoking Shelly, including
both default and absolute `XDG_CACHE_HOME` locations. It reports the affected
path without changing ownership or Git's trust settings.

For a confirmed root-owned `t3code-bin` cache, inspect the checkout tree for
symlinks, shared locations, and nested mounts first. Then repair only its
root-owned entries as the desktop user:

```fish
sudo chown --recursive --no-dereference --preserve-root --from=0:0 -- \
  (id -u):(id -g) "$HOME/.cache/Shelly/t3code-bin"
git -C "$HOME/.cache/Shelly/t3code-bin" status -sb
```

Use the affected package and cache path reported by setup; an absolute
`XDG_CACHE_HOME` override may place it elsewhere. Keep any local packaging
edits for review. A global `safe.directory` exception does not repair write
access, so it is not the recovery procedure.

To inspect source connectivity without building or installing, clone into a
new temporary directory with the same configured AUR base URL:

```fish
set -l aur_probe (mktemp -d)
and git clone -- https://aur.archlinux.org/t3code-bin.git "$aur_probe/t3code-bin"
```

This downloads packaging files only; do not execute them for diagnosis.
A successful clone does not rule out a cache, privilege, dependency, or later
application-download failure. Inspect the Shelly log path reported by the
installation error, then rerun the same full setup command.

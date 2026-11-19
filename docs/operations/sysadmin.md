# Sysadmin Convenience Commands

Quick-access commands for daily server administration tasks. All commands
inherit SSH credentials (username, key, port) from the saved Basaltwater
configuration for the host when available, so you rarely need to pass them
explicitly.

Commands started in a terminal can prompt for a password-protected key's
passphrase. For piped commands and parallel operations, preload the key with
`ssh-agent`/`ssh-add`; see [SSH authentication](ssh.md). This is especially
important for `fan`, `df`, and `reachable`, which may create several SSH
connections at once.

Non-interactive commands use a one-hour command deadline, including transfers,
mount operations, service changes, key installation, and each fan-out/upgrade
target. Health and one-shot logs use 120 seconds; reachability uses a 10-second
total bound in addition to its 5-second connection limit. Timeout terminates
the local process group and reports status 124 (aggregate commands report a
failed host). Completed remote mutations are not rolled back, and detached
remote work may require inspection before retrying. Interactive `ssh` and
explicit `logs --follow` sessions remain unbounded.

## Command Index

| Command | Summary |
|---------|---------|
| [`mount`](sysadmin-files-and-access.md#mount) | Mount a remote directory via sshfs |
| [`umount`](sysadmin-files-and-access.md#umount) | Unmount an sshfs mount |
| [`health`](sysadmin-host-services.md#health) | One-shot health summary for a remote host |
| [`ssh`](sysadmin-files-and-access.md#ssh) | Open an SSH session using saved config |
| [`push`](sysadmin-files-and-access.md#push) | Rsync a local path to a remote host |
| [`pull`](sysadmin-files-and-access.md#pull) | Rsync a remote path to local |
| [`key push`](sysadmin-files-and-access.md#key-push) | Install a local public key on a remote host |
| [`df`](sysadmin-host-services.md#df) | Multi-host disk usage table |
| [`fan`](sysadmin-host-services.md#fan) | Run a command on multiple hosts in parallel |
| [`svc`](sysadmin-host-services.md#svc) | Manage a systemd service |
| [`logs`](sysadmin-host-services.md#logs) | Show or follow journalctl output |
| [`upgrade`](sysadmin-host-services.md#upgrade) | Run apt upgrade across one or more hosts |
| [`reachable`](sysadmin-host-services.md#reachable) | Check which saved hosts respond via SSH |
| [`user rename`](sysadmin-user-rename.md#user-rename) | Rename a managed target login and reconcile its configuration |
| [`agent auth login`](../agents/agent-authentication.md#authorize-codex-on-a-target) | Authorize a target-owned Codex session from a minimal control system |

---

## Choose a topic

<a id="mount"></a>
<a id="umount"></a>
<a id="ssh"></a>
<a id="push"></a>
<a id="pull"></a>
<a id="key-push"></a>

- [System administration files and access](sysadmin-files-and-access.md) Manage mounts, SSH, file transfers, and key distribution.

<a id="health"></a>
<a id="df"></a>
<a id="fan"></a>
<a id="svc"></a>
<a id="logs"></a>
<a id="upgrade"></a>
<a id="remote-host-upgrade"></a>
<a id="reachable"></a>

- [System administration health and services](sysadmin-host-services.md) Inspect host health, disks, and logs, manage services, and upgrade a host.

<a id="user-rename"></a>

- [System administration user rename](sysadmin-user-rename.md) Rename a user and review home, group, and recovery behavior.

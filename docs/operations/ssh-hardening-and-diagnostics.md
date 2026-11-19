# SSH hardening and diagnostics

[Topic index](ssh.md)

## SSH hardening during setup

Setup validates SSH syntax and the effective authentication policy before
activating its managed drop-in. It selects the installed `ssh.service` or
`sshd.service`, reloads an active daemon when configuration changes, and starts
an inactive or failed service. An idle service with an active SSH socket is
left socket-activated. An unchanged rerun still checks service state.
After a reload or start, setup waits up to 30 seconds for transient `reloading`
or `activating` states to settle before checking the result. This also applies
to service recovery after a rollback. A state that does not settle still fails
setup and includes recent service journal entries when available.
If a reload leaves the listener stopped, setup attempts a start on the same
unit. It preserves existing unit enablement and socket activation choices.
OCI containers validate the configuration without invoking systemd.
If validation reports a missing `/run/sshd`, setup prepares a root-owned runtime
directory and retries validation before starting SSH.

If activation fails, setup restores the previous managed drop-in and any
managed legacy drop-in removed during migration, then attempts service recovery
with the restored configuration. It still reports the original failure and
shows recent service journal entries when available. Masked units and failures
of an active service require operator review.

If an earlier setup stopped at `service is reloading` or
`ssh.service is not active, cannot reload`, update Basaltwater on the controller
and rerun the same setup command. A `service is reloading` error immediately
after a successful reload can be an activation-check race even when SSH has
resumed listening. If SSH cannot accept a new connection, use the container or
host console to inspect the service first:

```bash
sudo /usr/sbin/sshd -t
sudo systemctl status ssh.service --no-pager
sudo journalctl -u ssh.service -n 40 --no-pager
```

Use `sshd.service` on systems that provide that unit name. The journal is
needed to distinguish a stopped daemon from a configuration, listener, or
service-unit failure.

## Managed build transport admission

Human and root SSH continue to require `remoteusers`. Managed builder and
target enrollment adds only the exact enrolled accounts' same-named primary
groups to the managed hardening drop-in's global `AllowGroups` directive.
There are no `bw-*` patterns or shared supplementary groups. Transport
UIDs/GIDs must remain exclusive, with no group aliases or explicit members;
passwords remain locked. Transport accounts never join `remoteusers`, which
also authorizes RDP. The builder worker gains no SSH or desktop access.
Root-owned forced-command keys and exact per-principal sudo rules continue
to enforce the protocol boundary.

Each role owns a mode-`0600` `ssh-admission` file in its private configuration
directory, with its hash in the schema-2 ownership marker. It contains only
literal enrolled primary-group names. Hardening reads completed inventories
and rechecks the recorded identities, group exclusivity and password lock
before preserving those groups. It checks effective key-only authentication
and `AllowGroups` for root, the setup user and each admitted transport account.
Missing, modified or unsafe inventories refuse hardening before policy writes.

Both installers update admission only while holding their existing role
maintenance ownership: a drained idle builder with the unit inactive, or an
exclusive target lease with the durable serving gate held. Existing hardened
hosts receive a change only to the current managed drop-in's `AllowGroups`
line, followed by syntax/effective-policy checks and service reconciliation.
Other SSH settings are preserved. Without that drop-in, enrollment records
intent for later hardening and leaves administrator SSH policy untouched.
Daemon policy edits share the nonblocking, root-owned mode-`0600` lock
`/etc/ssh/sshd_config.d/.basaltwater-ssh-admission.lock`; busy ownership refuses
the update. The lock inode is retained.

Existing schema-2 installs need one explicit installer enrollment rerun under
those guards to add the admission inventory. Hardening alone refuses active
enrollment without it. This does not adopt schema-1 markers or legacy SSH
layouts. Disable revokes the role's admitted groups, keys and sudo authority
while retaining identities, maintenance state and deployment/build evidence.
SSH activation failures restore the previous daemon policy, report failure
and leave role recovery guards in force. Mocked checks do not qualify live SSH;
validate new connections separately during native deployment.

## Troubleshooting

An older setup can change the firewall successfully and then fail in a later
step. Established SSH sessions can survive those changes while new connections
are blocked; a later setup failure does not roll back earlier firewall steps.
If the next run cannot connect, inspect both the SSH listener and the permitted
source networks rather than assuming the later failing service caused it.

For a Proxmox VM with a working QEMU guest agent, use the hosting node's root
Shell even when the guest has no root password and its coding user has no sudo.
For example, for VM 114:

```bash
qm guest exec 114 -- /usr/sbin/ufw status numbered
qm guest exec 114 -- /usr/bin/fail2ban-client status sshd
```

Replace `114` with the actual numeric VM ID. If UFW excludes the controller,
add an SSH-only allow rule for that exact source through the same guest-agent
interface, then rerun setup with an appropriate `--ssh-access-source` so the
intended access is retained. The privilege broker deliberately removes the
coding account's sudo access; see [privilege approvals](../agents/privilege-approvals.md).

Check which agent is active and whether it has the expected identity:

```bash
printf '%s\n' "${SSH_AUTH_SOCK:-no SSH agent socket}"
ssh-add -l
```

If a terminal command still fails, test the same identity directly without
forcing batch mode:

```bash
ssh -o BatchMode=no -i ~/.ssh/id_ed25519 admin@host true
```

If the command is being piped or run from a service, do not try to provide the
passphrase through standard input—the remote command or uploaded payload may
already be using it. Load the identity into the agent visible to that process
instead. A failure that says `Permission denied (publickey)` in a
non-interactive run commonly means that the key was not loaded into that
agent, or that the process received a different `SSH_AUTH_SOCK`.

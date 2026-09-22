# SSH authentication

`basaltw` uses the system OpenSSH client for remote commands, uploads,
Proxmox operations, rsync transfers, and sshfs mounts. A configured `--key`
is passed to OpenSSH as an identity file; Basaltwater never needs to store or
receive the key's passphrase.

## Password-protected private keys

When a command is started from a terminal, SSH is allowed to ask for the
private-key passphrase. This applies to setup and patch handoffs, Proxmox
commands, agent authentication and pairing, maintenance, transfers, and the
sysadmin shortcuts. A passphrase prompt can still appear while command output
is captured because OpenSSH uses the controlling terminal for authentication.
Interactive SSH subprocesses do not use the automation wall-clock timeout,
because it cannot distinguish time spent at that prompt from remote execution.
A newly provisioned guest can therefore wait for its setup key without the
operator watching the entire provisioning phase. If the SSH server closes an
interactive connection while setup is waiting at the remote-sudo verification
prompt, setup opens one fresh connection so the operator can enter the key
passphrase again. SSH connection establishment still has its own timeout, and
the command remains interruptible with Ctrl-C.

Streamed setup uploads also retain the controlling terminal in interactive
runs. SSH reads the passphrase directly from that terminal while standard
input carries the setup archive; no graphical `ssh-askpass` program or prior
`ssh-add` is required. The remote setup timeout still applies once the remote
command starts, but time spent entering the local key passphrase does not
consume it. Non-interactive uploads retain their local deadline and batch mode.

Commands started without a terminal—such as piped setup, automation, and
parallel host checks—cannot safely ask several processes for a passphrase. In
that case Basaltwater enables OpenSSH batch mode and retains bounded operation
timeouts. Load the key into an SSH agent before starting the command:

```bash
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_ed25519
ssh-add -l

basaltw setup workstation_dev 192.0.2.40 admin --key ~/.ssh/id_ed25519
```

The agent keeps the decrypted key in memory and does not save the passphrase
to disk. `ssh-add` prompts once in the terminal, after which all Basaltwater
SSH subprocesses inherit `SSH_AUTH_SOCK` and can authenticate without another
prompt. The agent must remain running for later commands; a new shell, reboot,
or agent restart may require `ssh-add` again.

For a desktop login, use the desktop's SSH-agent or keyring integration when
available. For unattended services, prefer a dedicated deployment key with
the narrowest authorized-key restrictions practical, or arrange an explicitly
managed agent environment. Do not put a passphrase in a command line,
repository, setup cache, systemd unit, or shell script.

## Repeated and parallel operations

Proxmox inspection and maintenance commands use a short-lived OpenSSH control
connection, so the first authenticated connection can be reused by subsequent
checks and by the streamed cloud-init or public-key uploads used during guest
provisioning. This reduces repeated prompts, but it does not replace an agent
for parallel operations such as `fan`, `df`, or `reachable`. Preload the key
when a command can open more than one SSH connection at a time.

The same terminal-aware behavior is used by SCP, rsync-over-SSH, SSHFS, and
other SSH uploads. A hosted setup may still prompt once for the Proxmox
identity and once for a different guest identity; loading both keys into the
agent avoids both prompts. SSHFS mounts may detach or reconnect after the
original terminal is gone, so an agent is the reliable choice for a long-lived
mount.

The managed UFW policy rate-limits SSH only when no inbound access sources are
declared. When `--lan-access` or `--access-source` identifies trusted management
networks, those source-restricted SSH rules allow connection bursts used by
setup, transfers, and host-key enrollment. SSH remains key-only and protected
by fail2ban. Rerunning setup migrates older source-specific `LIMIT` rules to
the trusted-source `ALLOW` policy without opening global SSH access.

Proxmox guests are a special host-key enrollment case. After provisioning,
Basaltwater scans the guest's ED25519 key from the authenticated Proxmox node,
replaces any stale plain or hashed entry for that address in both the workspace
`known_hosts` and the invoking user's default `~/.ssh/known_hosts`, and then
uses strict checking for the direct guest connection. It also refreshes both
files before every setup rerun for a cached managed guest whose saved
Basaltwater metadata matches the address, machine type, and Proxmox node. This
cleans up stale trust left by older or recreated managed guests before either
Basaltwater or plain `ssh HOST` performs a strict check. Existing guests
without that matching saved identity are not enrolled automatically; enroll
those explicitly with `basaltw ssh-key enroll` after verifying the
displayed fingerprint. Proxmox node and guest connections both use strict
checking against the workspace `known_hosts` file.
Explicit enrollment scans only the ED25519 key, avoiding a burst of parallel
probe connections against hosts that enforce SSH connection-rate limits.
The explicit enrollment command does not modify OpenSSH's default
`~/.ssh/known_hosts`; plain `ssh HOST` continues to use that separate file
unless setup has reconciled a matching managed Proxmox guest or the user's SSH
configuration selects the Basaltwater workspace file.

Hosted guest setup uses retained, key-only root SSH for both live route
preparation and the streamed setup upload. The coding account is never asked
for an interactive sudo password, and setup does not depend on that account's
current sudo policy. A standard VM user joins `sudo` without receiving a
temporary passwordless bootstrap rule. Pass `--nopasswd` to install and
validate `/etc/sudoers.d/basaltwater-USERNAME`, owned by `root:root` with mode
`0440`, when unrestricted non-interactive sudo is desired. The stable root
transport lets a later rerun add or remove that rule safely.
Patch commands preserve the saved choice when the flag is omitted. Pass
`--no-nopasswd` to remove the managed rule explicitly. Selecting `--nopasswd`
also exits a saved hardened posture; directly combining it with an enabled
hardening flag is rejected.
`--harden-agent` also removes the configured user from the `sudo` group.
It also reconciles other administrator and root-equivalent supplementary
groups. `--harden-user` implies that policy, locks Unix password
authentication even when the account previously had no password, makes the
home directory private, and removes sensitive system-data and device groups
while leaving authorized-key shell SSH usable. It also disables SSH agent,
TCP, Unix-socket, X11, and tunnel forwarding for that identity and prevents
`~/.ssh/rc` execution. A later root-driven setup with
`--no-harden-user --no-harden-agent` restores the account settings that
Basaltwater recorded before the lockdown and removes those per-user SSH
restrictions.
For an existing VM that was not provisioned by Basaltwater, install the
selected public key for root before the first setup. Basaltwater does not
accept, store, or transmit a sudo password. The configured non-root account's
managed `NOPASSWD` rule is present only when `--nopasswd` is selected.

## Troubleshooting

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

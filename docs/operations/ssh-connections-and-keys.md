# SSH connections and key management

[Topic index](ssh.md)

## First connection during setup

With working root SSH access, run the normal setup command from a terminal:

```bash
basaltw setup server_proxmox 192.168.0.24 root
```

When the target is new to the active workspace, OpenSSH displays its host-key
fingerprint and asks for confirmation during the setup connection. Compare
the fingerprint with the target's console or another trusted source, then
accept it. Setup saves the key in the workspace `known_hosts` file and
continues in the same command. A separate enrollment command is not required.
The prompt uses the controlling terminal, so it cannot consume the setup
archive being uploaded on stdin. Previously enrolled hosts connect without
another host-key prompt; changed keys still stop the connection.

Dry runs do not connect or enroll keys. Piped and unattended setup retains
strict checking; pre-enroll an independently verified fingerprint in the same
workspace before starting automation:

```bash
basaltw ssh-key enroll 192.168.0.24 --fingerprint SHA256:YOUR_VERIFIED_FINGERPRINT
```

Transfers, administration commands, and provider-node operations also retain
strict checking. They use the key saved by setup, or one explicitly enrolled
with `basaltw ssh-key enroll`. The workspace store is separate from ordinary
`~/.ssh/known_hosts`, so a first setup can prompt even when plain SSH already
trusts that address. Use the same `--workspace` for enrollment and setup when
selecting a custom workspace.

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

Post-setup Proxmox pending-configuration checks, VM restart/shutdown requests,
guest power preparation, and target restart checks also retain the controlling
terminal. They can prompt again if the earlier control connection has expired;
no graphical `ssh-askpass` program is needed. Restart readiness polling retains
its overall deadline, including any authentication needed to reconnect.
The prompt normally asks for the private key's passphrase. An account-password
prompt is available only if the remote SSH server permits password authentication;
managed hosts use key-only SSH.
Configuration recall/reconstruction and rolling-update checks use the same
terminal authentication behavior. Reconstruction still bounds remote execution
with its target-side timeout; rolling-update polling retains its overall deadline.

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

Managed control sockets are scoped to the SSH identity, server port, and
workspace host-key store. Their containing directory must be owned by the
invoking user with mode `0700`; symlinks, other owners, and unsafe permissions
stop connection reuse without changing the unexpected path.

The same terminal-aware behavior is used by SCP, rsync-over-SSH, SSHFS, and
other SSH uploads. Hosted setup can prompt separately for Proxmox and each
guest connection. Loading both keys into the agent avoids those prompts.
SSHFS mounts may detach or reconnect after the original terminal is gone, so
an agent is the reliable choice for a long-lived mount.

The managed UFW policy rate-limits SSH only when no effective SSH sources are
declared. When `--lan-access`, `--access-source`, or `--ssh-access-source`
identifies trusted management networks, those source-restricted SSH rules allow connection bursts used by
setup, transfers, and host-key enrollment. SSH remains key-only and protected
by fail2ban. Rerunning setup migrates older source-specific `LIMIT` rules to
the trusted-source `ALLOW` policy without opening global SSH access.

Use repeatable `--ssh-access-source IP[/PREFIX]` for controller addresses that
need SSH access without restricting public HTTP/HTTPS. IPv4 and IPv6 addresses
and CIDRs use the shared network validators. These SSH-only sources merge with
generic sources for SSH rules and peer protection only; they do not grant web,
RDP, Samba, or Proxmox management-set access. For example:

```bash
basaltw patch web-server --ssh-access-source 192.0.2.5/32
```

The list is saved for later deploys. Supplying sources on a patch replaces the
saved SSH-only list; omitting the option preserves it. Use
`--no-ssh-access-source` to clear it. Generic sources still apply after clearing;
without any effective SSH sources, the normal global SSH rate limit returns.
These options require a Debian profile that manages UFW. Proxmox uses its
native management IP set, and `server_wsl` has no managed ingress policy.
For a legacy saved Proxmox or WSL configuration containing SSH-only sources,
rerun its setup command with both SSH-only flags omitted.

Managed SSH filtering and Fail2ban expect the target daemon and any active or
enabled SSH activation socket to use TCP 22. Setup checks the effective
OpenSSH configuration and scans `Match` blocks and their includes for settings
that conflict with managed authentication and forwarding. A conflict identifies
the directive or service to correct before setup can activate its policy.

Before changing UFW rules or defaults, setup checks the current connection's
`SSH_CONNECTION` source against the combined generic and SSH-only access
sources. If the controller would be excluded, setup stops with an explicit `--ssh-access-source`
suggestion and leaves the firewall unchanged. IPv4 and IPv6 peers are checked;
local setup without an SSH connection has no remote peer to verify.
`--lan-access` describes the target's adjacent networks. A controller on a
routed Wi-Fi, VPN or management network needs an additional `--ssh-access-source`
for its observed address or trusted subnet.

When setup copies the target host's root-authorized keys into a login
account, it preserves existing entries and publishes the file with mode `0600`
under a user-owned `.ssh` directory with mode `0700`. Symlinked paths,
hard-linked key files and writable home directories fail the merge. Correct
the reported ownership or path before rerunning setup.

On a server with active UFW, source-restricted setup also requires a `deny` or
`reject` incoming default. Setup checks that policy before changing rules and
stops for operator review if the default is permissive or cannot be verified.
It does not change an existing server's default policy automatically. Review
`ufw status verbose` and the ports needed by existing services before changing
that default.

After reconciliation, setup checks numeric verbose rules, including expanded
application profiles, port lists, ranges and interface-specific rules, for
global access to restricted SSH, RDP and managed web ports. Conflicting operator
rules need explicit review; setup does not delete them automatically. Failure
to inspect or delete obsolete managed rules also fails setup instead of
reporting successful reconciliation. A previously inactive firewall is pruned
again after activation, when its saved rules become visible to UFW status.
These checks cover UFW's reported policy;
custom before/after rules, other firewall managers and upstream network policy
still require deployment-specific review and an external connection test.

### Proxmox guest host keys

For new and saved managed guests, Basaltwater reads the guest's Ed25519 public
host key through the authenticated Proxmox node. It uses the selected VMID
through the QEMU guest agent or the selected CTID through `pct exec`. The
guest must have a working management channel and the public key at
`/etc/ssh/ssh_host_ed25519_key.pub`.

Basaltwater compares plain and hashed entries in the workspace `known_hosts`
and the invoking user's `~/.ssh/known_hosts`. OpenSSH can save additional RSA
and ECDSA keys during an interactive login. Setup preserves those entries if
they match the public keys read from the guest's standard host-key paths
through Proxmox. It adds the verified Ed25519 key wherever that pin is missing.
A conflict in either file, a matching entry marked `@revoked` or
`@cert-authority`, or an unavailable guest Ed25519 key stops setup before route
repair and payload upload.
Hosted setup requires the workspace Ed25519 pin on TCP 22 for those SSH
connections and opens fresh connections to verify it. Saved `patch`, `deploy`
and `shares` uploads to managed guests also require the existing workspace
pin. These connections disable agent forwarding, X11, port forwarding and
SSH tunnels. A local SSH agent can still authenticate the connection. Use
hosted `setup` to verify the guest key through its node. An existing guest
without matching saved metadata requires explicit workspace enrollment.

### Deliberate rotation or address reuse

Identify the intended guest by its node and VMID or CTID. For a host-key
rotation, verify the new fingerprint through the guest console with
`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`. Inspect the address entries
in both trust files using `ssh-keygen -F GUEST_IP -f KNOWN_HOSTS_PATH`.
Update the affected pins to the verified key, preserving unrelated entries
and aliases. The workspace command `basaltw ssh-key enroll GUEST_IP
--fingerprint SHA256:VERIFIED_FINGERPRINT` can update the workspace pin when
the running guest already presents that key. Review shared entries before
replacing them. Plain `ssh` uses its own trust file, which you update
separately. Then rerun the hosted setup with the same workspace.

When reusing an address for a guest that has yet to be created, confirm the
previous guest is retired and deliberately remove that address's obsolete
pins from both files before provisioning. For a VM that could not supply its
key, restore the QEMU guest agent or its Proxmox connection before retrying.

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

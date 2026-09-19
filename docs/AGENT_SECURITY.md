# Agentic coding security

Agent coding machines intentionally balance a useful learning environment with
defense against common prompt-injection, package, and repository attacks. They
are not a malware-analysis sandbox. Treat the VM boundary, its credentials,
and its network access as part of the security design.

## Default and optional postures

| Posture | Linux account | Codex session | Intended use |
| --- | --- | --- | --- |
| Default | Member of `sudo`; password required | Auto-reviewed requests, workspace write access, no default shell network access | Interactive development and learning |
| `--nopasswd` | Unrestricted `NOPASSWD:ALL` on a VM | Same Codex policy as default | High-capability coding and manual administration |
| `--privilege-broker [PORT]` | No administrator groups or sudoers grants; narrowly registered privileged operations | Same Codex policy as default | [Separate browser approvals](PRIVILEGE_APPROVALS.md) and administrator allowlists |
| `--harden-agent` | Removed from administrator, host-control, and root-equivalent supplementary groups | Workspace write access; no approval, web search, credential-path reads, active browser/computer features, plugins, or MCP servers | Interactive evaluation of less-trusted code |
| `--harden-user` | `--harden-agent` plus locked password, mode-`0700` home, no sensitive system-data or device groups, no SSH forwarding/user rc, and no systemd lingering | Same hardened Codex boundary | Headless CI/CD and more restricted disposable evaluation |

`--nopasswd` cannot be combined with either hardened mode. Selecting it during
a patch exits a previously saved hardened posture; use `--no-nopasswd` to
remove it explicitly, while omitting either form preserves the saved choice.
`--harden-user` implies `--harden-agent` and cannot be combined with RDP because
XRDP needs an account password. Standard VM provisioning does not grant a
temporary passwordless rule. Key-only root SSH is the stable privileged setup
and recovery channel, while SSH password authentication remains disabled;
protect the authorized private key as a root credential.

`--harden-agent` does not lock the login itself. SSH and an explicitly
configured desktop remain usable. `--harden-user` locks Unix password
authentication, including an account that previously had no password, rather
than expiring the account, so authorized-key SSH stays available. It also
disables SSH agent, TCP, Unix-socket, X11, and tunnel forwarding for that user
and prevents `~/.ssh/rc` execution. It disables systemd lingering too; an
explicitly selected T3 Code service enables lingering again because that
service requires a persistent user manager.

Basaltwater journals group removals and the original wider account settings in
`/var/lib/basaltwater/agent-user-security/UID.json` before changing them.
Use `--no-harden-user` to return to agent-only hardening, or combine it with
`--no-harden-agent` to restore all recorded settings. An omitted hardening
option preserves the saved posture during a patch. The root-owned mode-`0600`
state follows the numeric user identity, so a Basaltwater user rename does not
orphan the rollback information. If a recorded group is temporarily absent,
the journal retains it and a later setup rerun retries the restoration. An
explicit rollback restores a recorded passwordless state too, so use the
hardened posture or set a real password instead when password authentication
must remain unavailable.

## Codex enforcement

When Codex is selected, setup writes root-owned system defaults to:

```text
/etc/codex/config.toml
```

The default policy selects `on-request`, the `auto_review` reviewer, and the
`:workspace` permission profile. These are low-precedence defaults, not
restrictions: users and clients can still select full access, YOLO-like modes,
or another supported profile. T3 Code may continue to request its upstream
full-access default. This preserves an explicit user choice while making direct
Codex sessions safer by default. `--nopasswd` deliberately keeps this capable
standard policy: it does not install a requirements file or disable login
shells, web search, apps, plugins, MCP servers, or browser features. Shell
network access still begins outside the workspace boundary and can be requested
through the normal approval flow instead of being silently enabled.

`--harden-agent` also writes `/etc/codex/requirements.toml` and selects `never`
within an basaltwater-defined workspace profile. That profile explicitly
disables command networking, so a user cannot re-enable it through the legacy
workspace network setting. In that mode there is no approval path to add
permissions. Live web search, login shells, apps and plugins, MCP servers,
native browser/computer use, app screenshots, remote control, and unmanaged
hooks are disallowed. The filesystem policy also denies common credential
locations such as Codex and other provider auth files, `.ssh`, `.gnupg`,
cloud/Kubernetes configuration, Git hosting credentials, password stores and
keyrings, package-registry credentials, container auth, `/run/secrets`, and
`.env` files. Package installation or another command that needs network
access should fail rather than gain it through automatic review.

As additional defense in depth, the hardened system default filters shell
environment variable names containing `KEY`, `SECRET`, or `TOKEN`. The current
Codex requirements schema does not make that environment setting an enforced
constraint, so do not inject sensitive environment variables into an
untrusted session and assume the filter is an isolation boundary.
Hardened setup also disables Codex's in-app updater; apply reviewed agent
updates deliberately with `basaltw agent update --tool codex`.

Hardened requirements are constraints, not warning preferences. Returning to
the default posture removes a Basaltwater-owned requirements file so all Codex
choices are available again. Administrator-owned defaults and requirements are
preserved in the default posture; hardened setup refuses to replace them, so
organization policy must be merged deliberately.

This policy covers Codex. Other provider CLIs retain their provider-native
permission model. T3 collaborative preview remains a distinct client
capability, but hardened Codex sessions cannot load MCP servers from their
configuration.

## Accepted convenience boundaries

- Root SSH stays enabled for provisioning, reruns, and recovery. It is key-only,
  restricted by the managed SSH firewall policy, and protected by fail2ban.
- T3 Code's plain HTTP listener remains available for clients that cannot use
  the private CA. Non-loopback access requires a private/non-global source
  allowlist and active UFW. Prefer the printed managed HTTPS URL whenever the
  client can trust the VM CA.
- Provider, Git, and GitHub credentials live under the coding identity.
  Hardened Codex blocks the common paths listed above, but other provider CLIs
  and arbitrary code running directly as that identity can generally read that
  identity's files. Hardened-user mode rejects inbound SSH forwarding, but do
  not expose personal agent sockets through another channel. GitHub token
  redesign and brokerage are outside this phase.
- Codex-enabled systems run an expiry-aware authentication check as the coding
  identity. It delegates refresh to Codex's own managed flow and never logs or
  copies token contents. This improves availability, not isolation: use a
  separate ChatGPT login stream per VM and revoke it if the VM is compromised.
- T3 Code and agent CLIs use their documented vendor distribution/update
  channels. This reduces arbitrary download sources but does not independently
  prove every upstream artifact or transitive dependency.

## Supply-chain work

For unfamiliar packages or unattended builds, prefer a fresh VM and enable the
hardened posture at creation:

```bash
basaltw setup agent_vm 10.0.0.40 agent \
  --provision-on pve1 \
  --harden-user \
  --git-access read \
  --no-browser-automation \
  --no-default-web-ports
```

Also use repository-specific credentials with the least practical scope, avoid
mounting personal shares, keep secrets outside the working tree, and restrict
VM egress at the hypervisor or network firewall when packages should not reach
the Internet. Snapshot or reprovision the VM instead of treating a clean Git
status as proof that untrusted install scripts made no persistent changes.

The hardened flags limit OS privilege and Codex capabilities. They do not
isolate projects from other files owned by the same user outside Codex,
inspect dependency behavior, constrain other provider CLIs, or protect
credentials already available to arbitrary processes under that UID. Use a
disposable identity or VM when those boundaries matter.

## Related documentation

- [Command-line reference](COMMAND_LINE.md)
- [Agentic VMs](AGENTIC_VMS.md)
- [Privilege approvals](PRIVILEGE_APPROVALS.md)
- [SSH authentication](SSH.md)
- [T3 Code server](T3_CODE.md)
- [Credentials overview](CREDENTIALS.md)
- [Agent authentication](AGENT_AUTHENTICATION.md)
- [Agent browser automation](BROWSER_AUTOMATION.md)

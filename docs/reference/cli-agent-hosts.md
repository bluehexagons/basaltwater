# Agent host setup flags

[Documentation index](command-line.md)

### Agent host flags

These flags prepare a Debian VM or local control plane for agentic coding. Their
availability remains profile-specific; `agent_cachyos` accepts only its local
workstation subset documented in [CachyOS](../workstations/cachyos.md). `agent_vm` is the
recommended terminal-only profile,
`agent_workstation` adds a desktop and Firefox ESR, and `agent_code_vm` adds
the common T3 Code web service, RDP, private source ranges, protected T3
pairing, read-write Git, active GitHub auth, and target-owned login when Codex is selected. Playwright remains an explicit
fallback through `--browser-automation playwright` for SSH-only or standalone
Codex and OpenCode sessions.
The [`agent_vm_lite` profile](../agents/agentic-vms.md#a-low-memory-coding-vm)
targets 2 GiB with an SSH-first baseline and opt-in T3 Code with device pairing.
The other Debian agent profiles default to GitHub CLI. Select at least one
coding provider explicitly with `--codex`, `--claude`, or `--opencode`.
`--agent-tool` values add to those
defaults and accept comma-separated lists; use `--no-agent-tool` to remove a
default. The full profile does not choose Proxmox capacity. It installs Node.js
automatically for T3 Code, while Go and other project runtimes remain optional.
The coding identity remains in the `sudo` group by default, but its sudo use
requires the account password. `--nopasswd` restores the former VM-wide
`NOPASSWD:ALL` policy for convenience, while leaving Codex's capable standard
feature set available. `--harden-agent` instead removes administrator and
root-equivalent supplementary groups. `--harden-user` adds
a private home, password lock, sensitive system-data/device-group removal,
disabled SSH forwarding and user rc, and disabled systemd lingering, making it
suitable for headless CI/CD and disposable evaluation; it cannot be combined
with RDP. Codex sessions use auto-reviewed workspace permissions by default;
standard sessions still allow an explicit user or client selection of full
access and YOLO-like modes. Hardened sessions stay in the workspace boundary
with no approval escalation and also disable live web search, common
credential-path reads, apps/plugins, MCP servers, native browser/computer use,
remote control, and unmanaged hooks. See [Agentic coding
security](../agents/agent-security.md) for the exact boundaries and supply-chain
guidance.

`--approval-panel [PORT]` provides a standalone option for an agent that needs
occasional, user-approved privileged work without a reusable sudo grant. It
uses the managed host at HTTPS port 9444 unless a port is specified.
It removes the coding account's privileged groups and rejects remaining
sudoers grants, then installs an independent HTTPS approval service with its
own password, without installing the main web panel or Nginx. The main web
panel includes approvals by default using its existing login, with a request
queue, exact command review, approve/deny decisions, and execution status.
Use `--no-privilege-broker` to disable the engine and `--privilege-broker` to
re-enable it. Main-panel installation preserves the selected account posture.
The standalone flag replaces the former `--privilege-broker [PORT]` syntax;
saved standalone configurations retain their listener.
See [Privilege approvals](../agents/privilege-approvals.md)
and the complete [Agentic VM examples](../agents/agentic-vms.md).
The shared CLI baseline includes [terminal Vim](../workstations/workstation-profiles-and-software.md#terminal-vim),
`ripgrep` (`rg`), `jq`, SQLite, `file`, `tree`, `make`, and `patch`. The
coding profiles also install ShellCheck. Select `--data-analysis` for Python
analysis tools and `--jupyterlab` for interactive notebooks. Image, audio, and video processing tools remain opt-in
through `--av-tools`; OpenGL inspection and API tracing remain opt-in through
`--gl-tools`.
Missing account and pairing passwords are requested securely from a terminal.
`server_lite` omits the standard firewall and generic CLI bundle, so use it
only when that lighter profile is intentional.

The agent setup model separates explicit tool installation, VM-level Git
policy, authentication payloads, and non-secret agent configuration. See
[agent authentication](../agents/agent-authentication.md) and [Git
access](../agents/git-access.md) for those workflows.
See [`browser-automation.md`](../agents/browser-automation.md) for the separate
Playwright runtime, MCP registration, and browser security model.

For a headless T3 Code VM without selecting a broader profile, use
`--t3code-ready`. It adds GitHub CLI, read-write Git, the T3 web
service, and protected pairing. Credentials remain opt-in through the normal
`--git-auth` and `--agent-auth` options.
When setup selects GitHub CLI alongside the T3 web service, it upgrades apt
packages below T3's supported GitHub CLI version. See
[server-side Git checks](../agents/t3-providers.md#server-side-git-checks).

```bash
basaltw setup agent_vm 10.0.0.10 agentuser --codex \
  --agent-config active \
  --git-access read --repo https://github.com/user/my_codebase.git
```

For a provisioned graphical coding VM, use the full profile while keeping
capacity and project runtimes explicit:

```bash
basaltw setup agent_code_vm 10.0.0.11 agentuser \
  --provision-on pve1 --name agent-1 \
  --memory 4G --cores 4 --storage root local-lvm 32G \
  --opencode --lan-access
```

The profile supplies T3 Code, RDP, read-write Git, active GitHub auth,
target-owned Codex subscription login, and T3 pairing. Access sources are deliberately explicit; the
example uses `--lan-access`, while a narrower deployment can use
`--access-source` or the service-specific source flags. Passwords omitted from
the command are requested with hidden prompts;
an empty pairing password reuses the target account password. Use
`--git-access none --git-auth none --agent-auth none` or the `--no-*` switches
when a deployment needs a narrower posture.

After [installing the launcher](../getting-started/installation.md), configure the local control plane:

```bash
sudo basaltw setup control_plane localhost "$USER"
```

To convert a Debian desktop to one shared XFCE/XRDP session, first log out
graphical sessions and run from SSH or a text console. Console graphical login
is disabled. Select the agent tools needed (GitHub CLI and Codex here):

```bash
sudo basaltw setup workstation_dev localhost "$USER" \
  --control-plane --agent-tool gh --codex --desktop xfce --rdp --rdp-existing-password
```

| Flag | Description |
|------|-------------|
| `--t3code-ready` | Add GitHub CLI, read-write Git, T3 web service, and protected pairing; requires explicit provider selection |
| `--nopasswd` / `--no-nopasswd` | Retain or remove unrestricted passwordless sudo for the VM setup identity; capability opt-in |
| `--privilege-broker` / `--no-privilege-broker` | Re-enable or disable the web panel's default approval engine; see [Privilege approvals](../agents/privilege-approvals.md) |
| `--approval-panel [PORT]` / `--no-approval-panel` | Enable or remove a separately authenticated standalone HTTPS panel at the managed host (port 9444 by default); main-panel approvals remain available when only this listener is removed |
| `--approval-panel-password [PASSWORD]` | Assign or rotate the standalone panel password; omitting the value prompts privately and the stored setup command excludes the password hash. Alias: `--privilege-broker-password`. A command-line value may appear in shell history and process arguments. |
| `--harden-agent` / `--no-harden-agent` | Apply or remove administrator/root-equivalent supplementary-group restrictions and the stricter agent policy |
| `--harden-user` / `--no-harden-user` | Apply or remove the password lock, private home, sensitive system-data/device-group restrictions, SSH forwarding/user-rc restrictions, and disabled lingering; enabling implies agent hardening and is incompatible with RDP |
| `--codex`, `--claude`, `--opencode` | Select coding providers; combine flags to install more than one; agent setups require at least one |
| `--agent-tool TOOL[,TOOL...]` | General tool selector (`gh`, `codex`, `claude`, or `opencode`), also accepted by existing saved commands |
| `--no-agent-tool TOOL[,TOOL...]` | Disable one or more default or saved agent tools |
| `--web-interface INTERFACE` | Install an explicit headless web interface; currently `t3code`; CachyOS uses its local user service |
| `--t3code-desktop` | Latest CachyOS only: install or check/update `t3code-bin` through the default Shelly CLI (paru/yay if absent), preserving package-review prompts; disable the Basaltwater web service and preserve data; mutually exclusive with `--web-interface` |
| `--web-interface-host IP` | Bind address for the selected web interface; `agent_vm_lite` defaults to loopback, other managed VM/server profiles default to loopback or `0.0.0.0` with a source, and CachyOS accepts loopback or an explicit private IPv4 |
| `--web-interface-port PORT` | TCP port for the selected web interface; default `3773` |
| `--web-interface-source IP_OR_CIDR` | Managed VM/server source restriction for HTTPS and any direct LAN access; repeatable; CachyOS uses `--web-interface-host` for direct LAN mode |
| `--no-web-interface` | Disable profile-provided web interfaces; on `agent_vm_lite`, also stop the managed T3 service and remove its ingress while preserving T3 data |
| `--no-web-interface-source` | Clear profile-provided web-interface source ranges |
| `--web-port PORT` | Manage an additional TCP web port through guest UFW; repeatable and source-restricted when generic sources are set |
| `--no-default-web-ports` | Disable the agent-VM defaults of TCP 80, 443, 8080, and 8081, already omitted by `agent_vm_lite` |
| `--device-pairing PROVIDER` | Install the protected browser enrollment portal for a provider; repeatable, currently `t3code`; unavailable for `agent_cachyos`, which uses native T3 pairing |
| `--device-pairing-port PORT` | Pairing portal port; default `3774` and must differ from the web-interface port |
| `--device-pairing-auth-file PATH` | Controller-local Nginx htpasswd file for the portal; transient and not saved |
| `--device-pairing-password PASS` | Controller-local portal password; hashed locally, transient, and not saved; the portal username defaults to the setup username. A command-line value may appear in shell history and process arguments; prefer a hidden prompt or password file. |
| `--no-device-pairing` | Remove the pairing broker, Nginx site, firewall rule, and installed portal password file from a saved host |
| `--browser-automation PROVIDER` | Install and register explicit agent browser automation; currently `playwright`, with selected Codex and/or OpenCode required |
| `--no-browser-automation` | Disable profile-provided browser automation |
| `--refresh-packages` | Force the APT update/upgrade and versioned runtime checks that normal reruns skip when their completion state is already present |
| `--git-access POLICY` | Set the VM's declared agent Git policy: `none`, `read`, or `read-write` |
| `--git-host HOST` | Select the GitHub CLI credential host; authenticated GitHub setup currently supports `github.com` |
| `--git-credential HTTPS_ORIGIN USERNAME` | Configure target-user Git and Git LFS authentication for one non-GitHub HTTPS origin using the matching workspace credential; repeatable |
| `--git-ca-certificate HTTPS_ORIGIN SOURCE` | Trust a local PEM path or retrieve one from an authenticated `ssh://USERNAME@HOST/ABSOLUTE_PATH` source, scoped only to one managed Git HTTPS origin; repeatable |
| `--no-git-credentials` | Remove all Basaltwater-managed Git HTTPS credentials, helper configuration, and private CA files from the target user |
| `--git-auth active\|none` | Seed missing active GitHub CLI host credentials, or disable a profile auth default |
| `--git-auth-file PATH` | Seed a missing selected-host `hosts.yml` entry or one-line GitHub token from a controller-local file |
| `--git-auth-token TOKEN` | Seed missing GitHub credentials with a classic or fine-grained access token; token is transient and omitted from saved commands |
| `--git-auth-credential NAME` | Resolve a GitHub token from the named workspace credential; save only the name for controller reruns |
| `--agent-auth login\|none` | Authorize Codex on the target (default for Debian agent profiles), or disable Codex login; independent of GitHub auth and other providers' explicit files |
| `--agent-auth-file TOOL PATH` | Stage one selected agent credential from a controller-local file at its canonical target path; `gh` accepts a hosts file or one-line token; setup otherwise preserves existing credentials, except for safe stale-Codex refresh; repeatable |
| `--agent-config active` | Copy known non-secret config from the active controller; does not copy auth files |
| `--interactive` | Prompt for tools, HTTPS repositories, Git policy, and credential sources |
| `--repo GIT_URL` | Clone an HTTPS repository below the selected agent workspace; repeatable |
| `--git-lfs` | Install Git LFS, initialize it for the target user, and do so before every requested repository clone |
| `--agent-workspace PATH` | Set the repository clone root; defaults to the target user's `~/repos` and may use a verified named-disk mount |
| `--backup SOURCE DESTINATION INTERVAL` | Configure a generic rsync-backed path mirror through the existing storage-ops service; repeatable |

On VM targets with agent features selected, guest UFW manages TCP ports 80,
443, 8080, and 8081 by default. Repeat `--web-port` for additional development
servers. `--no-default-web-ports` removes those agent defaults while retaining
explicit `--web-port` values. The rules are global when no generic access
source is set and source-restricted otherwise; applications must still bind a
reachable interface and provide their own authentication and transport
security. Local control planes, hardware, containers, and
`server_lite` and `agent_vm_lite` do not receive the defaults. An explicit `--web-port` still
enables the standard SSH-rate-limited UFW policy for `server_lite`.

GitHub credential input requires `--git-access read` or `--git-access
read-write`; `none` is the public/unauthenticated repository mode.
Choose one GitHub source. For a separate fine-grained token per VM, enter it
with `basaltw credentials set vm-1-github --service github --destination vm-1.example`, then pass
`--git-auth-credential vm-1-github`. The name is a local reference, not a GitHub
username. Explicit token and credential sources override active-auth profile
defaults. Prefer a saved credential or protected token file over a token in
shell history and process arguments. See [Git access](../agents/git-access.md).

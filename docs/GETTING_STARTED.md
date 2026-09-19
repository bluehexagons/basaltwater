# Try Basaltwater on a Debian VM

Start by previewing a setup, then try a few features on a disposable Debian
virtual machine (VM). You do not need Proxmox, a domain name, a GitHub account,
or an AI subscription for the first exercise.

## Before you start

Use a Debian VM with internet access, a regular user account, and working
`sudo`. Open its terminal through the VM console. Take a VM snapshot before
applying setup so you can return to the starting point. You can use an existing
VM from any virtualization tool; Basaltwater does not have to create it.

For this small development-server exercise, start with 2 virtual CPUs, 4 GB RAM,
and a 32 GB disk. A full desktop can also fit a 32 GB disk for limited use;
64 GB gives more room for projects. See the [hardware sizing table](../README.md#hardware-sizing)
for minimum, recommended, and performance tiers, including memory and swap.

A full setup changes system packages, SSH, firewall rules, and recurring
maintenance, including automatic updates and restart policy. Use a test VM
instead of your everyday desktop for this exercise. Installing only the
launcher does not apply a server or desktop setup profile.

Here are the terms used in the examples:

| Term | Meaning |
| --- | --- |
| Controller | The computer running the `basaltw` command. |
| Target | The computer being configured. In this exercise it is the same VM. |
| `localhost` | This computer, not another computer on your network. |
| Profile | A starting collection of setup tasks, such as `server_dev`. |
| Flag | An option such as `--node`, which adds Node.js development tools. |
| `--dry-run` | Validate and preview the requested setup without applying it. |
| `sudo` | Run a command with administrator privileges; enter your own password when prompted. |

Copy commands without adding a `$` prompt. `$USER` and `$HOME` are shell
variables: leave them as written. Password entry may show no characters; that
is normal. A backslash at the end of a line continues the same command.

## 1. Install and check the launcher

Follow [Install the launcher only](INSTALLATION.md#install-the-launcher-only)
inside the VM, then return here. Do not select a desktop conversion or local
setup during installation. Run:

```bash
basaltw --version
basaltw --help
```

You should see a version and the command help. If the command is not found,
follow [Verify the installation](INSTALLATION.md#verify-the-installation).
The examples in this checkout follow the `dev` channel; the latest published
`stable` release can have fewer features.

## 2. Preview a small development server

Run this as your regular user, without `sudo`:

```bash
basaltw setup server_dev localhost "$USER" --node --dry-run
```

`server_dev` selects the development-server baseline and `--node` adds Node.js,
npm, and pnpm. It does not select a desktop. Look for `Dry-run: Yes` and
`[DRY RUN] Would execute`. The preview validates the request and shows the
setup handoff; it does not test downloads or prove that installation will
succeed. Even if the output ends with `Setup Complete!`, a dry run has not
installed the profile or saved a configured host.

You can stop here or try other feature flags with `--dry-run` before changing
the VM. For example:

```bash
basaltw setup server_dev localhost "$USER" --python --dry-run
basaltw setup server_dev localhost "$USER" --godot --dry-run
```

## 3. Apply the setup in the test VM

When you are ready to change this VM, remove `--dry-run` and use `sudo`:

```bash
sudo "$(command -v basaltw)" setup server_dev localhost "$USER" --node
```

`$(command -v basaltw)` supplies the installed launcher's full path so
`sudo` can find a user installation. Keep the terminal open until setup
finishes; package downloads can take time. Successful live setup ends with
`Setup Complete!`. If it fails, read the first error and the final run notes,
fix the cause, and rerun the same command.

Open a new terminal as your regular user so the new shell environment loads:

```bash
node --version
npm --version
pnpm --version
```

Each command should print a version. See the
[installation troubleshooting](INSTALLATION.md#debian-package-sources) if
package downloads fail.

## 4. Inspect the saved setup and add a feature

This local exercise ran setup under `sudo`, so its saved configuration belongs
to root's workspace. Use `sudo` for the following saved-host commands too:

```bash
sudo "$(command -v basaltw)" list
sudo "$(command -v basaltw)" info localhost
sudo "$(command -v basaltw)" cmd localhost
```

`list` shows saved hosts, `info` shows configuration and run status, and `cmd`
prints the saved setup command. An ordinary `basaltw list` uses your own
workspace and may be empty; it does not mean setup failed.

Use `patch` to add a feature to the saved configuration. Preview Python tooling:

```bash
sudo "$(command -v basaltw)" patch localhost "$USER" --python --dry-run
```

Apply it by repeating the command without `--dry-run`. This preserves the
Node.js choice and reruns setup with Python tooling added. `patch` can revisit
shared setup steps; it is not merely a package-install command. In a new
regular-user terminal, check:

```bash
python3 --version
uv --version
```

## 5. Try another feature

Choose one row at a time. Substitute its flag for `--python` in the patch
example above, preview it, and then apply it if wanted. Run the verification
commands as your regular user in a new terminal.

| Try | Flag to add | Check after setup | Next guide |
| --- | --- | --- | --- |
| Go development | `--go` | `go version` | [Development flags](COMMAND_LINE.md#development-flags) |
| Godot game development | `--godot` | `godot --version` | [Godot](GODOT.md) |
| Audio and image tools | `--av-tools` | `ffmpeg -version` | [Development flags](COMMAND_LINE.md#development-flags) |
| A coding agent | `--agent-tool codex` | `codex --version` | [Agent authentication](AGENT_AUTHENTICATION.md) for login before model use |
| Godot web exports and an HTTPS gateway | `--godot-bundle web` | `basaltwater-web list` | [Publish a plain HTML page](INTERNAL_WEB.md#try-a-plain-html-page) |

The web bundle also installs Godot and its export templates. Publishing through
the gateway makes content reachable according to the VM's access policy; read
the linked guide before publishing private files. Coding-agent installation
does not sign you in or supply a provider subscription.

For other experiments, start with the prerequisites in the relevant guide:

- [File sharing with Samba](SAMBA_SHARES.md): a directory to share and user
  credentials.
- [A graphical workstation](WORKSTATIONS.md): desktop/RDP choices; this changes
  how you log into the graphical session.
- [Proxmox VMs](PROXMOX.md): an existing Proxmox node and storage choices.
- [Application deployments](DEPLOYMENTS.md): an application repository and its
  hosting requirements.
- [Agentic VMs](AGENTIC_VMS.md): headless, graphical, approval-panel, and
  hardened coding VM examples.

## Reset or move to another machine

Restore the VM snapshot to undo the experiment. `basaltw rm` removes saved
configuration only; it does not uninstall packages or reverse setup. Omitting
a flag on a later command is not a general uninstall mechanism.

To manage a separate Debian host, install the launcher on your controller and
follow [remote setup](INSTALLATION.md#install-and-configure-a-remote-host).
That path requires SSH key access for root and verified host-key enrollment.
The positional username names the target account to configure. Run remote
commands from your regular controller account; they use that account's saved
workspace and do not need local `sudo`.

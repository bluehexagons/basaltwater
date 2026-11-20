# Install or refresh the coding profile

[Documentation index](cachyos.md)

## Quick start

Finish the normal CachyOS installation, including KDE and the appropriate GPU
driver. Then open a terminal in your normal desktop session and run this as
yourself (without `sudo`). The setup phase upgrades repository packages before
installing the coding tools.

The commands on this page use **fish**, CachyOS's
[default login shell](https://wiki.cachyos.org/configuration/post_install_setup/#changing-the-default-shell).
Copy the whole block. The quoted download script runs explicitly under `sh`,
so fish never has to parse POSIX assignments or traps; your login shell stays fish.

```fish
sh -c '
  installer=$(mktemp) || exit
  cleanup() { rm -f -- "$installer"; }
  trap cleanup EXIT
  curl -fsSL --max-time 120 https://raw.githubusercontent.com/bluehexagons/basaltwater/main/install.sh -o "$installer" &&
  sh "$installer" "$@"
' sh --local-setup agent_cachyos --codex \
  --node --python --git-lfs
```

The package step may ask for your desktop user's sudo password. Keep the
terminal attached until setup finishes. This example installs the default Git,
ripgrep, build tools, GitHub CLI, and Codex plus Node.js, Python, and Git LFS.
The installer defaults to the `dev` channel while this profile is new.
The temporary download is removed when the command exits, including on failure.

No account, password, group, sudo, provider, or Git identity changes are made.
Log in to providers through their normal commands when needed:

```fish
codex login
gh auth login
```

Provider login does not configure Git commit identity. If readiness reports a
missing identity, set your own `user.name` and `user.email` with
`git config --global`, or configure them within each repository. Setup never
guesses an identity or changes an existing one. The default-identity check runs
outside a project; repository-specific overrides may differ.

`--git-lfs` initializes missing per-user LFS filters before cloning repositories.
Existing system/user filter values and repository hooks are preserved, including
custom filters. Readiness checks that the last (effective) value of each filter
setting is nonempty. Empty overrides are preserved and reported for you to
resolve; rerunning setup alone will not replace them. Test transfers in your
project to verify custom filters and remote authentication.

The launcher is `~/.local/bin/basaltw`; open a new terminal if the
installer's PATH change is not visible. Bash, Zsh, and Fish are supported.
Other shells need `~/.local/bin` and `~/.opencode/bin` added to PATH manually.

### Install with T3 Code desktop

Choose this installer instead of the basic quick start when you want Codex
inside the native T3 Code app. Run it once from your KDE terminal as yourself:

```fish
sh -c '
  installer=$(mktemp) || exit
  cleanup() { rm -f -- "$installer"; }
  trap cleanup EXIT
  curl -fsSL --max-time 120 https://raw.githubusercontent.com/bluehexagons/basaltwater/main/install.sh -o "$installer" &&
  sh "$installer" "$@"
' sh --local-setup agent_cachyos --codex \
  --t3code-desktop --node --python --git-lfs
```

This installs the launcher, Codex, GitHub CLI, T3 desktop, and the selected
project tools. Omit `--node --python --git-lfs` for just the default coding tools
and T3 desktop; add `--no-agent-tool gh` to omit GitHub CLI. Setup installs or
checks for updates to `t3code-bin` using CachyOS's default Shelly CLI and may
prompt for package review or sudo. T3 Code starts automatically at subsequent KDE logins.

For an installer-wide preview, add `--plan` before `--local-setup` in your
chosen block. This does not install packages, download the repository, or
change the launcher. Keep installer options such as `--channel dev` before
`--local-setup`, and setup options after it. Adding `--dry-run` after
`--local-setup` previews only the final setup phase; it still installs the
launcher and prerequisites.

After setup, open a new terminal, complete `codex login` and `gh auth login`,
then open T3 Code from KDE and verify a Codex thread and terminal. Refresh setup
queues a detached user worker only after all steps, saved state and notifications
finish. After a short delay it gracefully closes and relaunches a running app,
or starts a stopped app using KDE's graphical environment. If no graphical session
is available, its journal reports deferred launch until KDE login. Check the
printed user journal unit if the worker fails.
Queueing does not prove provider readiness: verify an authenticated thread and
terminal. If T3 cannot
discover Codex, set its provider **Binary path** to the absolute path printed
by setup, typically `/home/USER/.local/bin/codex`. Use
**Settings → Connections** for desktop connection settings.

## Choose a topic

<a id="pick-the-options-you-need"></a>
<a id="optional-webhook-notifications"></a>
<a id="codex-only-agent-setup"></a>

- [CachyOS install options](cachyos-install-options.md) Choose packages and tools, optional notifications, or a Codex-only setup.

<a id="ssh-access"></a>
<a id="install-the-administrator-ssh-helper"></a>
<a id="configure-ssh-access"></a>

- [CachyOS SSH setup](cachyos-install-ssh.md) Install the administrator SSH helper and configure remote access.

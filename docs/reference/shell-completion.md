# Shell Completion

The unified `basaltw` CLI supports full tab completion for Bash, Zsh, and
Fish. `tcsh` is accepted as a compatibility choice but has no generated
completion file; use Bash or Zsh for the complete command surface.

## Quick Setup

Install [`basaltw`](../getting-started/installation.md) first. Then install
`argcomplete`:

```bash
uv tool install --upgrade argcomplete
```

Before enabling completion, ensure `basaltw` and
`register-python-argcomplete` are on `PATH`. If either command is missing, add
its installation directory to `PATH`; open a new shell if you changed shell
startup files. Then run:

```bash
basaltw completions
```

The command installs completion for the consolidated launcher, including
`setup`, `patch`, `shares`, `recall`, `reconstruct`, `deploy`, `proxmox`,
`network`, `completions`, `python-tools`, and `credentials`.

## Manual Setup

### Bash

```bash
eval "$(register-python-argcomplete basaltw)"
```

### Zsh

```bash
eval "$(register-python-argcomplete basaltw)"
```

### Fish

```bash
register-python-argcomplete --shell fish basaltw > ~/.config/fish/completions/basaltw.fish
```

## System-wide Installation

```bash
sudo basaltw completions --global --shell bash
```

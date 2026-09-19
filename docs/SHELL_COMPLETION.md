# Shell Completion

The unified `basaltw` CLI supports full tab completion for Bash, Zsh, and
Fish. `tcsh` is accepted as a compatibility choice but has no generated
completion file; use Bash or Zsh for the complete command surface.

## Quick Setup

```bash
uv tool install --upgrade argcomplete
basaltw completions
```

This installs completion for the consolidated launcher, including `setup`,
`patch`, `shares`, `recall`, `reconstruct`, `deploy`, `proxmox`, `network`,
`completions`, `python-tools`, and `credentials`.

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

# Git access and authentication

This guide covers repository policy and HTTPS authentication for GitHub and
self-hosted Git servers. SSH login credentials are documented in
[SSH authentication](SSH.md).

## Declare the VM's Git policy

| `--git-access` | Intended behavior |
| --- | --- |
| `none` | No persistent authenticated Git access; public HTTPS clones still work |
| `read` | Repository work is intended to be read-only |
| `read-write` | Repository work may push changes |

This declaration controls Basaltwater behavior; it does not reduce the
provider token's permissions. Enforce least privilege at GitHub or the
self-hosted Git service.

Public repositories need no credential:

```bash
--git-access read \
--repo https://git.example.net/public/tools.git
```

## GitHub

A private GitHub repository needs all of these:

- `gh` selected by the profile or `--agent-tool gh`;
- one GitHub auth source;
- `--git-access read` or `read-write`; and
- a token authorized for the repository.

```bash
--agent-tool gh \
--git-access read \
--git-auth-file /run/secrets/agent-1/github-hosts.yml \
--repo https://github.com/example/private-project.git
```

Supported sources are `--git-auth active`, `--git-auth-file PATH`,
`--agent-auth-file gh PATH`, or the interactive token prompt. Select exactly
one. The active source reads the selected `hosts.yml` entry or asks an
authenticated controller-local `gh` command for a keyring-backed token.

A file source may contain a `github.com` entry from `hosts.yml` or a one-line
token. Basaltwater extracts only the selected host entry. Authenticated GitHub
setup currently supports only `github.com`; use the managed origin flow below
for another HTTPS Git server.

On initial setup, Basaltwater appends a missing GitHub host entry without
removing other hosts. It preserves an existing selected entry on ordinary
reruns. When authentication succeeds, it runs `gh auth setup-git` and fills
missing global `user.name` and `user.email` from the controller or authenticated
account. It does not copy the controller's complete `.gitconfig`.

Use `basaltw agent auth set HOST USER --tool gh ...` for deliberate
replacement. See [Agent authentication](AGENT_AUTHENTICATION.md) for status,
rotation, and file portability.

## Self-hosted HTTPS and Git LFS

Save the password in the workspace store, then bind its username to one exact
HTTPS origin:

```bash
basaltw credentials set agent-git

basaltw setup agent_vm 192.168.0.41 agent \
  --git-access read-write \
  --git-credential https://192.168.0.51:3000 agent-git \
  --git-ca-certificate https://192.168.0.51:3000 \
    ssh://gitadmin@192.168.0.51/etc/nginx/ssl/192.168.0.51.crt \
  --git-lfs \
  --repo https://192.168.0.51:3000/team/project.git
```

`HTTPS_ORIGIN` contains only `https://`, a host or IP, and an optional port.
Paths, embedded credentials, HTTP, queries, and fragments are rejected. The
credential name must match the Git server username. Prefer a separate
least-privilege account for each VM or trust boundary.

The optional CA source can be a controller-local PEM bundle or an SSH URL.
Local files must be regular, non-symlink, at most 1 MiB, and not group/world
writable. SSH sources use strict host-key checking and non-interactive `sudo`
when required. Enroll and verify an unknown SSH host key first:

```bash
basaltw ssh-key enroll 192.168.0.51
```

Never bypass a trust failure with `http.sslVerify=false`.

The target receives a URL-scoped Git include and a mode-`0600` credential-store
file below `~/.config/basaltwater/git/`. The scoped helper leaves GitHub's `gh`
helper intact. Standard HTTPS and Git LFS use the same origin credential.

Updating the workspace password and rerunning setup rotates the managed
origin. Remove every Basaltwater-managed Git credential and private CA setting
with:

```bash
basaltw patch 192.168.0.41 agent --no-git-credentials
```

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Private GitHub clone fails | `gh` selection, one auth source, token repository access, and `--git-access` |
| Active GitHub source is missing | Install and authenticate `gh` on the controller, or use a protected file |
| Self-hosted authentication fails | Exact origin scheme/host/port, matching workspace credential name, and server account permissions |
| Private CA fails | Source path, SSH host-key enrollment, non-interactive remote access, and the origin match |
| Git LFS fails while Git works | Confirm `--git-lfs` and that the same managed origin covers the LFS URL |

Keep tokens outside shell history and repositories. Use separate credentials
when VMs need independent scopes, audit identities, or revocation. See the
[Credentials overview](CREDENTIALS.md) and
[Agent authentication](AGENT_AUTHENTICATION.md).

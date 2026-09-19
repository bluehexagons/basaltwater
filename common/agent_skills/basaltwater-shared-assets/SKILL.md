---
name: basaltwater-shared-assets
description: Work safely with SMB or SSHFS asset shares and Git LFS from a Basaltwater agent VM.
metadata:
  managed-by: basaltwater
---

# Shared assets and Git LFS

Identify the storage layer before moving or editing large files:

```bash
findmnt --target PATH
git lfs env
git lfs status
```

Replace `PATH` with the asset or project path. Do not print mount credential
files or include credentials in URLs.

Keep active Git worktrees, `.git` directories, package caches, build trees,
databases, and service state on local ext4 or xfs storage. CIFS and SSHFS have
different locking, case, executable-bit, symlink, notification, and disconnect
behavior. Use mounted shares for large source assets, deliberate import/export,
staging, or archives. Copy files to a local worktree before workflows that need
local filesystem semantics, then publish finalized artifacts back deliberately.

Git LFS uses the repository's HTTPS LFS endpoint and credentials; a mounted
share does not replace it. Use ordinary `git lfs pull`, checkout, and push
workflows through the Git remote. Do not create a `file://` remote on a share or
move the local LFS cache there.

For a Basaltwater-managed self-hosted Git origin, inspect only non-secret
configuration when troubleshooting:

```bash
git config --includes --get-regexp '^(credential\..*\.username|http\..*\.sslCAInfo)$'
```

Managed credentials and private CA settings live below
`~/.config/basaltwater/git/`. Do not print the credential file. Do not add
credentials to repository URLs or disable TLS verification; have an operator
rerun setup with `--git-credential` and, when needed,
`--git-ca-certificate`. Operators can use a host-key-verified
`ssh://USERNAME@HOST/ABSOLUTE_PATH` certificate source instead of manually
copying the public certificate through the controller.

Before writing a shared artifact, confirm the intended destination and whether
another user or job owns it. For a completed archive or export, write a
temporary sibling and rename it only after the copy and any checksum complete.
Do not reconfigure persistent mounts, invent share credentials, or delete
shared content unless the user explicitly requests that external change.

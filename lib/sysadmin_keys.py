"""SSH public key installation helpers."""

from __future__ import annotations

import os
import sys
from typing import Optional

from lib.sysadmin_process import run_command
from lib.cache import load_setup_command
from lib.ssh_utils import build_ssh_command


def _resolve_credentials(
    host: str,
    username: Optional[str],
    ssh_key: Optional[str],
) -> tuple[str, Optional[str]]:
    config = load_setup_command(host)
    if config:
        if not username:
            username = config.username
        if not ssh_key:
            ssh_key = config.ssh_key
    return username or "root", ssh_key


_INSTALL_KEY_SCRIPT = """\
set -e
KEY='{key}'
mkdir -p ~/.ssh
chmod 700 ~/.ssh
touch ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
if grep -qF "$KEY" ~/.ssh/authorized_keys 2>/dev/null; then
    echo "Key already present in authorized_keys — nothing to do."
else
    printf '\\n%s\\n' "$KEY" >> ~/.ssh/authorized_keys
    echo "Key added to authorized_keys."
fi
"""


def run_key_push(
    host: str,
    username: Optional[str] = None,
    ssh_key: Optional[str] = None,
    pubkey_path: str = "~/.ssh/id_ed25519.pub",
) -> int:
    pubkey_path = os.path.expanduser(pubkey_path)
    if not os.path.isfile(pubkey_path):
        print(f"Error: public key file not found: {pubkey_path}", file=sys.stderr)
        return 1

    with open(pubkey_path) as f:
        pubkey = f.read().strip()

    if not pubkey:
        print(f"Error: public key file is empty: {pubkey_path}", file=sys.stderr)
        return 1

    username, ssh_key = _resolve_credentials(host, username, ssh_key)

    # Single-quote the key in the remote script; escape any single quotes in it
    escaped_key = pubkey.replace("'", "'\\''")
    remote_script = _INSTALL_KEY_SCRIPT.format(key=escaped_key)

    cmd = build_ssh_command(host, username, ssh_key, batch_mode=False, remote_command=remote_script)
    print(f"Installing public key on {username}@{host}…")
    result = run_command(cmd)
    return result.returncode

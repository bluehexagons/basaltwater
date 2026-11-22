# SSH authentication

`basaltw` uses the system OpenSSH client for remote commands, uploads,
Proxmox operations, rsync transfers, and sshfs mounts. A configured `--key`
is passed to OpenSSH as an identity file; Basaltwater never needs to store or
receive the key's passphrase.

## Choose a topic

<a id="first-connection-during-setup"></a>
<a id="password-protected-private-keys"></a>
<a id="repeated-and-parallel-operations"></a>
<a id="proxmox-guest-host-keys"></a>
<a id="deliberate-rotation-or-address-reuse"></a>

- [SSH connections and key management](ssh-connections-and-keys.md) Set up a first connection, load encrypted keys, and manage guest host keys.

<a id="ssh-hardening-during-setup"></a>
<a id="managed-build-transport-admission"></a>
<a id="troubleshooting"></a>

- [SSH hardening and diagnostics](ssh-hardening-and-diagnostics.md) Review setup hardening, build transport admission, and troubleshooting.

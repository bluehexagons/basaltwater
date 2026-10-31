# Proxmox setup troubleshooting

The `server_proxmox` profile configures the hypervisor as `root`. The controller's
Unix username and any supplied setup username are normalized to `root` before
configuration defaults, password hashing, transport and saved configuration.
This also applies when loading an older saved Proxmox setup.

## Management command

Setup installs `/usr/local/bin/basaltw` and verifies that `basaltw --help` runs
against `/opt/basaltwater/basaltwater.py`. Installation happens before the
optional panel, so a later panel failure does not prevent CLI access. It uses
the system Python and does not install coding agents. An unrelated executable
or symlink at the command's destination stops setup and remains untouched.

If an older installation has no command, inspect its entrypoint directly:

```bash
python3 /opt/basaltwater/basaltwater.py --help
```

Rerun the saved Proxmox setup from an updated controller to install and verify
the launcher. Check that `/usr/local/bin` is on the shell's PATH if the absolute
command works but `basaltw` is still unavailable by name.

## Panel rejects a supplied password

The panel login for this profile is **root**, with its separately configured
web-panel password. It is distinct from Proxmox's `root@pam` login and the Unix
root password. Adding the panel does not change those passwords.

Older controllers could hash `--web-panel-password` for their own setup username
and then change the target username to `root`. The target correctly rejected
that payload with “Web panel auth must contain exactly the setup username”.
Update the controller and rerun the saved setup or patch with the requested
panel password. Updating only target files does not repair an incorrectly
generated controller payload.

For example, enable a panel on a port separate from Proxmox's reserved 8006:

```bash
basaltw patch pve.example --web-panel 9443 --web-panel-password 'YOUR-PANEL-PASSWORD'
```

The password argument is transient and hashed before upload; a literal command
line value can appear in shell history or process arguments. Subsequent reruns
without this flag preserve a valid existing root panel credential. The target
continues to require exactly one matching user and rejects malformed or unsafe
authentication files; do not weaken these checks or add extra users to bypass
the error.

## Updates and restart warnings

`sudo basaltw local update` detects Proxmox independently of a saved profile.
It uses the same checked, non-removing upgrade as Proxmox setup under the shared
maintenance lock. It does not use the generic Debian autoremove path. See
[local maintenance](local-maintenance.md) and [recurring maintenance](maintenance.md).

Running guests, a pending kernel reboot and an unavailable previous boot
journal can appear as maintenance warnings during an otherwise successful
setup. They do not cause the username mismatch above. Plan guest downtime and
review autostart settings before rebooting a hypervisor; adding a panel or
running a local update does not authorize guest shutdown or a reboot.

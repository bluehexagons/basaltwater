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

## Management firewall compilation warnings

Setup checks the selected native firewall before changing management sources
and again after enabling the cluster firewall. The legacy `pve-firewall`
compiler can report invalid rules on stderr while exiting successfully, so
those warnings still stop setup. The error includes the compiler command,
exit status and up to 2,048 characters of diagnostics. Inspect the full output
on the Proxmox host:

```bash
sudo env LC_ALL=C pve-firewall compile
```

For a host configured to use the nftables backend, use:

```bash
sudo env LC_ALL=C /usr/libexec/proxmox/proxmox-firewall compile
```

Older Basaltwater versions also treated Perl locale warnings as firewall
configuration warnings and hid the diagnostic. This could happen when SSH
forwarded a locale unavailable on the host. Updated controllers run the
compiler probe with the portable `C` locale, without changing the host's locale
or ignoring rule warnings. Update the controller before rerunning setup.

If compilation fails after activation, setup preserves the previous managed
access sources and restores a previously disabled cluster firewall. An already
enabled firewall remains enabled. Inspect and correct the reported rule or
backend problem before retrying; do not disable validation to continue setup.

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

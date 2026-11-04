# T3 Code 0.1.7 platform boundaries

Use the selected environment's installed version and live controls. A desktop
package update, an updated skill bundle, and a verified provider turn are
separate observations. These source-reviewed workflows still need native
Windows/WSL and CachyOS client qualification.

## Desktop command installation

Basaltwater installs the native Windows desktop, not a separate npm `t3` CLI.
For policy diagnostics, first inspect `Get-Command t3` and `t3 --version` in
PowerShell. To expose the desktop's bundled command, choose **Settings → About
→ t3 command → Install**, then open a new terminal. On Windows this adds the
app's command folder to PATH; on Linux it installs a link. An existing npm
command is preserved, so verify which version resolves before using it.
Do not install a second runtime merely to make `t3 policy show` available.

Run `t3 policy show` as the account running the intended server. It compares
installed policy with what that server loaded; a client reconnect does not
reload policy. For CachyOS's isolated web environment, use its
[managed CLI and data directory](../../basaltwater-cachyos-t3code/references/web-service.md)
rather than the desktop command. Keep policy output in private evidence.

Contract: [bundled command](https://github.com/pingdotgg/t3code/blob/v0.1.7/docs/user/install.md#the-t3-command).

## Windows policy and Ubuntu WSL

On an unmanaged Windows machine, choose the distro in **Settings → Sharing &
access**. T3 prepares its own WSL server runtime; the first connection after an
app update can take longer. Ubuntu provider CLIs, authentication and skills
belong to the selected Linux account, separately from native Windows tools.

When Windows managed policy is present, 0.1.7 deliberately disables the desktop's
WSL environments. Invalid or unreadable policy also leaves them unavailable.
Successful Ubuntu setup or `wsl.exe` discovery does not override that restriction.
Ask the administrator to review policy and the intended environment; do not
remove policy or add a WSL service to restore a missing desktop connection.

An independently authorized server inside WSL reads the distro's
`/etc/t3code/policy.json`, not Windows registry/file policy. Deploying a Linux
policy does not enable the managed Windows desktop's WSL integration.
Basaltwater's `server_wsl` profile does not provision that independent service.

Contracts: [WSL connection](https://github.com/pingdotgg/t3code/blob/v0.1.7/docs/user/install.md#windows-subsystem-for-linux),
[policy boundary](https://github.com/pingdotgg/t3code/blob/v0.1.7/docs/user/managed-policy.md#what-a-policy-does-not-cover),
and [availability check](https://github.com/pingdotgg/t3code/blob/v0.1.7/apps/desktop/src/wsl/DesktopWslEnvironment.ts).

## Debian systemd configuration paths

Upstream 0.1.7 discovers the user manager's persistent configuration directory
from its D-Bus `UnitPath`, falling back to an absolute `XDG_CONFIG_HOME` or
`~/.config`. The manager's directory can differ from the invoking shell's.
Basaltwater's Debian drop-in, reconciliation records, log wrapper and diagnostics
currently require the default `~/.config/systemd/user` directory.
Setup checks the manager's paths before changing T3 configuration or invoking
its installer. A relocated or unverified directory stops reconciliation.

Before setup/refresh on a machine with relocated user configuration, inspect
both the loaded unit and the manager's search paths as the service account:

```bash
systemctl --user show t3code.service --property=FragmentPath --value
busctl --user --json=short get-property org.freedesktop.systemd1 \
  /org/freedesktop/systemd1 org.freedesktop.systemd1.Manager UnitPath
```

If the unit or persistent configuration root is relocated, retain its files
and pause Basaltwater T3 reconciliation for that installation until path support
is added. Copying a unit into the default directory does not change the manager's
configuration root. This limitation concerns Debian's upstream service adapter;
CachyOS owns a separate service and uses its platform-specific reference.

Contract: [upstream manager discovery](https://github.com/pingdotgg/t3code/blob/v0.1.7/apps/server/src/cloud/bootService.ts).

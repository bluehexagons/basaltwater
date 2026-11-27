# Switch channels and maintain the installation

[Documentation index](installation.md)

## Channels and upgrades

The installer downloads the repository into a managed local worktree. The
launcher stays installed while the worktree's channel changes:

| Channel | Selects |
| --- | --- |
| `stable` | Latest `vMAJOR.MINOR.PATCH` release tag |
| `dev` | `main` branch |
| `v<version>` | One exact tag, such as `v2.0.0` or `v2.0.0-rc.5` |
| `branch-<branch>` | Any existing branch |
| `commit-<hash>` | One exact commit |

The v2.0 preview sequence uses Git tags `v2.0.0-rc.N`, with matching Python
package versions `2.0.0rcN`. Select a published preview with
`basaltw channel v2.0.0-rc.5` and update with `basaltw upgrade`. The `stable`
selector matches plain three-part version tags. Release candidates are for
qualification and feedback.

Inspect or change the selected channel:

```bash
basaltw channel
basaltw channel stable
basaltw channel dev
```

Update the local installation to the newest commit on its selected channel:

```bash
basaltw upgrade
```

The default installer channel is `dev`, which tracks `main`. Until final
`v2.0.0` is published, `stable` resolves to historical `v0.2.0`. The current
installer refuses that historical source. Use `dev` or an exact preview tag
for a current Basaltwater install. `upgrade` refuses to overwrite local
worktree changes. Commit or stash changes before reinstalling or upgrading.

System installs use the official Basaltwater GitHub repository over verified
HTTPS. The administrator authorizes that repository to publish updates to the
selected channel after installation. System fetches ignore the invoking account's
Git configuration, credentials, and URL rewrites. Fresh system installations
and writable-checkout migrations require a full commit channel or
`--source-commit` from independent review. User-owned controller checkouts can
still use an alternate origin for remote administration.

The system checkout and runtime under `/opt/basaltwater` are root-owned.
`--user` selects the account for user tools and completions. Run system
channel changes as root using `sudo /usr/local/bin/basaltw channel CHANNEL`
or `sudo /usr/local/bin/basaltw upgrade`. Run local Debian setup through that
system launcher. A user-owned controller checkout can preview local setup
without elevation or drive setup on another host over root SSH.

Channel Git commands have a five-minute deadline and disable terminal credential
prompts. A timeout terminates the local command group and leaves saved channel
state unchanged; inspect `git status` and `HEAD` before retrying an interrupted
checkout. Configure a credential helper or SSH agent before accessing private
remotes.

`--install-dir` must name a dedicated application directory. The installer
refuses symlinks, mount points, home directories, broad system paths, and
unmanaged existing directories. Successful installs record
`.basaltwater/managed-install` for later reinstalls. Recent infra-tools
installations require the [intermediate-version cutover](../migration-guide.md) first;
the installer refuses to create a parallel namespace beside an old installation.

Reinstallation preserves private channel state alongside the source-owned
`.basaltwater/cleanup.json` policy. The policy follows the selected source;
local edits to it are refused like other source changes. Other tracked files
under `.basaltwater` still cause a managed-data refusal. If an older installer
rejects a clean checkout solely because this policy is tracked, download the
current installer and rerun the same command.

Activation failures and HUP/INT/TERM interruptions restore the previous
protected installation. The installer keeps staged or failed source alongside it for inspection
and prints recovery paths if restoration fails. SIGKILL or power loss cannot
run shell traps: inspect the adjacent `.backup.*`, `.new.*`, and `.failed.*`
directories and restore a verified, root-owned backup to the install path before retrying.
Source rollback does not undo package or launcher changes already made by
bootstrap. Run only one installer for a given destination at a time.

To migrate a writable legacy system checkout, run the reviewed installer
as root with an explicit channel and full commit, or rerun setup from a trusted remote
controller. Migration quarantines the old checkout under a root-private
`.basaltwater-quarantine.*` directory beside the install path. Durable
`/var/lib/basaltwater` state remains in place. Source and other data inside the
writable checkout stay in quarantine for manual review. If activation is
interrupted after displacement, rerun with trusted source. Restore any
reviewed application data separately.

These channel commands manage the controller installation. A remote `setup`
or `patch` run sends a complete snapshot of the controller's current source to
the target and replaces the target's `/opt/basaltwater` runtime with it while
preserving `/opt/basaltwater/state`. The snapshot includes its project version,
source commit, branch, and whether the controller checkout was dirty, so
`basaltw channel` on the target reports exactly what that setup run
deployed. To replace a snapshot from the controller, switch or upgrade the
controller channel first, then rerun `setup` or `patch` to update the target.
Alternatively, [Debian refresh](../reference/command-line.md#refresh-this-machine) downloads
the snapshot's branch into a managed target checkout and repeats saved setup.
Downloaded source is made readable before activation even under a private
administrative-job umask; Git/channel metadata and retained runtime data keep
their existing private permissions. The previous source remains in a private
adjacent backup. A failed setup can follow successful source activation, so
inspect both the installed source and setup result before retrying.
Subsequent managed-checkout refreshes reconcile only runtime source permissions;
retained state and application data are excluded.
The root `state` link is ignored as a path rather than a directory, keeping the
converted checkout clean for later upgrades. A broker-enabled Debian refresh
restores its approval hostname from the protected policy and verifies its port
and requester UID, preserving the existing HTTPS approval origin during replay.

## Unsupported orchestration hosts

The installer can also install the remote-management launcher on another Linux
distribution, such as Fedora, but this remains outside the support guarantee.
It asks for an explicit `[y/N]` confirmation, does not attempt to install APT
packages, and requires these controller commands to already be available:

- `python3`
- `git`
- `ssh`
- `rsync`
- `curl`

Install the equivalent packages with the host distribution's package manager
before rerunning the installer. The uv bootstrap also requires `curl`, so
`wget` alone does not satisfy the installer prerequisites. CachyOS has its own limited
[local coding setup](../workstations/cachyos.md), including prerequisite installation, and does
not use this unsupported-host confirmation path.

The installer skips local system-package bootstrap on unsupported hosts. Do not
use `--local-setup` or `--qemu-guest-agent` there; install the launcher and use
remote-management commands such as `setup`, `patch`, `ssh`, `push`, and `pull`.
Remote setup profiles and target-side package operations remain Debian-oriented.

## Debian package sources

The installer and setup need network access to APT. On Debian, they check the
release codename and archive keyring, disable active CD-ROM-only entries, and
ensure the current release uses the official mirrors:

- `https://deb.debian.org/debian` for the base and updates suites;
- `https://security.debian.org/debian-security` for security updates.

If a minimal or offline Debian installation has only installation media
configured, Basaltwater creates a managed source file and runs `apt-get
update` before installing packages. Existing `non-free-firmware` components
are preserved. Existing source files are backed up, and an unmanaged
`basaltwater-debian.sources` file is not overwritten. Existing current Debian
base and security entries are reused; the managed file is limited to any
missing suite, and a redundant managed file from an older installer run is
removed. An existing entry must be enabled, include binary packages and the
case-sensitive `main` component,
and associate the official mirror with the current suite in the same line or
deb822 stanza. CD-ROM cleanup affects only its active entry; other stanzas in
the same source file are retained.

The installer and setup print APT progress directly. Setup package operations
wait up to five minutes while another package operation releases the APT lock,
including an automatic update that starts during setup. During that bounded
wait, setup should continue after the other operation finishes instead of
failing immediately. Keep the terminal open until the installer reports its
completion message. If a previously started installer was interrupted, rerun
the same command after checking that no other `apt` or `dpkg` process is still
active.

If this step fails, fix network, DNS, proxy, or mirror access and rerun the
same installer command. Do not work around a failed package-list update by
leaving a CD-ROM as the only source.

For byte-stream progress, add `--apt-install pv` to your saved setup or
[request the package from a running agent](../agents/privilege-approvals.md#request-software-for-a-debian-agent).
Rsync transfers use rsync's own progress display. Debian desktop setup installs
`xdg-utils` for opening files and links in the desktop session.

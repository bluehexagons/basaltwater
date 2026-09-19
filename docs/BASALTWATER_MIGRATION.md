# Basaltwater v2 rename: upgrade and rollback

Basaltwater, formerly infra-tools, uses distribution name `basaltwater` and
command `basaltw`. All command verbs and options are unchanged. `infra-tools`
remains an installed transition command through v2.x; both launchers report
`basaltw 2.0.0` from `--version`. No `basalt`, `bw`, `b6`, or `infra_tools`
executable is added.

## Git-managed installations

For an existing development-channel installation, run as the installation owner:

```sh
infra-tools upgrade
infra-tools bootstrap --skip-system-packages
basaltw --version
basaltw channel
```

The first command updates source; the second installs the new user launcher
and completion. To refresh the system launcher too, use
`sudo infra-tools bootstrap --user "$USER"` instead of the second command.
Bootstrap can install or update its normal prerequisites. CachyOS users must
run bootstrap as their desktop user, without sudo.

Before v2.0.0 is tagged, `stable` still selects an older release. A pinned user
can select `infra-tools channel dev` before bootstrap to try the renamed
development source. After publication, select `infra-tools channel v2.0.0`.
Keep the previous commit from `infra-tools channel` for rollback.

Alternatively, rerun the [installer](INSTALLATION.md) with the same
`--install-dir`, user and shell, and a channel containing this change. Default
source directories remain `/opt/infra_tools` for root installs and
`~/.local/share/infra_tools` for user installs. Always pass an existing custom
path explicitly. There is no automatic discovery or relocation of a second
installation at a new branded path. Only run one installer per destination.

An unmarked legacy source tree needs `--migrate-existing-install`. Dirty Git
worktrees are refused. The installer preserves `state/` and `.infra_tools/`,
keeps a private adjacent source backup, and restores the previous source if
activation/bootstrap fails or receives HUP, INT or TERM. Rerunning after a
successful install is supported. After SIGKILL or power loss, inspect the
printed/adjacent `.backup.*`, `.new.*` and `.failed.*` trees and restore the
old backup to the original path before rerunning. Do not delete recovery data
until the active source and launchers have been verified.

For a Git rollback, select `basaltw channel commit-PREVIOUS_COMMIT` using the
recorded full commit. The two wrappers invoke the retained `infra_tools.py`
path, so they still work against the old source. To undo an installer source
activation, preserve the failed/current tree and restore the adjacent backup
to the original path. Neither operation reverses system packages installed by
bootstrap. If you applied unrelated setup changes after upgrading, use their
own documented recovery procedures before downgrading.

Remote targets receive the controller source on the next normal setup/patch.
Agent setup installs both launchers. Existing unmanaged executable launchers
are retained, and symlinked destinations are refused. Check `command -v
basaltw` and `basaltw --version` if an existing user command shadows the managed
one. No service/timer, sudoers, lock, readiness route or skill ID changes in
this cutover, so existing scheduled work and older agent guidance continue to
use their established contracts.

## Python-package installations

Download/build the new wheel before removing the old package. In the same
isolated Python environment that owns the old installation:

```sh
python -m pip uninstall infra_tools
python -m pip install /absolute/path/to/basaltwater-2.0.0-py3-none-any.whl
basaltw --version
infra-tools --version
```

Do not co-install the distributions and then uninstall `infra_tools`: their
runtime modules and transition launcher overlap, and pip would remove files
belonging to the new install. Ordinary `pip install --upgrade infra_tools`
does not discover a differently named distribution. For pipx/uv tool installs,
use that tool's uninstall followed by install from the new wheel; do not mix
package managers in one environment.

Keep the previous wheel for rollback. Uninstall `basaltwater` first, then
reinstall that previous `infra_tools` wheel. The new `basaltw` console script
is removed by uninstall; use `infra-tools` after package rollback. If a new
install fails, reinstall the saved old wheel before resuming automation.
Workspace state and credentials remain outside package ownership and are
not removed by either uninstall.

Release artifact checks support:

```sh
python3 scripts/check_wheel_artifact.py --previous-wheel /path/to/old.whl
```

This builds the current wheel and checks fresh installation by default. When
supplied the previous wheel, it exercises uninstall-before-install and package rollback
outside the source tree in a temporary virtual environment.

## Environment and state

Installer settings accept `BASALTWATER_REPOSITORY_URL`, `BASALTWATER_CHANNEL`
and `BASALTWATER_REF`. Each new spelling overrides its `INFRA_TOOLS_*`
equivalent when set. Either channel setting takes precedence over refs;
explicit `--channel`/`--ref` options take precedence over the environment.
An explicitly empty channel or repository URL fails validation; an empty new
ref suppresses the old ref. With no selection, a reinstall reuses its saved
channel, and a fresh install uses `dev`.

Other runtime `INFRA_TOOLS_*` settings, `~/.config/infra_tools`,
`/opt/infra_tools/state`, ownership markers, locks, JSON keys, `infra.json`,
credentials and recovery records are deliberately retained. There is no
`BASALTWATER_WORKSPACE` setting or implicit new state path. Use the existing
`--workspace` option for an explicit location. Help, status and inspection do
not copy or move state. See the [contract inventory](plans/BASALTWATER_CONTRACTS.md).

## Hosting and transition completion

GitHub source/download links still use `bluehexagons/infra_tools`. This PR does
not rename the hosted repository, publish to PyPI, register a domain or claim
trademark clearance. Remaining older documentation and installed workflow
skills using `infra-tools` work through the supported transition command.

The maintainer may retire that command no earlier than v3.0, after migrating
generated automation and agent guidance and publishing removal instructions.
Persistent identifiers are not scheduled for automatic renaming.

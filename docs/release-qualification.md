# Basaltwater v2.0 preview and release qualification

The current preview candidate is `v2.0.0-rc.6` (Python package `2.0.0rc6`).
The latest final release remains `v0.2.0`. This document is the qualification
record and release gate for the eventual stable `v2.0.0` release.

The `v2.0.0-rc.1` tag remains available for audit. Its Python 3.11 and 3.15
checks failed before release publication. The next candidate corrected those
test failures and restored package version reporting in Git-based installs.

The [coverage map](plans/testing-coverage.md) summarizes selected workflow
checks. Those results do not pass the exact-candidate matrix below. The
[roadmap](plans/roadmap.md) owns implementation priorities, including the
unfinished combined build/CI host; this document is not a progress log.

## Version and publication model

| Stage | Git tag | Python package version | Installer behavior |
| --- | --- | --- | --- |
| Development | `main` | Current preview version | Selected by `dev` |
| Preview candidate | `v2.0.0-rc.N` | `2.0.0rcN` | Opt in by selecting the exact tag; GitHub release is marked prerelease |
| Stable release | `v2.0.0` | `2.0.0` | Selected by `stable` after final qualification |

Increment `N` for each new candidate. Do not move or reuse a published tag.
The release workflow marks `-rc.` tags as prereleases, and the installer’s
`stable` channel matches only unqualified `vMAJOR.MINOR.PATCH` tags. Preview
users select an exact candidate, for example `basaltw channel
v2.0.0-rc.6`. A release candidate is for qualification and feedback; it does
not claim that the stable release matrix has passed.

Publishing a preview requires the tag’s Python package version to match the
candidate number and the release CI checks to pass. The package metadata check
compares the version against the exact tag on release builds, including the
final stable tag. After a failed candidate, fix the issue, increment the
candidate number, and record which previously passed cases need to be repeated.

Basaltwater replaces the repository and runtime namespace. The distribution is
`basaltwater`, the Python entry point is `basaltwater.py`, and the command is
`basaltw`. Internal callers, install/staging paths, state directories, services,
accounts, locks, skills, generated configuration, and deployment manifests use
the new namespace. The gateway command is `basaltwater-web`. New disk serials
and cache volume groups use Basaltwater names; existing storage retains its
durable identities as described in the current
[Proxmox storage guide](operations/proxmox.md).

The infra-tools cutover is complete for the managed fleet. Its migration engine
and automatic client/target migrations are retired. Any remaining old
installation or interrupted cutover requires the pinned
[intermediate version](migration-guide.md) before current setup.
There are no persistent old-name aliases, environment fallbacks, or historical
release support after cutover. The repository is hosted at
`bluehexagons/basaltwater`.

## Repository verification

- The default suite exercises the renamed callers, setup plans, service
  configuration, authentication, agent integrations and state handling.
- Retirement fixtures verify that old installations and incomplete journals
  stop setup without mutations, while current runtime activation preserves
  Syncthing traversal and respects unfinished systemd recovery.
- Installer tests cover new root/user installations, Debian/CachyOS selection,
  custom destinations, source activation failure and interruption recovery.
- The fresh wheel is built, installed and exercised outside the source tree.
  Packaging rejects retired entry modules and skill IDs.
- `make check` validates CLI docs, metadata, generated brand assets and tests.
  Visual identity and contrast checks remain described in [BRANDING.md](brand/README.md).

## Qualification protocol

Run live cases only on disposable systems and data. Mocked system calls,
fixtures, static panel specimens, and development-host observations do not
qualify a release candidate. Test the exact candidate commit and retain a
short, reviewed result for every required case. Never commit credentials,
private hostnames, personal data, or raw logs that contain them.

### Preview publication gate

- [x] `pyproject.toml` declares `2.0.0rc6` for `v2.0.0-rc.6`.
- [x] Release CI passes on Python 3.11 and 3.15, builds the wheel, and
  installs and smoke-tests the packaged launchers on Python 3.13.
- [x] The GitHub release is marked **pre-release** and contains the wheel built
  from the tagged commit.
- [x] An isolated disposable controller installs the exact candidate tag and
  reports its candidate version through `basaltw --version` and
  `basaltw channel`.
- [x] `stable` still selects the latest unqualified stable tag; preview tags
  remain opt-in.

Passing this gate permits a preview for feedback. It does not pass the live
qualification matrix or authorize the final stable release.

## Choose a topic

<a id="candidate-results"></a>
<a id="v200-rc6"></a>
- [Current preview candidate evidence](release-candidate-current.md) Qualification recorded for v2.0.0-rc.6.

<a id="v200-rc5"></a>
<a id="v200-rc4"></a>
<a id="v200-rc3"></a>
<a id="v200-rc2"></a>
- [Earlier preview qualification](release-candidate-history.md) Dated evidence for rc.5 through rc.2.

<a id="stable-qualification-matrix"></a>
<a id="result-record-and-prepublication-sign-off"></a>
<a id="post-publication-verification"></a>
- [Stable qualification matrix](release-stable-matrix.md) Exact-candidate gates, sign-off, and post-publication checks.

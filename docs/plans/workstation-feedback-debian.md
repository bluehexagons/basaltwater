# Debian continuation handoff

Status: Debian qualification completed on 2026-10-31; see the
[completion record](#debian-completion-record-2026-10-31). The original handoff
below records the requested scope and earlier CachyOS evidence.

Continue the workstation-feedback work on Debian. Validate the shared changes
under Debian's actual toolchain and desktop model, fix concrete findings, and
update the owning documentation/skills. The user requested incremental commits
and pushes to `main`; preserve unrelated work and follow the receiving session's
authority. This handoff does not authorize publishing, credential changes,
reconfiguring an unrelated machine or replacing its desktop.

## Source and scope

Start from current `main` containing this handoff and record its full HEAD and
dirty state. The initial implementation is in `30cfc75b`, `38d79a27`, and
`8a7f2d96`. The second-pass changes are in the commit containing this handoff;
do not test only the initial three commits. Read `AGENTS.md`, the
[contributor guide](../agents/contributing/README.md), and only relevant skill
references before editing.

| Area | Implementation / guidance |
| --- | --- |
| Opt-in workstation inventory | `lib/workstation_report.py`, `lib/agent_environment.py`; [manifest contract](../agents/agent-environment.md) |
| Explicit graphics context checks | `lib/agent_graphics.py`; [workflow routes](../workstations/workflows.md) |
| Managed skill delivery and comparison | `lib/agent_skill_bundles.py`, `lib/agent_skill_catalog.py`; [managed skills](../agents/agent-skills.md) |
| Native task process/timing evidence | `desktop/development.py`, `desktop/development_worker.py`; CachyOS-only execution |
| Project rendering/assertion harness | `lib/agent_visual_check.py`, `lib/agent_visuals.py`; [visual checks](../workstations/visual-comparisons.md) |
| Checkpoints, history and handoffs | [Handoff guidance and templates](../agents/handoffs.md) |

Repository policy owns project decisions; platform skills own machine/session
operation; active orchestration guidance owns delegation/provider settings.
Project-owned scripts and `basaltwater-agent.json` recipes own actual capture,
export and acceptance behavior. Keep private one-off Antistatic recipes out of
Basaltwater; recurring behavior belongs in the consuming repository.

## Observed on CachyOS

The initial pass passed 2,687 agent/desktop tests and 35 final focused tests.
Its native checks observed kernel `7.3.0-4-cachyos`, Intel PCI `8086:7d55` with
`i915`, display GLX renderer `Mesa Intel(R) Arc(tm) Graphics (MTL)`, Mesa
`26.2.5-arch3.1`, and an isolated Xvfb llvmpipe context. Blender `5.2.3 LTS`
completed the bundled 128×128 Cycles CPU fixture. Disposable native tasks
reported live unit metadata, stopped through their recorded unit, and retained
successful/stopped completion reports, logs and durations. Both fixtures ended.
No shared desktop application was adopted or stopped.

The second pass corrected these findings:

- Prefer Xvfb's atomic `-d` display allocation when its bounded help probe
  advertises it. Keep `-a` for older wrappers or unavailable help, with the choice
  exposed as `xvfb_allocation`.
- Reject oversized corrupt completion durations without integer-to-float overflow.
- Exclude generated `__pycache__`, `.pyc` and `.pyo` from skill source bundles.
  Compilation previously caused false incomplete-catalog findings and could
  distribute machine-specific bytecode. Existing digest/ownership and personal
  modification checks still govern reinstall and stale-file cleanup.
- Remove duplicated model policy from the CachyOS workspace skill; expose compact
  discovery and common handoff routes in workstation/desktop guidance.
- Clarify that inventory `usable_in_session`/`verified_by_project` remain null;
  separate context/recipe reports carry verification, with no automatic import.

Four existing local CachyOS bundles were refreshed using the guarded shared
installer, without a full setup/package/service refresh. The receiving Debian
machine's catalogs and already-loaded agent instructions are independent.
Second-pass validation passed `make check-fast SUITE="agent desktop"` in the
activated project venv: syntax, CLI/link/package checks and 2,690 tests. The live
software GLX check selected `auto-display` and observed llvmpipe; all nine
installed Basaltwater bundles compared as current afterward. Tests ran on the
scoped, uncommitted source changes subsequently committed with this handoff.
Private raw CachyOS evidence stays on that machine; local project memory does
not travel between separate clones or machines. This document carries the
portable findings, not a claim of Debian qualification.

## Debian work to perform

1. **Establish source and interpreter.** Inspect `git status -sb`, full HEAD,
   installation provenance and the provider account. Preserve existing checkouts
   and environments. Prepare the documented project venv if needed and install
   `.[dev,completion]` there. Activate it before Make: Make invokes `python3`,
   even when the outer capture helper was launched with a venv Python. Confirm
   `python3 -c 'import sys; print(sys.executable)'`. A prior CachyOS Make run used
   system Python and failed to import `aiohttp`; that was an interpreter issue.

2. **Validate discovery without a desktop first.** Run from the checkout:

   ```bash
   python3 basaltwater.py agent manifest . --capability workstation
   python3 basaltwater.py agent manifest . --capability workstation --json
   python3 basaltwater.py agent manifest . --capability development --json
   python3 basaltwater.py agent manifest . --capability skills --json
   ```

   Verify Debian version banners, `/usr/bin` alternatives, venv symlinks and
   unqueried user-local wrappers. Missing tools, an absent DRM sysfs or absent
   display environment should remain honest discovery observations. GUI apps
   and package-manager shims are presence-only. No downloads, renderer launches
   or project execution belong in inventory. A headless VM need not expose a
   physical PCI GPU; do not invent one from software-renderer counters.

3. **Exercise bundle delivery and comparison.** Run the focused selection below
   while making changes. Include a checkout with generated Python caches and
   confirm that bundle/catalog results do not depend on those caches. Verify
   legacy inventories, unchanged stale bytecode cleanup, personal-file retention,
   idempotence and executable modes using temporary catalog fixtures. Compare
   the Debian provider's actual installed skill catalog to the source. Use the
   normal managed delivery path for a requested installation; a full `refresh`
   can update packages and restart services and is not a diagnostic command.

   ```bash
   python3 run_tests.py tests.test_agent_manifest_output tests.test_agent_environment \
     tests.test_agent_probe tests.test_agent_graphics tests.test_agent_skill_bundles \
     tests.test_agent_skill_catalog tests.test_agent_skills tests.test_agent_skill_contracts \
     tests.test_cachyos_development_tasks
   ```

   The CachyOS task tests mock system calls; they must not launch native tasks
   on Debian. After final changes, run `make check-ci` once in the documented
   Debian environment for full static, brand, wheel and coverage validation.
   Keep browser qualification separate as the contributor guide requires.

4. **Perform explicit rendering checks where prerequisites exist.** Use
   `python3 basaltwater.py agent graphics doctor --context xvfb --require-software
   --json`; verify `-a` on older Debian wrappers and `-d` only when advertised.
   Help probing has a three-second bound; context execution has a separate
   fifteen-second bound. Test `--context display` only in the intended existing
   X session; on XRDP, that context does not qualify a physical display or the
   host GPU. If Blender is already selected/available, run `agent blender smoke
   --json` and inspect saved settings, scene and PNG. Record missing prerequisites
   rather than installing unrelated applications just to make a check pass.

5. **Qualify a project recipe.** Use a disposable project fixture or a reviewed
   existing recipe with `agent visuals check RECIPE --repository PATH --json`.
   Check success, deliberate failure and timeout evidence, log truncation,
   settings preservation, exit propagation and artifact discovery. Confirm
   Debian's `dpkg-query` graphics-package observations in `environment.json`.
   The harness must assert its own image content and actual backend/renderer;
   successful process exit or PNG headers alone are insufficient.

6. **Audit Debian desktop ownership separately.** Read the Debian desktop skill
   and [XRDP automation](../workstations/desktop-automation.md) before interaction.
   `desktop --native develop` is CachyOS-only. Debian's `desktop/session_runtime.py`
   owns the configured shared XRDP session and session-scoped launch handles;
   it does not acquire CachyOS's durable per-task user-service contract. Do not
   port cgroup stop logic blindly or log out the shared session for cleanup.
   Record any concrete Debian gaps in owned-job visibility, logs or duration
   and implement only the justified scope, preserving unrelated applications.

## Remaining limits and return deliverable

No Debian native execution, full CI, native Windows, Wayland portal interaction,
EGL/Vulkan rendering, audio/controller, HDR, performance, or complete 1440p/4K
project-image acceptance was established by the CachyOS context probes. These
are distinct checks, not prerequisites to call inventory successful. Native
Windows skill delivery uses separate platform code and needs its own later
cache-exclusion review; the shared Python fix does not establish that coverage.

Return the exact checkout/branch and tested/delivered commits, dirty state,
changes, commands with status/duration, private evidence paths, and remaining
unverified cases using the shared templates. Keep one current checkpoint and
separate history. Record all running/uncertain owned jobs and their status and
cleanup commands, or checked none. Commit and push completed increments when
authorized; retain failed evidence and do not replace native project commands
with a second Basaltwater-owned rendering framework.

## Debian completion record (2026-10-31)

Tested `/home/agent/repos/basaltwater`, branch `main`, base
`de7502b14b55828c1bc8315f215cb7b9c4b3beec`, initially clean. Native checks and
the full suite used that implementation; subsequent changes only document
qualification and the XRDP launch contract. The provider account was `agent`
(UID 1000), on Debian 13.7/Trixie, x86_64, kernel
`6.12.113+deb13-cloud-amd64`. The existing project venv supplied Python 3.13.5
and the development dependencies; activation selected `.venv/bin/python3`.
The user-local `basaltw` shell launcher invokes this checkout. No setup,
package/service refresh, credential changes or Proxmox guest operations were
needed.

Observed results:

- Workstation text/JSON, development, skills and installation manifests all
  exited zero (0.61–0.79 seconds each). Debian compiler/FFmpeg banners parsed;
  `/usr/bin/cc` alternatives and the venv Python symlink were probed. User-local
  Node/npm/pnpm, Go, Godot and provider tools remained presence-only, as did GUI
  applications. No physical DRM GPU was observed and no display was advertised
  in the invoking shell. Discovery retained null session/project verification.
- The specified nine-module focused selection passed 162 tests, exit zero,
  3.524 seconds. CachyOS task execution stayed mocked. Temporary real-filesystem
  fixtures also passed compiled-source cache exclusion, old inventories lacking
  a completion flag, unchanged stale bytecode cleanup, edited bytecode retention,
  executable modes, idempotence and catalog comparison.
- `make check-ci` passed once in the activated Debian venv, exit zero,
  206.134 seconds: 9,006 tests, six skips, 82% combined statement/branch coverage,
  syntax, CLI/link checks, branding, package metadata and wheel build/install.
  It started on the clean base; only the documentation changes below were made
  during its coverage phase. Final `make docs-check`, 16 skill/contract tests
  and `git diff --check` also passed after those edits.
- Xvfb GLX doctor passed, exit zero, 0.586 seconds, selecting `auto-servernum`
  (`-a`) for Debian's older wrapper. Renderer: llvmpipe LLVM 19.1.7, Mesa
  `25.0.7-2+deb13u1`, OpenGL 4.5. The configured XFCE XRDP session was initially
  stopped and was explicitly started with user authorization at 1600×900.
  Its separate display GLX check passed with the same software renderer; the
  recorded launch exited zero. Neither context qualifies physical GPU/display
  behavior.
- Blender 4.3.2 completed the bundled 128×128 Cycles CPU smoke, exit zero,
  4.279 seconds, retaining scene, settings and PNG. A disposable project recipe
  reopened that scene, asserted Cycles/CPU and resolution, rendered again and
  decoded all 16,384 pixels. Every pixel was opaque/nonblack and every quadrant
  had content variation; observed luminance spanned 0.0458–0.8327. This recipe
  passed in 3.609 seconds. It is a small content smoke, not scene fidelity or
  full-resolution acceptance.
- Recipe exit seven propagated as CLI exit one (0.638 seconds); a one-second
  deadline reported `timed-out` and exit one (1.583 seconds); 17 MiB output was
  drained with exactly 16 MiB retained and `log_truncated: true` (0.619 seconds);
  changing settings failed with exit one (0.644 seconds). PNG and assertion
  artifacts were discoverable in the evidence directory. Debian `dpkg-query`
  recorded installed multiarch-qualified Mesa/SDL/GL packages; missing optional
  packages correctly produced a partial observation. The initial disposable
  fixture lacked a Git HEAD and failed before recipe execution; its error was
  retained, then an empty baseline commit enabled the checks without replaying
  the earlier smoke/discovery phases.
- Installed skills initially reported 13 current and two different bundles.
  The guarded shared bundle installer refreshed `basaltwater-desktop` and
  `basaltwater-project-development`, including the clarified XRDP reference.
  All 15 installed bundles subsequently compared current; the Claude catalog
  was empty. This does not reload already-read provider instructions.

The desktop audit confirmed that launch tokens are session-local, limited to
128 receipts, and observe only immediate children. There is no per-launch stop,
duration or private output contract; output inherits session streams. The owning
[session reference](../../common/agent_skills/basaltwater-desktop/references/session.md)
now makes those limits and the bounded recipe alternative explicit. No durable
CachyOS task/cgroup behavior was ported to Debian. The XFCE session stays running;
no unrelated application was stopped.

Private native evidence: `/tmp/workstation-debian-jMHCtIhh/` (`commands.json`,
`continued.json`, manifests, graphics/display reports, Blender artifacts,
recipe reports/logs/settings/environment, cache fixture and delivered catalog).
Focused test logs: `/tmp/basaltwater-output-71sng68d/`; full CI logs:
`/tmp/basaltwater-output-ckve977q/`. A durable private copy, current checkpoint
and separate history are retained under
`/home/agent/.local/state/basaltwater/audits/workstation-feedback-debian-2026-10-31/`.
Raw logs and the
disposable project are not repository assets or a new shared rendering framework.

Remaining separate qualification: first-party browser tests, native Windows
bundle cache exclusion, Wayland portals, physical GPU/display, EGL/Vulkan,
audio/controller, HDR, latency/performance, full 1440p/4K image acceptance and
live XRDP application editing. No such coverage is inferred from the checks above.

## Follow-up audit (2026-10-31)

A second review found a settings-integrity defect missed by the first pass:
Python equality equates `1`, `1.0` and `true` (and `0` with `false`), including
nested arrays and objects. Both recipe checks and revision captures could
therefore report success after a setting's type changed. They now compare
normalized JSON with type/number distinctions preserved, while allowing
whitespace and key-order changes. Regression cases reproduced five failures
before the fix; all 34 visual check/capture tests passed afterward.

The configured live XRDP Geany smoke also passed in 7.038 seconds, exit zero:
Unicode edit/save, scoped dialog interaction, owned-window close and temporary
artifact cleanup. This closes the live XRDP editing gap for that fixture only;
it does not qualify every desktop application. The shared session remains
running and its Geany child exited successfully. Native Windows delivery already
contains cache exclusion and tests in its separate PowerShell implementation;
native Windows execution remains unverified here. The other platform/hardware
limits above remain separate qualification work.

Final follow-up validation: `make check-ci` passed on the scoped changes in the
activated Debian venv, exit zero, 208.685 seconds, 9,009 tests/six skips and 82%
combined coverage, with static/brand/wheel checks. The native type-mutation CLI
fixture correctly returned exit one in 1.194 seconds. Follow-up raw evidence is
copied under the same private audit directory (`followup-focused`,
`followup-desktop`, `followup-native`, `followup-ci`); the checkpoint records the
delivered commit. No outstanding owned application jobs remain.

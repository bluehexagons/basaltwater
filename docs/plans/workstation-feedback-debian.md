# Debian continuation handoff

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

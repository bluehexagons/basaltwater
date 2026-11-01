# Debian workstation qualification

Status: Debian qualification completed on 2026-10-31; see the
[completion record](#debian-completion-record-2026-10-31). The procedure below
records repeatable checks and platform boundaries.

Validate shared workstation changes under Debian's toolchain and desktop model.
Preserve unrelated work and follow the active session's authorization for edits,
commits and pushes. This document grants no authority over an installed machine.

## Source and scope

Start from current `main` and record its full HEAD and dirty state. The initial
implementation is in `30cfc75b`, `38d79a27`, and `8a7f2d96`; include subsequent
corrections when repeating qualification. Read `AGENTS.md`, the
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
export and acceptance behavior. Keep private one-off project recipes out of
Basaltwater; recurring behavior belongs in the consuming repository.

## Cross-platform findings

Earlier CachyOS validation exercised workstation discovery, an accelerated GLX
context, isolated llvmpipe, a small Blender CPU render and disposable owned tasks.
Those checks established their own scope, not Debian acceptance or full project
image/hardware qualification. Corrections included atomic Xvfb display allocation
when advertised, bounded completion durations, bytecode exclusion from skill
bundles, compact shared workflow guidance and explicit unknown readiness.
Private machine/package inventories and delivery records stay outside Git.

## Repeatable Debian checks

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

## Qualification boundaries

Software context checks do not establish physical GPU/display, native Wayland,
EGL/Vulkan, audio/controller, HDR, latency or full-resolution project-image
acceptance. Native Windows uses separate delivery/execution code. Record exact
operational paths and job state in private handoffs; publish only sanitized
capability results and limits.

## Debian completion record (2026-10-31)

Historical validation used Debian 13/Trixie x86_64 with the documented project
venv on implementation `de7502b14b55828c1bc8315f215cb7b9c4b3beec`.
Subsequent corrections and documentation updates were qualified separately.

- Scoped manifest and installation discovery succeeded, preserving null
  session/project verification and presence-only results for user-local tools.
- The nine-module selection passed 162 tests. Temporary filesystem fixtures
  verified cache exclusion, legacy inventories, unchanged stale-file cleanup,
  edited-file preservation, executable modes and idempotent catalog comparison.
- The full check passed 9,006 tests with six skips and 82% combined coverage,
  plus static, brand and wheel checks. Follow-up validation passed 9,009 tests
  with six skips and the same coverage.
- Isolated Xvfb and XRDP display GLX checks observed llvmpipe. Debian's older
  wrapper used `-a`; no hardware display coverage was inferred.
- A 128×128 Blender Cycles CPU fixture and a project recipe passed. Pixel
  assertions checked opacity, nonblack content and quadrant variation. This
  establishes a small content smoke, not scene fidelity or full-resolution QA.
- Recipe failures, deadlines, output caps and settings mutation propagated
  correctly. Installed-skill comparison and guarded bundle delivery succeeded;
  delivery did not establish which instructions a running session had loaded.
- A scoped Geany Unicode edit/save/dialog/close fixture passed in XRDP. It
  qualifies that editing fixture, not every desktop application.

The desktop audit confirmed that launch tokens are session-local, limited to
128 receipts, and observe only immediate children. They have no per-launch stop,
duration or private-output contract. The owning
[session reference](../../common/agent_skills/basaltwater-desktop/references/session.md)
documents the bounded recipe alternative; CachyOS task/cgroup behavior was not
ported to Debian.

## Follow-up audit (2026-10-31)

Python equality allowed settings values to change between `1`, `1.0` and
`true` (or `0` and `false`), including nested values. Recipe checks and
revision captures now compare normalized JSON preserving type/number
distinctions while accepting whitespace/key-order changes. Five regressions
failed before the fix; all 34 visual check/capture tests passed afterward.

Raw logs, settings, catalog state, exact machine details and current checkpoints
remain in private evidence. First-party browser qualification, native Windows
execution, native Wayland, physical display/input/audio, HDR, performance and
full-resolution project image checks remain distinct work; these historical
checks do not establish them.

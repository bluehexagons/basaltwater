# Workstation workflow routes

Start in the actual project checkout. Read its `AGENTS.md` and canonical commands;
Basaltwater supplies shared operation and evidence tools. Project code owns build,
export, capture and acceptance logic. Use the smallest relevant discovery scope:

```bash
basaltw agent manifest . --capability workstation
basaltw agent manifest . --capability development --json
basaltw agent manifest . --application blender --json
```

| Workflow | Entry point | Completion evidence |
| --- | --- | --- |
| Native development | Run the project's documented build/test command. On CachyOS, use `desktop --native develop doctor`, then `develop exec`, `node`, or Godot `editor`/`run` with `--project PATH` | Task ID, status, private log, tested commit and project assertions; [native development](cachyos-development.md) |
| Blender assets | Use the discovered executable, project scripts and export settings. `agent blender smoke --json` tests an isolated Cycles CPU fixture | Editable task copy, reopened file, relocated export round trip and consuming-project check; [Blender reference](../../common/agent_skills/basaltwater-cachyos-desktop/references/blender.md) and [desktop development](desktop-development.md) |
| Visual verification | Declare a reviewed harness under `recipes` in `basaltwater-agent.json`; run `agent visuals check RECIPE --repository PATH --json` | `check.json`, settings, logs, actual backend/renderer, complete captures and project assertions; [visual checks](visual-comparisons.md) |
| Handoff | Update one current checkpoint and select a [handoff template](../agents/handoffs.md) | Checkout, tested revision/dirty state, observed results, unfinished work, next action, evidence and owned jobs |

All commands above use the `basaltw` prefix. Native `develop` supervision is
CachyOS-specific. Debian uses [desktop automation](desktop-automation.md) and its
project commands; native Windows uses [Windows desktop operation](windows-desktop.md).
Linux CLI success does not establish native Windows support. Keep platform
acceptance separate using the hardware template.

For a backend prerequisite check, explicitly run `basaltw agent graphics doctor
--context display --json` or `--context xvfb --require-software --json`.
GLX context creation does not verify a project frame. EGL/Vulkan/offscreen checks
belong in the project's harness, recording the actual renderer. See
[rendering evidence](../../common/agent_skills/basaltwater-cachyos-desktop/references/rendering.md).

The software GLX doctor checks `xvfb-run --help` once and uses atomic `-d`
allocation when advertised, falling back to `-a` for older wrappers or an
unavailable help probe. `xvfb_allocation` records that selection. This creates
only the diagnostic's isolated X server, not a replacement desktop session.

Compare installed guidance with the invoking source using
`basaltw agent manifest . --capability skills --json`. Source checkout updates
do not update installed skills or a running agent's already-loaded instructions.
Use the platform's managed skill installation during setup/refresh, preserving
personal changes; recheck the catalog afterward. Keep full workstation refresh
separate from diagnostics because it can update packages and restart services.

Keep reusable capture recipes in the consuming repository. Private settings may
select task inputs through `--settings PATH`; do not make a private one-off script
the only documented route. Native Wayland capture/input follows
[desktop control](cachyos-desktop.md), including the existing consent and control
state. Background scripts do not require screen access.

## Results and owned jobs

Return a compact status, exit code, elapsed duration and artifact paths. Use
`check.json`/`check.log` for recipes, Blender's `report.json`/`blender.log`, and
`develop status TASK_ID --json` for native task state. Full available output stays
in private logs; quota-limited tools must report truncation. A successful status
query is distinct from a successful project assertion. Inspect the report's
verification scope before claiming GPU, UI or hardware acceptance.

For other verbose commands, use the shared
[capture helper](../../common/agent_skills/basaltwater-project-development/scripts/capture_output.py):

```bash
python3 /path/to/basaltwater-project-development/scripts/capture_output.py \
  --timeout 300 -- PROJECT_COMMAND ARGUMENTS
```

It retains stdout/stderr and reports paths, counts, exit code and duration. It is
a bounded foreground runner; native `develop` provides durable background task
ownership on CachyOS. Preserve each task/unit/launch handle in the checkpoint and
inspect that handle to completion. `develop stop TASK_ID --dry-run --json` previews
guarded cleanup of that task's unit; save task-owned documents before stopping.
Portal captures are bounded children of the approved native helper, not independent
services to find and kill by application name. Preserve the user's applications
and desktop; see the owning platform's recovery guidance for interrupted capture.

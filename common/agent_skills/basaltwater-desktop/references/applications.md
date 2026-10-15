# Shared native desktop

The setup account owns one XRDP/Xorg desktop on this VM. The human's RDP client
and your commands use the same applications and display. The desktop is created
on demand and persists through RDP or agent disconnection until desktop logout,
failure, or VM shutdown. Only one human RDP connection is supported at a time.

Read `status` for the configured environment (XFCE by default; i3, Cinnamon or
LXQt may be selected), current state and geometry. Use `basaltw agent manifest`
inside the project for installed applications, workflows and instruction links;
use `command -v` or package queries for further discovery. This skill does not
imply that a particular editor or browser exists. Standard emulated graphics use software
rendering; heavy 3D work may be slow and does not establish GPU compatibility.

Debian setup selects Blender with `--blender` on workstation or standard server
profiles. It uses the configured Debian release's APT package even with
`--flatpak`; keep updates under normal host package maintenance. Check
`blender --version` before using version-specific APIs or add-ons. See the
[desktop software support table](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/WORKSTATIONS.md#desktop-software-support-and-flags)
for supported applications and setup flags.

Agentic Blender desktop editing is a primary workflow: model touch-ups,
material/UV adjustments, scene work and asset preparation, usually completed
fully autonomously. Read
[Blender workflows](blender.md) for native edit/save/reopen checks,
software graphics, embedded Python and isolated test preferences. Launch the
working scene with `basaltw desktop exec -- blender /absolute/project/scene.blend`
using the project's intended preferences, add-ons and embedded-script policy.
Combine viewport interaction with `bpy` for repeatable changes, verify saved or
exported results, and report the completed artifacts. Human handoff is optional
when requested; autonomous Blender tasks do not require human review.

For game and website media touch-ups, read [media workflows](media.md).
Debian selects native Inkscape, GIMP, Krita, Audacity and Shotcut with
`--inkscape`, `--gimp`, `--krita`, `--audacity` and `--shotcut`; these use the
configured Debian release even with `--flatpak`. Keep the tool set small and
add `--av-tools` for repeatable conversion and inspection. Save editable sources,
export delivery assets, reopen them and check the consuming project autonomously.

For a deliberate background rendering check, `basaltw agent blender smoke --json`
retains private scene/PNG/settings/log evidence from a small Cycles CPU render.
Desktop editing readiness requires its own edit/save/reopen check.
Record scene, camera, frame, resolution, engine
and device alongside PNG captures. Compare results with `basaltw agent visuals
compare`; use a project harness with `visuals capture` for isolated revisions.
Read the [desktop development guide](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/DESKTOP_DEVELOPMENT.md)
for application guidance and the distinction between discovery and readiness.

Use it for native editors, office applications, graphical tools, and behavior
that specifically depends on the desktop environment. Use shell commands for
files, builds, and service operations. For browser work, follow the active
session's browser policy and installed browser-testing skill. Choose the
available option that covers the task: T3 for live collaboration, Playwright
for repeatable DOM/network checks, or the desktop browser for native integration
and visual interaction. Canvas/WebGL alone does not require a desktop.

After confirmed T3 disconnection or unavailable automation tools permit fallback,
continue the authorized browser test here without asking the user to reopen T3.
Check desktop status/doctor and installed applications, then use the managed
launch and scoped inspection/input commands below. A stopped desktop can be
started within the test task; do not restart a running session or resume human
pause automatically. Select the project page without taking over unrelated
browser windows, verify the resulting interaction, and report which assertions
were exercised. Pixel-only evidence does not prove DOM or network behavior.

The desktop browser runs on the VM. It has a separate profile from Playwright
and the client-side T3 browser. Do not copy cookies or assume shared logins.

For unattended readiness checks, use `desktop status` and `desktop doctor` only
when accessible. Starting applications, the live smoke check, screenshots, and
input affect or capture the human's shared session and require that task scope;
a readiness prompt does not authorize them. Honor human pause and report blocked
checks without restarting the desktop or acquiring control through another tool.

# Development and testing with desktop applications

Start with `basaltw agent manifest --json`. Detected applications include their
active executable path, development workflows and instructions. Availability
means the executable was found on PATH; it does not verify the application's
version, add-ons, desktop session, audio devices or graphics backend.

Use the local desktop skill listed by the manifest before native UI work.
Managed Debian VMs can provide a shared XRDP desktop and `basaltw desktop`
tools. CachyOS workstations use their existing native desktop; do not assume
XRDP, X11, screenshot automation or the VM gateway exists there. If automation
is unavailable, prepare the project and ask for human validation in the native
application. Use the collaborative browser policy for web applications.

## Blender

Select `--blender` in a Debian workstation or standard server setup, including
`agent_vm` for headless use. It installs Debian's APT `blender` package for the
configured release and follows normal host package updates. CachyOS's
`agent_cachyos` profile uses its native package with the same flag. See the
[desktop software support table](WORKSTATIONS.md#desktop-software-support-and-flags)
for other applications, flags and desktop access choices. Check
`blender --version` before using version-specific scene APIs or add-ons.

Blender can render a project without opening its UI:

```bash
mkdir -p /absolute/ignored/artifact-directory
blender --background /absolute/project/scene.blend \
  --render-output /absolute/ignored/artifact-directory/render- \
  --render-format PNG --render-frame 1
```

Blender evaluates command-line arguments in order. Load the blend file first,
then override output settings, and render last. A project's scene automation
can use `--python /path/to/capture.py` with `--python-exit-code 1` before the
script argument so Python errors fail the command. Use a trusted project script
to set the scene, camera, frame, resolution, seed and render engine explicitly.
See Blender's official [command-line rendering](https://docs.blender.org/manual/en/latest/advanced/command_line/render.html)
and [argument reference](https://docs.blender.org/manual/en/latest/advanced/command_line/arguments.html).

Keep generated files in a declared, ignored artifact directory. Record the
Blender version, scene/camera, frame, dimensions, engine, CPU/GPU device and
render settings alongside each capture. A background render is evidence about
that render path; test interactive editing in the native desktop separately.
Do not select a GPU backend merely because Blender is installed. Use the
project's established backend and inspect a deliberate smoke render's logs.

Compare PNG results with `basaltw agent visuals compare`. For comparisons
across revisions, provide a project capture harness to
`basaltw agent visuals capture`; it runs the same settings in two managed
worktrees. The harness must apply those settings and write one PNG to its
requested output path. See [visual comparisons](VISUAL_COMPARISONS.md).

## Other applications

Krita and GIMP support raster editing; Inkscape supports vector editing;
FreeCAD and KiCad support CAD work; Kdenlive and Shotcut support video editing;
Audacity, Ardour and LMMS support audio work; Scribus supports page layout;
OBS supports recording. The manifest exposes these workflows only when the
corresponding executable is present.

Prefer project-owned sources and repeatable export recipes. Use a native UI
check when the change concerns editing behavior, layout, timeline interaction
or device selection. Keep capture settings and exported evidence with the
test result so another agent can repeat the check.

## Further capability-awareness improvements

- A short session-start summary of available workflows, workspace conventions,
  deployment mappings and instruction links, refreshed after provisioning or
  switching the active toolchain.
- Explicit states for each workflow: discovered, checked, unavailable, or
  unknown, with the last check's timestamp, context and remediation. Avoid
  treating an installed binary as a successful test.
- Project declarations of required capabilities and named build/test/capture
  recipes. Discovery can identify unmet requirements; executing recipes should
  remain a deliberate action using trusted project instructions.
- An `explain CAPABILITY` entry point with prerequisites, examples, evidence
  locations and relevant local skills, so agents need not load every guide.
- Session-aware routing advice that identifies whether a browser, terminal or
  desktop runs on the client or VM and supplies the usable preview address.
- Capture provenance that records application versions, renderer/device,
  inputs and revisions, with links to logs and visual comparisons.
- Coverage for applications launched through Flatpak or desktop entries, with
  their actual launch commands and sandbox limitations; PATH discovery alone
  does not inventory those installations.

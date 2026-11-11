# Diagnose GLX and EGL contexts

Choose the backend and context explicitly on a Linux workstation:

```bash
basaltw agent graphics doctor --context xvfb --require-software --json
basaltw agent graphics doctor --context display --expect-renderer Radeon --json
basaltw agent graphics doctor --backend egl --context offscreen --json
LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe \
  basaltw agent graphics doctor --backend egl --context surfaceless \
  --require-software --expect-renderer llvmpipe --json
```

GLX remains the default and accepts `xvfb` or `display`. EGL accepts `offscreen`
or `surfaceless`, creates a 1x1 OpenGL pbuffer, makes its context current and
queries `GL_RENDERER` and `GL_VERSION`. It also reports the EGL vendor/version
and client API. It needs the existing `libEGL.so.1` and a working OpenGL driver,
without installing tools or compiling a probe. OpenGL ES and Vulkan are outside
this probe's scope.

`offscreen` uses `eglGetDisplay(EGL_DEFAULT_DISPLAY)`, including the driver's
interpretation of inherited `EGL_PLATFORM`; it may need an existing display.
`surfaceless` explicitly selects `EGL_PLATFORM_SURFACELESS_MESA` using the
[Mesa platform extension](https://registry.khronos.org/EGL/extensions/MESA/EGL_MESA_platform_surfaceless.txt),
without changing environment selectors or falling back to another platform.
Unsupported extensions, unavailable libraries, failed initialization, config,
surface, context and current-context steps remain distinct failures. The
isolated helper has a 15-second deadline and a 16 KiB output bound; a driver
crash or hang cannot run indefinitely in the doctor process.

`--expect-renderer` requires a case-insensitive substring in the actual renderer.
`--require-software` requires a recognized software renderer such as llvmpipe.
These assertions return nonzero for a mismatch or an unverified context.
Acceleration and unknown software selection remain nullable; an EGL vendor
string, requested software flag or successful process alone cannot satisfy the
software assertion. Hardware profiling, rendered project content, physical
display, HDR and input latency remain unqualified.

`inherited_selectors` reports bounded allowlisted display/SDL/Mesa, EGL/GLX
vendor, PRIME, Vulkan and device-visibility settings. Values are limited to
1024 characters and control-containing values are marked invalid. The same
allowlist is retained by project checks in `environment.json`. These local
reports can contain private device selectors or library paths; inspect them
before sharing. Neither diagnostic rewrites selectors or publishes evidence.

A software GLX context does not establish software EGL rendering. An SDL
offscreen harness can also choose differently from this standalone EGL probe.
Record and assert the backend, actual renderer and settings inside every
project rendering process and worker. Keep acceptance thresholds in the project.
See [project rendering checks](visual-comparisons.md) and
[shared measurement reservations](performance-sessions.md).

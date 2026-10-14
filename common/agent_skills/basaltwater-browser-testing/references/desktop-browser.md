# Use a configured desktop browser

Discover installed applications with `basaltw agent manifest REPOSITORY --json`
and read the installed desktop skill before launch, capture, or input. On an
XRDP VM, check `basaltw desktop status` and `basaltw desktop doctor`; use
`desktop start` and `desktop exec -- BROWSER URL` within the browser-testing
task. These commands reuse the managed session and supply its environment.
Use PID/window-scoped inspection and captures, verify the resulting page, and
honor human pause. Never guess a display, copy authority cookies, or take over
an unrelated browser window. Native KDE uses its separate installed desktop
skill and existing portal grant instead of XRDP commands.

Use accessible controls when available and fresh screenshots for visual input.
Pixel-only checks do not establish DOM/network assertions; report the checks
actually performed. Preserve existing desktop applications and leave the
session running. Do not change browser installation or access policy merely
because a collaborative client disconnected.

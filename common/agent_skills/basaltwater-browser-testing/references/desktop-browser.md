# Use a configured desktop browser

Discover installed browsers with `basaltw agent manifest REPOSITORY --capability browser --json`
and read the installed desktop skill before launch, capture, or input. On an
XRDP VM, check `basaltw desktop status` and `basaltw desktop doctor`; use
`desktop start` when needed within the authorized task. For a fresh Firefox
profile use `basaltw desktop develop browser --browser firefox-esr --project
REPOSITORY --url URL --json`. Use `--browser chromium` or `--browser librewolf`
to test either native alternative. The task reports its private profile and
download directory. Use `desktop develop status TASK_ID` to get the browser launch PID.
Fresh desktop profiles can inherit system-wide content blockers. If a request
is missing, record the enabled extension and compare with the project's
Playwright context when the task permits.
Use a fresh window receipt for the file picker when it opens separately.
Collect required downloads, close the owned browser, and run
`desktop develop cleanup-browser TASK_ID` after exit. Add `--include-downloads`
only when those files can be discarded. For an existing configured browser
profile, use `desktop exec -- BROWSER URL` within the browser-testing task.
Use PID/window-scoped inspection and captures, verify the resulting page, and
honor human pause. Never guess a display, copy authority cookies, or take over
an unrelated browser window. Native KDE uses its separate installed desktop
skill and existing portal grant instead of XRDP commands.

Use accessible controls when available and fresh screenshots for visual input.
Pixel-only checks do not establish DOM/network assertions; report the checks
actually performed. Preserve existing desktop applications and leave the
session running. Do not change browser installation or access policy merely
because a collaborative client disconnected.

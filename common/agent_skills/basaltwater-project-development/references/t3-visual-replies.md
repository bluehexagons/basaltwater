# T3 visual replies

[Topic index](t3-workflows.md)

## Visual replies

For a chart, comparison, diagram, or interactive explanation that communicates
the result better, build self-contained HTML with inline styles and scripts.
Call `html_preview`, inspect the screenshot and console output, fix relevant
errors, then publish with `html_render` using the measured content height and
the live tool's theme/layout rules. Check a narrow viewport when relevant.
Follow the session's instruction for the accompanying final reply.

Stable 0.1.8 also accepts `path` with an absolute `.html` file path in both
tools. Follow the live schema and retain the file when an export is useful;
a file on disk alone does not publish an inline result.

Local image paths are embedded when published; remote URLs remain remote
dependencies. Keep needed images local and exclude private material from the
page. The sandboxed page is a thread artifact: it does not deploy a site,
provide a backend, or verify a real application's behavior. For requested
product changes, implement and test the actual project. For exportable or
publication-quality plots, retain the normal standalone artifact too.

`html_preview` uses T3's own headless Chrome, shared with its server browser.
An initial browser download is preparation, not an unavailable viewing client;
follow the returned retry guidance. Missing host libraries, fonts, or sandbox
support need the browser skill's host-setup route. Do not install another
automation stack or disable the sandbox to repair an HTML preview. If preview
is unavailable, report that validation gap; publishing itself does not require
Chrome. Independent terminal tasks do not inherit these tools or rendered pages.

Contracts: [visual replies](https://github.com/pingdotgg/t3code/blob/v0.1.8/docs/user/html-renders.md)
and [HTML tools](https://github.com/pingdotgg/t3code/blob/v0.1.8/apps/server/src/mcp/toolkits/html/tools.ts).

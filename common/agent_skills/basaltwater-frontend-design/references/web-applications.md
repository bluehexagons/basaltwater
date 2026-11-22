# Web applications

## Design around a complete task

- Start with a realistic task such as finding a record, editing it, and
  confirming the saved result. Give the workspace most of the space. Keep
  navigation consistent and place actions next to the content they affect.
  Use tables for comparing records, lists for scanning, and detail views when
  an item needs its own context.
- Use representative data and handle states the flow can reach, such as a
  first visit, zero search results, loading, validation errors, and failed
  requests. Explain what happened and how to recover. Preserve useful input
  and selection through recoverable failures. For asynchronous actions, show
  progress and the eventual outcome and prevent accidental duplicate submits.
- Preserve routes and browser Back behavior. Where a filter, tab, or selection
  needs to survive sharing or reload, use the project's URL-state convention.
  Choose responsive layouts that keep data and the actions for it reachable
  at supported widths.

## Exercise the workflow

- Use native links, buttons, form controls, and tables where they fit. Give
  controls accessible names and associated errors. For custom widgets, use
  the applicable interaction pattern, including focus order, keyboard
  behavior, and focus return from overlays. Check that sticky controls leave
  the focused element visible, status changes can be announced, and touch
  actions have adequate target size and spacing.
- Try the main task with representative data and a relevant recovery path.
  Check dense data or long strings at constrained widths, keyboard traversal,
  and touch or zoom when relevant. Treat rendered inspection and functional
  assertions as complementary checks. Use the project's test setup and
  assert outcomes through user-facing controls and content.

See [Vercel's interface guidelines](https://vercel.com/design/guidelines),
[WCAG 2.2](https://www.w3.org/TR/WCAG22/),
[ARIA Authoring Practices](https://www.w3.org/WAI/ARIA/apg/practices/read-me-first/),
and [Playwright's testing guidance](https://playwright.dev/docs/best-practices).

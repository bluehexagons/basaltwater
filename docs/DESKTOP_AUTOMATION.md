# Native desktop automation

The shared desktop supports AT-SPI inspection and actions alongside screenshots,
window management and keyboard/pointer input. Run commands as the configured
desktop owner. Browser workflows remain covered by [browser automation](BROWSER_AUTOMATION.md).

Desktop setup installs `python3-gi`, `gir1.2-atspi-2.0` and `at-spi2-core` with
their package dependencies. They run on the shared session's accessibility bus;
no office suite or application adapter is required. Existing installations need
updated runtime code and these packages; normal desktop setup applies them after
its existing logout checks. Save work before rerunning setup.

`basaltw desktop doctor` checks that the system Python can import AT-SPI.
This dependency probe neither starts the desktop nor reads application content;
application accessibility coverage still requires an inspection.

## Inspect and act

```bash
basaltw desktop start
basaltw desktop windows
basaltw desktop inspect --pid PID
basaltw desktop inspect --pid PID --name Save --role button
basaltw desktop element invoke --generation GENERATION --ref REF --action-name click
```

Copy the PID from the current window inventory. Inspection covers that
application's showing controls, including its dialogs, rather than assuming one
PID corresponds to one window. Names and roles are exact matches. Inspect the
returned tree paths, states and text to distinguish duplicate controls.

Each row includes a reference, name, role, states and advertised action names.
Name and text previews stop at 256 characters and report `name_truncated` and
`text_truncated`. A shortened name does not match an exact name selector; select
by role and inspect the returned reference instead. Identity checks use the full
name. Marked password controls omit their names, text and descendants. Other
document content may be private; inspect output before sharing it.

Scans skip hidden subtrees and stop at 512 visited nodes, depth 20, 128 returned
rows, or a response size/time limit. `truncated` reports incomplete results.
Exact name/role filters reduce returned rows but do not remove the traversal
limit. A showing control may still be occluded. Use screenshots for visual
judgment and when an application exposes insufficient accessibility information.

## Scope to a dialog or subtree

Inspect the application to find a dialog, panel, or other container, then use its
reference as `--root`. The root and its showing descendants become the entire
search scope, so unrelated menus and windows do not consume the scan budget:

```bash
basaltw desktop inspect --pid PID --role dialog
basaltw desktop inspect --pid PID --root ROOT_REF --generation GENERATION
basaltw desktop wait-element --pid PID --root ROOT_REF --name Save \
  --role button --state enabled --generation GENERATION
```

Scoped inspection requires an explicit generation from the original observation.
References are opaque: copy them intact rather than constructing or parsing them.
The helper walks the recorded ancestry and rechecks its identity on every call.
A changed, hidden, or missing root is an error, including for an absence wait;
it never falls back to the full application. Scope reads can reuse a root while
its identity remains valid, including across actions; reobserve if it changes.
An absence result refers only to showing controls inside that root.

The usual node, time and output limits still apply within the subtree; the depth
limit remains relative to the application root.
If needed, inspect a smaller container next. Descendant references work with the
normal `element` commands, which now resolve targets directly along their
recorded ancestry instead of rescanning unrelated UI.

## Act on observed controls

References are retained in a bounded session-local inventory for up to 60 seconds.
Desktop mutations and human pause clear them. Actions recheck the application's
D-Bus/object identity, tree position, name, role and enabled/showing state. These
checks reduce stale targeting; application changes can still race with an action.
Actions refresh the target's cached identity/state immediately before requesting
the mutation. All mutations require the existing session generation and exclusive
control lease.

`element focus` requests keyboard focus. `element set-text --text TEXT` replaces
the entire editable control with up to 4096 characters, including Unicode and
newlines. `element invoke --action-name NAME` invokes an advertised action.
Responses report application acceptance, not completed work. Reinspect after
each action. A timed-out action may have reached the application; do not retry it
without observing the result.

## Wait and verify

```bash
basaltw desktop wait-element --pid PID --name Save --role button \
  --state enabled --generation GENERATION --timeout 15
basaltw desktop wait-element --pid PID --role text \
  --state focused --text 'expected text' --generation GENERATION
```

Waits combine exact name/role, state (`present`, `absent`, `enabled`, `showing`,
or `focused`) and optional complete text equality. They require an unambiguous
match and a complete scan. Absence means no matching showing control; it cannot
prove that a document saved or an export finished. Truncated text cannot satisfy
text equality. Timeout errors include the last observation.

Polling releases control between requests, including during human pause. Each
accessibility helper has an eight-second limit, so the final observation can
extend a wait beyond its requested timeout by that amount. A failed helper never
restarts the application or desktop.

Verify task results with ordinary file tools: compare saved text, inspect an
export, or reopen the output. There is no separate artifact-verification service.

## Small Geany check

Run the repeatable live check on a running desktop:

```bash
basaltw desktop smoke
```

It launches a new Geany instance with a private temporary profile and an English
UTF-8 locale, edits Unicode text, verifies the saved file's exact bytes, and
exercises a scoped Find dialog wait. Success closes its own window, verifies
that the instance has no remaining windows, and removes its temporary files.
The shared desktop stays running. No packages are installed by this command.

JSON output lists completed stages. Failure returns a nonzero exit code with the
failed stage, temporary directory and launch/PID/window identities when known.
It retains the test instance and files for inspection; it does not retry actions,
force-close applications, or resume paused control. Inspect and close that test
instance before rerunning. Waits are bounded individually, not by a single overall
deadline. This is an explicit live check, separate from `doctor` and unit tests.

To reproduce the edit/save portion manually:

Geany ships in the `agent_code_vm` profile. This check uses its normal editor and
Save button. On other profiles, first check `command -v geany`.

1. Create a private temporary directory and a UTF-8 text file containing a short,
   unique marker. Keep a separate expected file containing the desired result.
2. Launch `basaltw desktop exec --wait-window Geany -- geany --new-instance
   --no-session --config /absolute/temporary/profile /absolute/temporary/check.txt`.
   The private profile avoids changing the user's editor preferences or session.
3. Read the new window's PID and generation. Use `desktop inspect --pid PID
   --role text` and identify the editor by the marker. Its search field is also
   a text control, so do not select an arbitrary first match.
4. Use `element set-text` with the editor reference and desired contents. Include
   accented or non-Latin characters to exercise Unicode handling.
5. Use `wait-element --pid PID --name Save --role button --state enabled
   --generation GENERATION`, then invoke the returned button's `click` action.
6. Compare the saved file to the expected file with `cmp`. Optionally capture the
   application window for visual inspection. Close only this test instance using
   its current window identity, and retain or remove the temporary files as needed.

This verifies real application editing and saving without an office dependency.
The automated unit tests mock AT-SPI, subprocesses and session operations; they
do not launch applications or modify the test host.

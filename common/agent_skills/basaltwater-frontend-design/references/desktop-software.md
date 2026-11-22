# Desktop software

## Fit the platform and the workspace

- Inspect the app's window model, toolbars, menu structure, and active document
  or selection. Keep frequent commands reachable and place context-specific
  actions near the selection. Preserve platform conventions for menus,
  file dialogs, window controls, and shortcuts.
- Use toolkit controls and accessibility APIs. Keep menu labels, shortcuts,
  and focus behavior consistent with platform and project conventions.
  Prefer in-place edits or secondary panes when users need the main workspace
  visible. Use a dialog when a decision needs to interrupt the task, and
  restore focus to the relevant control when it closes.
- Plan for resizing, high DPI, and increased system text size. Choose which
  panes stretch, collapse, or scroll and keep the current work and required
  actions reachable. Apply the project's conventions for system themes and
  high contrast. Keep unsaved-change recovery and long-running operation
  feedback visible in the flow that owns them.

## Verify the packaged experience

- Exercise the main workflow in an actual app window at normal and
  constrained sizes. Check tab order, shortcuts, dialog focus, and the active
  document after switching windows. Verify display scaling or theme changes
  when they are relevant to the affected controls.
- Test platform-owned behavior on the relevant operating system. For a
  web-based desktop shell, exercise the packaged app when the change touches
  window management, native dialogs, menus, or OS integration. Record the
  platform and app mode exercised.

For an editor with multiple documents, edit one document, switch windows,
then invoke a native save dialog with the keyboard. Cancel and verify that
the draft and active document remain intact and focus returns to the editor.

See [Apple's macOS design guidance](https://developer.apple.com/design/human-interface-guidelines/designing-for-macos),
[Microsoft's Windows accessibility checklist](https://learn.microsoft.com/en-us/windows/apps/design/accessibility/accessibility-checklist),
and [GNOME's keyboard guidelines](https://developer.gnome.org/hig/guidelines/keyboard.html).

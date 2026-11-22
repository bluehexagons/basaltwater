# Mobile applications

## Design for the available space and platform

- Use the app's navigation and platform conventions for Back, tabs, sheets,
  permission prompts, and system pickers. Keep the primary action reachable
  with the software keyboard open. Account for safe areas and system insets
  around bars, cutouts, and system gestures.
- Adapt to available window space. For supported tablet, split-screen, or
  foldable layouts, decide when to show list and detail together and preserve
  the current selection as the layout switches. Let text reflow with system
  font scaling and use touch targets sized and spaced for the platform.
- Use standard controls where they fit, with meaningful accessible names
  and state. Provide a visible, accessible way to invoke important actions
  offered by custom gestures. Preserve drafts and give users a way to recover
  when an affected workflow loses connectivity or is interrupted.

## Exercise the app

- Use the project's native device, simulator, or emulator workflow. Check a
  compact layout and a different supported window size when layout changes.
  Exercise relevant cases with large system text, the onscreen keyboard,
  and VoiceOver or TalkBack when available.
- For changes involving permissions, navigation, or lifecycle, exercise the
  affected state transition, such as declining a permission, going Back, or
  resuming with an unfinished form. Identify which checks ran on hardware,
  in a simulator or emulator, or in a component preview.

For a form layout change, enter a draft on a compact screen with the keyboard
open, change to another supported window configuration, and finish the form.
Verify that the draft survives and the primary action stays reachable.

See [Apple's iOS design guidance](https://developer.apple.com/design/human-interface-guidelines/designing-for-ios)
and [accessibility guidance](https://developer.apple.com/design/human-interface-guidelines/accessibility),
and Android's guides to [adaptive layouts](https://developer.android.com/develop/ui/compose/layouts/adaptive/get-started-with-adaptive-apps),
[system insets](https://developer.android.com/develop/ui/compose/system/insets),
and [accessibility testing](https://developer.android.com/guide/topics/ui/accessibility/testing).

# Godot games

## Design for the moment of play

- Identify what the player needs to notice, decide, and confirm during the
  relevant moment. Keep time-sensitive HUD cues visible over a range of
  gameplay backgrounds. Use shape, icons, or wording alongside color and
  provide visual cues for essential information also delivered by sound.
- Use the game's art direction, typography, input conventions, and UI scale.
  Share styling through the project's `Theme` resources. Anchor screen-level
  `Control` nodes to their parent and let `Container` nodes lay out their
  children using size flags and minimum sizes. For game-wide scale changes,
  inspect the project's base resolution, stretch mode, aspect behavior, and
  pixel-art constraints. Consult the Godot docs for the project's version.
- Define which control has focus on entry and where focus returns on Back.
  Set explicit neighbors where automatic navigation is ambiguous. Use the
  project's input actions and keep gameplay input from firing while a menu
  owns it. Bind prompts to the active device and remapped controls where the
  game supports them.

## Play through the change

- Enter the UI from the normal game flow, perform the action, then close the
  UI and resume play. Exercise relevant cancel, disabled, and confirmation
  states. Confirm keyboard and controller users can reach the same required
  actions when both inputs are supported. Where applicable, check device
  switching, held inputs, touch targets, and loss of window focus.
- Inspect the intended game viewport and a constrained supported aspect ratio
  or scale. Where relevant, check safe areas for display cutouts and system
  overlays. Check HUD readability during motion and over actual scenes.
  Exercise the affected export if it changes input, layout, or rendering.
  Reuse the project's capture and smoke-test recipes when available.

For a pause menu change, enter from active play using the controller, open
settings, and cancel or confirm a change. Check focus on return and confirm
that menu input did not trigger gameplay and play resumes when the menu closes.
Use the project’s fixed scene and frame sequence to compare HUD readability
and transitions when relevant.

See Godot's guides to [containers](https://docs.godotengine.org/en/stable/tutorials/ui/gui_containers.html),
[themes](https://docs.godotengine.org/en/stable/tutorials/ui/gui_skinning.html),
[navigation and focus](https://docs.godotengine.org/en/stable/tutorials/ui/gui_navigation.html),
and [multiple resolutions](https://docs.godotengine.org/en/stable/tutorials/rendering/multiple_resolutions.html).

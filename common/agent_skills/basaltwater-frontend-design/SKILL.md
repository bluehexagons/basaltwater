---
name: basaltwater-frontend-design
description: Use when creating frontend pages or components, substantially redesigning a user interface, or shaping its layout, visual hierarchy, responsive behavior, and interactions. Skip for backend-only work, isolated bug fixes, or small copy edits.
metadata:
  managed-by: basaltwater
---

# Frontend design

Use this skill for frontend work where visual or interaction design is part of
the requested outcome. The project-development skill should route new screens,
new components with meaningful visual choices, and substantial UI changes here.

## Understand the product

- Read applicable project instructions and inspect the existing app shell,
  design tokens, components, routes, and nearby screens before changing them.
- Identify the screen's audience, purpose, main task, and content from the brief
  and project context. Ask a focused question only when a missing decision would
  materially change the design.
- Preserve the product's established design system, brand, platform conventions,
  and explicit user direction. Where there is no established direction, choose
  one coherent visual approach that fits the product and its audience.
- Decide on a visual voice before building and carry it through composition,
  typography, palette, spacing, and details so the interface feels specific to
  its product instead of like an uncustomized starter template.

## Shape and build the interface

- Give the main task a clear visual priority. Use layout, typography, color,
  spacing, and content density to make relationships and next actions easy to
  understand.
- Use realistic, product-specific content and existing assets. Avoid placeholder
  copy and generic starter layouts by default; use cards, gradients, decoration,
  and motion when they support the product's structure or brief.
- Keep labels and supporting text concise and concrete. Make controls describe
  their action, and make loading, empty, success, and error states useful when
  those states are in scope.
- Reuse the project's components, framework conventions, and dependencies. Keep
  responsive behavior and interaction states in scope, and do not expand into
  unrelated backend or data changes without a request.
- Use semantic controls, keyboard access, visible focus, readable contrast, and
  reduced-motion preferences. Check that the layout works at narrow viewports
  when the interface is responsive.
- Use motion to explain a state change or response to an action; avoid movement
  that competes with the task or ignores reduced-motion preferences.

## Review the result

- Run the relevant local app and use an available project browser workflow when
  practical. Inspect the changed screen at its main viewport and a narrow
  viewport when relevant; check hierarchy, spacing, overflow, focus, and the
  requested interactions.
- Make a final visual pass and correct mismatches with the project's design
  system or the brief. Report what you actually reviewed; do not claim a visual
  check when no browser or rendered view was available.

Design inspiration: [Anthropic's Claude Code frontend-design skill](https://github.com/anthropics/claude-code/blob/main/plugins/frontend-design/skills/frontend-design/SKILL.md).

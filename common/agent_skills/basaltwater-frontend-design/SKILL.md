---
name: basaltwater-frontend-design
description: Design, build, or review interfaces for websites, web apps, mobile apps, games, and desktop software. Use for substantial changes or reviews involving visual direction, layout, responsiveness, or interaction design.
metadata:
  managed-by: basaltwater
---

# Frontend design

Use the guidance below, then open the reference for the surface in scope. Open
additional references when the task spans multiple surfaces.

| Surface | Reference |
| --- | --- |
| Landing pages, portfolios, documentation, and other content-led sites | [Websites](references/websites.md) |
| Dashboards, forms, portals, and tools that run in a browser, including mobile browsers | [Web applications](references/web-applications.md) |
| Installed phone and tablet apps, including native controls around web content | [Mobile applications](references/mobile-applications.md) |
| Godot HUDs, menus, settings, and gameplay overlays | [Godot games](references/godot-games.md) |
| Native windows, editors, utilities, and packaged desktop apps | [Desktop software](references/desktop-software.md) |

## Understand the product

- Identify who uses the screen, what they need to accomplish, and which content
  or state has priority. Read project instructions and inspect nearby screens,
  shared components, and the normal entry point. Ask a focused question when a
  missing decision would materially change the outcome.
- Read supplied references for hierarchy, density, typography, and interaction.
  Translate those choices into the project's own design system and platform
  conventions. For a new visual direction, choose a coherent composition,
  type scale, and palette grounded in the subject and audience.

## Shape and build the interface

- Establish hierarchy with scale, alignment, spacing, and contrast. Give each
  section a clear purpose. Use cards where grouping distinct items or actions
  helps people make a choice. Give everyday work surfaces room for their content.
- Use realistic content and existing assets. Add text that helps users
  understand, navigate, or act. Use imagery to clarify the subject or express
  its identity. Use motion for cues such as a transition or acknowledgment and
  honor reduced-motion preferences where supported.
- Reuse project components, tokens, and dependencies. Use semantic controls,
  visible focus, readable contrast, and clear cues for status and recovery.
  Keep essential actions available across supported inputs.

## Review the result

- Run the app through the project's normal entry point and use the available
  visual review workflow under the active session's tool policy. Inspect the
  rendered interface and exercise the main task, including relevant layout
  constraints, alternate states, and input methods. When comparing captures,
  match the data, state, and dimensions so changes are attributable. If the
  target runtime is unavailable, use existing captures, component previews,
  or project tests and qualify conclusions to the checks they support.
- For a critique, tie findings to an observable state or interaction,
  explain the user impact, and prioritize changes that help the affected task.
- When asked to iterate, rerun the main task on each revision. Check affected
  flows and shared components for the next material improvement. Continue
  through design, implementation, and verification while worthwhile work
  remains in scope. Report the checks performed and any coverage gaps.

Design references are available from [Anthropic](https://github.com/anthropics/skills/blob/main/skills/frontend-design/SKILL.md)
and [OpenAI](https://developers.openai.com/blog/designing-delightful-frontends-with-gpt-5-4).

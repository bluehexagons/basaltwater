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

## Choose a starting point

Identify the user, the task they need to complete, and which content or state
has priority. Read the project instructions and the relevant part of its design
guide (such as `DESIGN.md`), component examples, and tokens where they exist.

- For a new direction, choose a composition, type scale, and palette grounded
  in the subject and real content. Use a sketch or visual concept when seeing
  the direction early would resolve a consequential design choice.
- For a supplied design, identify the intended hierarchy, density, and states.
  Use the project's components and tokens to implement them, and infer
  unspecified responsive and interactive behavior from nearby screens.
- For an existing interface, use its normal entry point to observe the workflow
  and related screens before choosing changes. Capture a baseline when it will
  help compare the result.

Make routine, reversible design decisions and state assumptions that affect the
result. Ask when a consequential choice depends on information only the user
can provide. Record durable design decisions in the project’s existing guidance
when they would help on a different screen.

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

## Verify and iterate

- Choose the workflow, relevant states, and observable outcomes to verify.
  Run the app through its normal entry point using the available tools under
  the session policy. For appearance, inspect the rendered UI at its intended
  size and a relevant constraint. Match data, state, and dimensions for
  before/after comparisons. Use a sequence when timing or motion matters.
  For behavior, exercise the controls with supported input and verify the
  resulting state, including a relevant edge or recovery path.
- Tie critique findings to an observed state or interaction and its user
  impact. Use technical checks to investigate issues such as keyboard access,
  overflow, or slow rendering when they affect the surface under review.
- When asked to iterate, fix the highest-impact findings, rerun affected
  checks, and inspect an adjacent state or shared component for regressions.
  Revisit the whole brief before handing back and continue while a fresh pass
  finds a material improvement worth making within scope.
- If the target runtime is unavailable, use existing captures, component
  previews, or project tests. Report the evidence and what it covers, including
  any workflow or platform that could not be exercised.

Design references are available from [Anthropic](https://github.com/anthropics/skills/blob/main/skills/frontend-design/SKILL.md)
and [OpenAI](https://learn.chatgpt.com/use-cases/frontend-designs).

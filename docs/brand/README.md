# Basaltwater identity

Basaltwater is infrastructure management, from one machine to your whole
network. Use **Basaltwater** in prose, `basaltwater` for the distribution,
and `basaltw` for commands. Use the old project name only when explaining
the one-time migration or historical evidence. Keep bluehexagons maintainer attribution.

## Assets and construction

The symbol is two hexagonal basalt columns above a stepped current. Each
column has a six-sided top and two upright faces, separated by open joints.
It uses a 32-unit grid, a two-unit waterline, and square corners. Keep at least
eight units of clear space around the symbol. Minimum displayed sizes are 16 pixels for
the symbol and 176 pixels for the wordmark. Do not stretch, rotate, animate,
add shadows to, or recolor individual pieces of the mark.

| Use | Light background | Dark background | One color |
| --- | --- | --- | --- |
| Compact symbol / favicon source | [SVG](symbol-light.svg) | [SVG](symbol-dark.svg) | [SVG](symbol-mono.svg) |
| Wordmark | [SVG](wordmark-light.svg) | [SVG](wordmark-dark.svg) | [SVG](wordmark-mono.svg) |

The [favicon](favicon.svg) uses the compact mark and adapts its ink and
water colors to the browser's light/dark preference. Every panel view links to
`/favicon.svg`; the handler serves it as `image/svg+xml` without reading host
state. Static specimens use a relative `favicon.svg` link, so they also work
under a subdirectory. Use `<link rel="icon" href="../favicon.svg"
type="image/svg+xml" sizes="any">` on other sites. No binary ICO or PNG is needed.

Use the theme-specific wordmark in README/documentation headers. For a
single-color print process, use the monochrome version; reverse it to white
on a dark substrate. The web panel uses the same inline symbol without an
image request. The panel's content security policy allows same-origin images
for the favicon; scripts, remote images and embedded data remain blocked.

## Icons and textures

The eighteen original [icons](index.html) share a 24-unit
grid and a 1.6-unit stroke. Hexagonal frames and angular cuts echo the mark,
while keys, clocks, controls and other familiar shapes keep actions recognizable.
The panel renders the icons inline using `render_icon()` from
`common/service_tools/web_panel_templates.py`. They inherit `currentColor` and
display at 18 pixels in navigation; use 18–32 pixels elsewhere. Keep a visible
text label on controls. Decorative icons have `aria-hidden="true"`; provide
the helper's `label` argument when an icon needs its own accessible name.
Individual `brand/icon-*.svg` exports are available for other pages and tools.
Thirteen icons cover navigation; storage, data, import, export and power icons
extend the family to agent tools and administration. Page eyebrows, section
headings, service summaries and tool rows reuse the same geometry. `render_heading()` pairs a
decorative icon with a visible title and preserves its optional anchor ID.

The [connected basalt artwork](artwork-network.svg) accompanies the
unloaded local-service view and the dashboard's empty service list. It shares
the mark's column geometry and adapts to the browser theme. Use it as supporting
decoration alongside an explanation; it does not indicate service health.

| Texture | Light background | Dark background |
| --- | --- | --- |
| Hexagonal basalt joints | [SVG](texture-basalt-light.svg) | [SVG](texture-basalt-dark.svg) |
| Stepped water strata | [SVG](texture-water-light.svg) | [SVG](texture-water-dark.svg) |
| Flowing contours | [SVG](texture-contours-light.svg) | [SVG](texture-contours-dark.svg) |
| Stone facets | [SVG](texture-facets-light.svg) | [SVG](texture-facets-dark.svg) |
| Mineral grain | [SVG](texture-mineral-light.svg) | [SVG](texture-mineral-dark.svg) |

The transparent texture swatches repeat at 192 × 168 pixels for basalt and
192 × 128 pixels for water. Basalt uses a 24 × 42-unit tile; water uses a
96 × 32-unit tile. Contours use a 96 × 64-unit tile; facets and mineral grain
use 64 × 64-unit tiles. Their 192 × 256-pixel swatches also repeat seamlessly.
Use them sparingly on headers or supporting surfaces, with a plain surface
behind dense information.

The dashboard keeps basalt joints. Agent pages use flowing contours;
administration, maintenance and jobs use angular facets; access, inbox and
diagnostics use mineral grain; services and desktop access use stepped water.
Header textures fade toward the text and use ten percent opacity. A seven-percent
mineral texture sits at the bottom of the desktop sidebar; an eight-percent
water texture accents footers.
The mobile navigation omits the sidebar texture. Forms, logs, tables and metric
surfaces remain plain. All textures are decorative, ignore pointer input, and
disappear in forced-color mode. There is no animation.

Other sites can repeat the exports with CSS
`background-image`; choose the matching light/dark variant. The exports already
include their subdued opacity.

All artwork is small, readable SVG XML: paths, local patterns, and live text
in the wordmark. There are no bitmap images, embedded payloads, scripts, filters,
external resource references, or editor metadata. The SVGs open directly in Inkscape.
Edit their canonical geometry in the template module, then regenerate the
exports so the panel and documentation keep the same identity.
When repeating a texture inline in one document, supply a distinct `pattern_id`
to `render_texture()` for each instance so its local SVG paint reference remains
unambiguous. The shared frame uses separate header, sidebar and footer IDs.

## Palette and typography

[Palette JSON](palette.json) and [CSS tokens](theme.css) are
generated from `BRAND_PALETTES` in
`common/service_tools/web_panel_templates.py`, the runtime source of truth.
Use semantic tokens instead of copying color values into each screen.

| Token | Meaning |
| --- | --- |
| `bg`, `panel` | Page and raised surfaces |
| `text`, `muted` | Primary and supporting text |
| `accent`, `accent-soft` | Links, actions, keyboard focus, selected backgrounds |
| `line` | Control boundaries and separators |
| `ok`, `warning`, `bad` | Healthy, attention, failure; always pair with text |
| `bad-soft` | Muted rose surface for error badges |
| `brand-water` | The symbol's current |
| `workspace`, `workspace-soft` | Violet for agents, workspace data and hosted processes |
| `stone`, `stone-soft` | Copper for administration, maintenance and storage tools |
| `sea`, `sea-soft` | Green for access, trust and published sites |

Category accents appear on navigation icons, headers, section icon tiles,
resource-card edges, service surfaces, and tool/admin row edges. Use `tone-water`,
`tone-workspace`, `tone-stone` or `tone-sea` with `--section-ink` and
`--section-soft` to reuse these accents. Status labels continue to use the
separate `ok`, `warning` and `bad` tokens, with explicit text.

Hover and keyboard focus emphasize the same category edges on resource cards.
The sidebar mark links to the dashboard; the skip link remains the first keyboard
stop. Inputs, selects and text areas share a visible focus ring. Journal entries
use severity-colored edges and labeled badges. Job cards show the next trigger,
last finish and boot setting first; a native disclosure contains the full run and
timer details. Numeric values use tabular figures to make adjacent values easier
to compare. These treatments require no JavaScript or animation.

Both themes meet a 4.5:1 minimum for text, supporting text, links, and
status labels on their page/panel/selected backgrounds. The same text contrast
checks include the three category surfaces and inks. Boundaries meet
3:1 on page/panel backgrounds. Buttons use the panel color on the accent;
the same tested ratio applies in reverse. Focus outlines use the accent.
Tests enforce these actual color pairs, rather than certifying raw swatches.
The palette follows the OS light/dark preference and the mark respects
forced colors. No motion is introduced, including with reduced motion off.

Use DejaVu Sans for the interface and DejaVu Sans Mono for commands, with
system fallbacks. The editable wordmark SVG keeps its text live. Fonts are
not bundled or downloaded; install DejaVu when reproducing the reference
wordmark exactly. DejaVu changes are public domain over Bitstream Vera's
permissive font license; see [font attribution](font-license.txt).

The mark and generated artwork are original project assets by bluehexagons
contributors, distributed under the repository's [Apache-2.0 license](../../LICENSE).
No third-party image or icon assets are used. Sibling projects may reuse the
grid, typography and semantic state colors while choosing their own name,
symbol and accent. No sibling product identity is included in this release.

## Reproduction and review

Run `python3 scripts/export_brand.py` to regenerate the logo, icons, textures,
tokens and static specimens. The generator renders the existing panel with synthetic
data and a fixed `/home/operator` account directory, independent of the account
or clock running the export. Diagnostic state is synthetic too. Regression tests
compare every exported asset under root and developer account fixtures and block
host commands, task-storage reads, and credential inspection.
`make brand-check` (also part of `make check`) renders into a temporary
directory and fails if committed exports are missing or stale, without changing
the checkout. The generator mocks host discovery; it does not contact a service
or read private host state. Review [identity](index.html), [README](readme.html),
[web panel](panel.html), [agent tools](tools.html),
[Git and credentials](credentials.html), and
[service artwork](services.html), [administration](admin.html), [Shares & Data](shares-data.html),
[scheduled jobs](jobs.html), [diagnostics](logs.html) and the
[agent workbench](agents.html) together,
for example through a loopback
`python3 -m http.server --bind 127.0.0.1 --directory docs/brand` server.

For working forms and source reload, use `make panel-dev` from an activated
development environment. Its disposable scenarios use the production handlers
and share sample job records with these exports. See
[local panel development](../agents/browser-testing.md#develop-the-web-panel-locally)
for startup, scenario controls and simulated-action boundaries.

Review light/dark themes at 375 and 1280 pixels, keyboard skip-link focus,
horizontal overflow, native disclosures, and the symbol at 16 pixels when
changing the geometry. Loaded jobs and diagnostics specimens use fixed synthetic
records; generating them never queries the service manager, journal or storage.
Specimen navigation links to the corresponding local HTML files, including
dashboard section anchors. Controls remain static; the previews never run tasks
or submit host actions. The agent workbench uses unavailable execution and
synthetic template/model data without reading local credentials or caches.
CLI output remains readable without color; branding adds no ANSI sequences.

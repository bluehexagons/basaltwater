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
| Compact symbol / favicon source | [SVG](brand/symbol-light.svg) | [SVG](brand/symbol-dark.svg) | [SVG](brand/symbol-mono.svg) |
| Wordmark | [SVG](brand/wordmark-light.svg) | [SVG](brand/wordmark-dark.svg) | [SVG](brand/wordmark-mono.svg) |

Use the theme-specific wordmark in README/documentation headers. For a
single-color print process, use the monochrome version; reverse it to white
on a dark substrate. The web panel uses the same inline symbol without an
image request or a change to its restrictive content security policy.

## Icons and textures

The thirteen original [navigation icons](brand/index.html) share a 24-unit
grid and a 1.6-unit stroke. Hexagonal frames and angular cuts echo the mark,
while keys, clocks, controls and other familiar shapes keep actions recognizable.
The panel renders the icons inline using `render_icon()` from
`common/service_tools/web_panel_templates.py`. They inherit `currentColor` and
display at 18 pixels in navigation; use 18–32 pixels elsewhere. Keep a visible
text label on controls. Decorative icons have `aria-hidden="true"`; provide
the helper's `label` argument when an icon needs its own accessible name.
Individual `brand/icon-*.svg` exports are available for other pages and tools.

| Texture | Light background | Dark background |
| --- | --- | --- |
| Hexagonal basalt joints | [SVG](brand/texture-basalt-light.svg) | [SVG](brand/texture-basalt-dark.svg) |
| Stepped water strata | [SVG](brand/texture-water-light.svg) | [SVG](brand/texture-water-dark.svg) |

The transparent texture swatches repeat at 192 × 168 pixels for basalt and
192 × 128 pixels for water. Basalt uses a 24 × 42-unit tile; water uses a
96 × 32-unit tile. Use them sparingly on headers or supporting surfaces,
with a plain surface behind dense information. The
panel's header uses the basalt pattern inline at nine percent opacity and
hides it in forced-color mode. Other sites can repeat the exports with CSS
`background-image`; choose the matching light/dark variant. The exports already
include their subdued opacity.

All artwork is small, readable SVG XML: paths, local patterns, and live text
in the wordmark. There are no bitmap images, embedded payloads, scripts, filters,
external resource references, or editor metadata. The SVGs open directly in Inkscape.
Edit their canonical geometry in the template module, then regenerate the
exports so the panel and documentation keep the same identity.

## Palette and typography

[Palette JSON](brand/palette.json) and [CSS tokens](brand/theme.css) are
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
| `brand-water` | The symbol's current |

Both themes meet a 4.5:1 minimum for text, supporting text, links, and
status labels on their page/panel/selected backgrounds. Boundaries meet
3:1 on page/panel backgrounds. Buttons use the panel color on the accent;
the same tested ratio applies in reverse. Focus outlines use the accent.
Tests enforce these actual color pairs, rather than certifying raw swatches.
The palette follows the OS light/dark preference and the mark respects
forced colors. No motion is introduced, including with reduced motion off.

Use DejaVu Sans for the interface and DejaVu Sans Mono for commands, with
system fallbacks. The editable wordmark SVG keeps its text live. Fonts are
not bundled or downloaded; install DejaVu when reproducing the reference
wordmark exactly. DejaVu changes are public domain over Bitstream Vera's
permissive font license; see [font attribution](brand/FONT_LICENSE.txt).

The mark and generated artwork are original project assets by bluehexagons
contributors, distributed under the repository's [Apache-2.0 license](../LICENSE).
No third-party image or icon assets are used. Sibling projects may reuse the
grid, typography and semantic state colors while choosing their own name,
symbol and accent. No sibling product identity is included in this release.

## Reproduction and review

Run `python3 scripts/export_brand.py` to regenerate the logo, icons, textures,
tokens and static specimens. The generator renders the existing panel with synthetic
data. `make brand-check` (also part of `make check`) renders into a temporary
directory and fails if committed exports are missing or stale, without changing
the checkout. The generator mocks host discovery; it does not contact a service
or read private host state. Review [identity](brand/index.html), [README](brand/readme.html),
and [web panel](brand/panel.html) together, for example through a loopback
`python3 -m http.server --bind 127.0.0.1 --directory docs/brand` server.

Review light/dark themes at 375 and 1280 pixels, keyboard skip-link focus,
horizontal overflow, and the symbol at 16 pixels when changing the geometry.
CLI output remains readable without color; branding adds no ANSI sequences.

# Basaltwater identity and project rename

Status: v2.0.0 repository implementation complete, including the package/CLI,
active guides, bundled skills and [visual identity](../BRANDING.md).
Public release operations and live VM qualification remain. See
[cutover contracts](BASALTWATER_CONTRACTS.md) and
the [operator migration guide](../BASALTWATER_MIGRATION.md).
This document records the branding discussion and the full project scope.
The implementation renames the Python entry point and internal resources,
with a one-time migration from recent installations and no runtime
compatibility aliases after cutover. The [roadmap](ROADMAP.md)
continues to own delivery priority.

## Naming decisions

| Context | Name | Convention |
| --- | --- | --- |
| Public display name | Basaltwater | Use in document titles, release announcements, and ordinary product references. |
| Technical identity | `basaltwater` | Distribution name; the existing `bluehexagons/infra_tools` repository remains the source host. Publication clearance remains on the [release checklist](../BASALTWATER_RELEASE.md). |
| Primary command | `basaltw` | Use consistently in installation instructions, examples, completion, and generated commands. |
| Maintainer identity | bluehexagons | Retain attribution without requiring company knowledge to understand or use the project. |
| Transitional description | Basaltwater, formerly infra-tools | Use where it helps existing users recognize the rename; retire after a defined transition period. |

Lowercase `basaltwater` is also appropriate in a wordmark. Capitalization is a
presentation convention, not a separate brand. Avoid `BasaltWater` and the
two-word form `Basalt Water` in canonical naming.

`basaltw` supersedes the briefly suggested `basalt` command. Do not install
`basalt`, `bw`, or `b6` as additional default executables. The earlier `bw`
suggestion was only a possible personal alias; documentation should teach the
canonical command. A separate alias is unnecessary for launch.

Basaltwater was selected for its approachable, memorable character and its
combination of durable foundations with flowing water. Its identity should
stand independently of bluehexagons. Public explanations can use material,
water, and geometric imagery; no additional origin story is needed.

## Positioning and language

The project should make managing one machine, multiple networks, and
homelab- or small-business-sized datacenters straightforward. These audiences
guide usability without imposing an artificial limit on future scope.

Working descriptor:

> Infrastructure management, from one machine to your whole network.

Use calm, direct, practical language. Keep command verbs descriptive and
consistent with implemented behavior. Renaming is not a reason to redesign
the command tree or introduce hypothetical commands from branding examples.
Terms such as flow, soak, and cure are optional future vocabulary, not
reserved commands or promised features.

## Visual identity

Build around charcoal and steel blue/cyan, with water-inspired highlights.
The desired character is capable, approachable, and a little playful.
The following colors preserve the original design proposal. The implemented,
contrast-tested light/dark values and assets are in the [identity guide](../BRANDING.md);
use its generated semantic tokens for new interfaces instead of these anchors:

| Token | Starting color | Intended role |
| --- | --- | --- |
| Charcoal | `#17232C` | Dark foundation, primary text in light themes |
| Slate | `#344B58` | Secondary surfaces and supporting elements |
| Steel blue | `#287E9C` | Primary brand color |
| Cyan | `#4DC5DD` | Selected elements and sparing highlights |
| Mist | `#D8F0F4` | Pale surfaces and light foreground candidates |
| Sea-glass | `#5FB89F` | Success and healthy states |
| Sandstone amber | `#D99B52` | Warnings and warm complementary accents |
| Coral | `#D66D62` | Errors and destructive actions |

Develop separate light and dark theme values from these anchors. Validate
actual foreground/background combinations for text, icons, controls, and
focus indicators before adoption; these swatches are not contrast-certified.
Pair status colors with labels or icons, and keep decorative warm accents
visually separate from warning states.

A proposed mark uses a small cluster of angular columns and a cyan current
or waterline. Explore a shared geometric grid, consistent stroke weights,
corner treatment, spacing, and typography. A literal hexagon is optional.
The mark should remain recognizable at favicon size and in monochrome.
Water motion, if used, should be restrained and respect reduced-motion
preferences. Choose legible, openly licensed interface and monospace fonts
when producing visual specimens.

The delivered visual design includes a wordmark, compact symbol, monochrome
variants, light/dark palette tokens, and a usage guide. README, documentation,
and existing web-panel specimens have been reviewed together. Regeneration and
verification instructions live in the identity guide.

Keep editable vector sources and generated exports in the repository, with
font/asset licenses and attribution. Define reusable semantic tokens for
background, text, action, focus, and status instead of copying raw hex values
into each interface. Include terminal output with color disabled in the
readability review. Visual exploration may proceed alongside the technical
rename; a complete sister-project design system is not a launch dependency.

## A family of independent projects

Basaltwater should support a recognizable family without forcing future
projects to begin with Basalt or end with water. Favor evocative, pronounceable
names with simple lowercase identifiers. Each sibling gets its own name and
symbol, sharing typography, geometric construction, neutral colors, spacing,
and semantic state colors. Product accent colors may vary.

These are naming studies only; none has been selected or availability-checked:

| Possible sibling | Illustrative name | Family relationship |
| --- | --- | --- |
| Minimal open-source password manager | Slatekey | A compact enclosure or key shape on the shared grid; steel blue with a restrained lavender accent. |
| Backup and restore utility | Cairnwell | Stacked shapes and a sheltered center; blue-green accents. |
| Lightweight service monitor | Tideglass | A simple observation/window motif; clearer cyan accents. |

The password manager is an intended future project, not part of this rename.
The other examples test whether the convention can support a wider family;
they are not roadmap commitments. Keep success, warning, and error meanings
consistent across products even when their brand accents differ.

## Availability and unresolved choices

Earlier exploratory searches found substantial software usage of Basalt and
Basaltic. Searches for the joined name Basaltwater did not reveal an obvious
software product, but did not establish domain, registry, or trademark
availability. Do not present those searches as clearance or repeat earlier
unverified availability claims as facts.

Before public cutover, record current checks for the intended GitHub location,
Python distribution name, relevant domain names, `basaltw` executable usage,
and relevant trademark records. Select a domain and repository owner/path;
keeping bluehexagons as the owner is compatible with an independent identity.
Decide the release version and transition window, and document any temporary
compatibility behavior with explicit removal criteria.

Record each availability check with its date, exact identifier, registry or
source, and result. Distinguish an unused identifier from one the project
actually controls. A new domain is optional; existing repository hosting can
support the release. The current names are the intended choices, subject to
resolving a concrete collision if these checks uncover one.

## Technical rename scope

The approved implementation is a full rename, superseding the earlier proposal
to preserve module paths, service names and skill IDs through v2.x. The source
entry is `basaltwater.py`, the package is `basaltwater`, and the CLI is `basaltw`.
Runtime paths, owned configuration, environment settings, service/account names,
managed skills and deployment manifests use the Basaltwater namespace.

The only compatibility feature is a one-time migration from recent
infra-tools installations, run automatically on setup targets before setup
steps and explicitly for standalone/controller migration. It does
not create old-name aliases. Historical release support, old environment-variable
fallbacks and mixed old/new operation after successful cutover are excluded.

See [contracts](BASALTWATER_CONTRACTS.md) for the surface mapping and the
[migration guide](../BASALTWATER_MIGRATION.md) for user/system passes, conflict
handling, interrupted-cutover recovery and external automation changes.

## Acceptance and release boundary

- Source, package metadata, launchers, internal callers, help and completion
  consistently use the new entry point and command.
- Recent-install migration preserves private data, refuses ambiguous conflicts,
  reconciles managed skills/configuration and coordinates service cutover.
- A failed or interrupted migration has a private recovery journal; successful
  cutover does not retain support for operating the old release.
- Focused migration tests use temporary directories and mocked service/account
  operations. The default suite and isolated fresh-wheel checks pass.
- Active operator documentation, plans and generated visual assets match the
  final implementation; historical records keep their original context.
- Disposable live-host qualification, publication and the owner's later GitHub
  rename remain separate items on the [release checklist](../BASALTWATER_RELEASE.md).

The visual identity and family guidance above remain delivered. No repository
rename, live host mutation or package publication is claimed by this PR.

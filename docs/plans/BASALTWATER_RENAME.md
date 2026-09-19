# Basaltwater identity and project rename

Status: project brief; name and CLI selected, implementation unscheduled.
This document records the branding discussion and defines the scope of a
future rename. Adding this plan does not rename the running software,
repository, packages, or managed installations. The [roadmap](ROADMAP.md)
continues to own delivery priority.

## Naming decisions

| Context | Name | Convention |
| --- | --- | --- |
| Public display name | Basaltwater | Use in document titles, release announcements, and ordinary product references. |
| Technical identity | `basaltwater` | Use for the intended repository and distribution name and other lowercase identifiers. Availability remains to be checked. |
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
The following colors are a starting proposal, not an approved UI theme:

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

Deliverables for the visual design pass are a wordmark, compact symbol,
monochrome variants, light/dark palette tokens, and a short usage guide.
Evaluate them together in a README header, documentation page, and existing
web-panel screen before committing to a finished identity.

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

The current package metadata in `pyproject.toml` declares distribution
`infra_tools`, module `infra_tools`, and script `infra-tools = infra_tools:main`.
These are separate surfaces and need a coordinated update. The intended
distribution is `basaltwater`, with `basaltw` as its console entry point.
Determine the final module layout after reviewing imports and callers.

Create an old-to-new inventory before editing runtime code. Include spelling
variants `infra-tools`, `infra_tools`, and `INFRA_TOOLS`, and classify each
occurrence as public branding, executable contract, persisted contract,
external reference, or historical evidence.

| Surface | Required review |
| --- | --- |
| Packaging and commands | Distribution/module names, console scripts, direct Python invocation, imports, completion, help text, build artifacts, and uninstall behavior. |
| Installation and updates | `install.sh`, source/archive URLs, channels, environment variables, managed-install markers, staged source, and upgrade/rollback logic. |
| Saved state and recovery | Local and target configuration, ownership records, credentials, backups, transaction journals, inventory, and reconstruction/recall paths. |
| Managed system resources | Services, timers, sudoers rules, hooks, runtime files, locks, temporary paths, logs, and service discovery. |
| External and machine-readable contracts | JSON fields, schemas, API/readiness routes, webhook consumers, monitoring rules, authentication scopes, and generated configuration consumed outside this repository. |
| Agent integrations | Bundled skill names and content, installation/reconciliation, readiness checks, tool registration, and generated agent instructions. |
| Documentation and hosting | README, operator/contributor guides, examples, links, badges, repository metadata, CI, releases, and any published site. |
| Tests and fixtures | Assertions, temporary filesystem layouts, mocked commands, packaging checks, and supported migration scenarios. |

Concrete existing examples include `.infra_tools/managed-install` and
`INFRA_TOOLS_*` in `install.sh`, `/opt/infra_tools/infra_tools.py` in
`lib/recall.py`, `/var/lib/infra_tools/user-renames` in `lib/user_rename.py`,
and `infra-tools-locks-*` in `lib/concurrency.py`. These are inventory starting
points, not a complete list or permission to replace strings blindly.

Preserve third-party project names and external historical references.
For contracts such as `infra.json`, make an explicit retain-or-migrate
decision based on user impact; a branding change does not require changing
every generic use of the word infra.

For each inventory entry, record its producer and consumers, proposed name
or explicit retention decision, migration action, verification, and any
transition end. Default to retaining existing serialized keys and manifest
formats unless a change has a concrete benefit and a versioned migration.
Do not combine the rename with unrelated schema or package-layout redesign.

Resource names can participate in security and ownership checks: review
systemd sandbox paths, sudoers command paths, firewall ownership comments,
log parsers, and readiness probes together with their targets. For example,
`web/cicd_steps.py` embeds working directories, environment files, and
`ReadWritePaths`, while `web/homebox_steps.py` generates a branded readiness
route. Updating only visible labels or filenames would leave stale consumers.

## Release and migration contracts

The delivery sequence below describes development order. Package, installer,
caller, and required state/resource migration changes must reach a coherent
release boundary before users receive the new entry point. If intermediate
commits are exposed through an update channel, each must remain usable.

A new distribution name is not automatically discovered by an upgrade of the
old distribution. Define how existing Git-managed installations and Python
package installations reach the new release, including users pinned to a
release or following an update channel. Document the exact upgrade command
and how ownership of installed files transfers. Test old-package uninstall
ordering so it cannot remove files or launchers belonging to the new install.

Specify precedence for old/new environment variables and configuration
locations, including what happens when both are set. Preserve explicit user
paths. An ordinary help, status, or inspection command must not move state
as a side effect of recognizing the old name.

Make migration repeatable and recoverable after interruption. Record
completion only after verifying the destination, preserve permissions and
ownership, and define treatment of symlinks and user-modified managed files.
Keep recovery data until verification succeeds. If new state cannot be read
by an older release, rollback must restore the matching state snapshot or
report the unsupported downgrade before making changes.

## Delivery sequence

1. **Inventory and cutover design.** Finish the naming/availability checks,
   produce the contract mapping, and choose the release boundary. Specify
   which identifiers change immediately and which require a later migration.
2. **Package and CLI.** Implement the selected distribution/module layout,
   `basaltw` launcher, completion, help, and generated invocation changes in a
   coherent slice. Update all callers and remove unused code.
3. **Installation and state migration.** Cover fresh installs and upgrades
   from the previous supported release. Preserve ownership, permissions,
   credentials, inventory, and recovery data. Specify precedence and conflict
   handling if both old and new state locations exist. Define interrupted
   migration recovery and rollback before changing persistent paths.
4. **Managed resources and integrations.** Reconcile services, timers, agent
   skills, and related resources. Avoid duplicate scheduled work and ensure
   old/new processes cannot bypass mutual exclusion through renamed locks.
   Support the documented interval where controller and targets differ in
   version, or require an explicit coordinated upgrade.
5. **Public identity and release.** Update active documentation, examples,
   assets, repository/package metadata, and installation URLs together.
   Publish migration instructions and the temporary “formerly infra-tools”
   wording. Verify redirects and raw/download URLs individually.
6. **Transition completion.** Remove temporary migration mechanisms at the
   documented boundary and review residual old-name references. Preserve
   historical evidence and intentionally retained data contracts.

Follow repository rules against retaining unused code for compatibility.
Any necessary transition mechanism must have a concrete supported upgrade
use, tests, an owner, and removal criteria. Do not silently remove the old
launcher while generated automation still invokes it.

## Acceptance criteria

- Fresh installation exposes `basaltw`; package metadata, help, completion,
  and operator examples agree on the selected naming.
- Upgrade and rollback scenarios preserve managed state and credentials;
  conflicting old/new state produces an explicit resolution path.
- Existing scheduled work, remote reconstruction, and managed agent
  integrations work across the documented transition boundary without
  duplicate jobs or independent lock namespaces.
- Focused tests exercise meaningful migration and caller behavior using
  mocked system operations and temporary directories. Packaging and installer
  smoke checks cover both a fresh install and a previous-release upgrade.
- Verification covers root/system and non-root/user installs, custom install
  paths, Git-managed and Python-package installs, and the supported Debian
  and CachyOS workflows. Exercise a migration rerun, interruption recovery,
  conflicting state, old/new environment settings, and rollback. Use
  disposable environments for installation and service lifecycle checks.
- Existing users can discover and execute the documented upgrade; old-package
  removal does not damage the new installation. Inspection commands do not
  perform hidden migration, and retained machine-readable contracts remain
  compatible with their consumers.
- Active documentation and owned links are current. Remaining old-name
  occurrences are reviewed and explained, rather than requiring zero matches.
- Visual specimens establish consistent light/dark colors, readable contrast,
  a small/monochrome mark, and a reusable family style.
- The release notes identify the command/package changes, operator actions,
  supported version combinations, rollback procedure, and transition end.

Actual runtime renaming, external repository changes, domain registration,
package publication, visual asset production, and sister-project development
are subsequent implementation work. This brief establishes their scope
without claiming they have been completed.

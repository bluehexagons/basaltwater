# Project development standards

Basaltwater is the primary supported development environment for these projects.
Debian is the default; projects can also support development or testing on
CachyOS and Windows 11 where useful. Basaltwater supplies shared tooling, skills
and reference workflows. Projects retain their native toolchains and adapt the
standards to their own needs. Installing Basaltwater is not a prerequisite for
editing, building or testing a project.

## Shared environment, native project behavior

Keep project-specific commands, dependency pins, export presets, tests, fixtures
and release requirements in the project. Document the canonical commands once in
its README or contributor guide. Keep `AGENTS.md` focused on local constraints
and links to those commands and relevant shared skills.

Use Basaltwater for environment preparation, runtime selection, browser and
desktop access, development gateways, agent coordination and guarded cleanup.
Prefer small adapters describing project paths and commands over copies of
shared infrastructure. A native bootstrap or wrapper is useful when it performs
project-specific work; it should not silently install or configure Basaltwater.

On another environment, install the documented native prerequisites and run the
same project commands. Optional integration features may require additional setup
or manual steps. Describe that route next to the integration, including its
limits; do not maintain a second implementation solely to hide the absence of
Basaltwater. Keep ordinary CI and native build/test commands independent of
optional panel, gateway, broker or agent services.

## Platforms and verification

Name each project's intended development and runtime targets. Document meaningful
differences, such as shell requirements, toolchain versions, graphics support and
export templates. Avoid hardcoded Debian home paths in portable project config.
A shell script can remain a Linux workflow with a documented equivalent editor
or native command on Windows; it need not grow a second framework.

Report the platform and checks actually exercised. A successful Debian build or
cross-platform export does not establish a native Windows 11 or CachyOS runtime
test. Keep costly platform qualification separate from fast developer checks
unless the project requires it for every change. Preserve existing useful CI.

## Adopt only what helps

Use the [project adoption skill](../../common/agent_skills/basaltwater-project-adoption/SKILL.md)
to discover existing conventions and close concrete gaps. The
[project development skill](../../common/agent_skills/basaltwater-project-development/SKILL.md)
covers scoped changes, checks and waiting for jobs. Projects do not need every
manifest, standard directory or optional service.

For reusable rootless Podman test images and task container cleanup, follow the
[container lifecycle reference](../../common/agent_skills/basaltwater-project-development/references/containers.md).

For artifacts, distinguish reproducible output from review evidence and curated
history. Follow [project cleanup conventions](project-cleanup.md), preserving
existing layouts when useful. Reuse the shared generated-output remover instead
of copying deletion logic; existing native cleanup tools remain valid. Policies
and guidance describe scope rather than granting standing deletion permission.

When a project exposes a recurring environment gap, improve the owning shared
Basaltwater tool or skill and keep only the project-specific configuration in
that repository. Document the native fallback and any verification still needed.

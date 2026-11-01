# Antistatic integration

Antistatic is an optional Basaltwater integration. Its lobby/database support
remains part of setup; its game and authoring workflows are documented here so
other projects can use the general host guides without adopting this layout.
Project-owned files at the checked-out revision remain authoritative. Repository
access may be required; Basaltwater setup does not grant it.

| Task | Guide |
| --- | --- |
| Deploy lobby, reports, STUN or antistatic-db | [Services](services.md) |
| Develop the game, Animator or Blender sources on CachyOS | [CachyOS development](cachyos.md) |
| Build and qualify on native Windows | [Windows development](windows.md) |
| Select dependencies and runner prerequisites | [Runner inputs](runners.md) |

Start with the general [CachyOS game tooling](../../workstations/cachyos-game-development.md),
[Windows game tooling](../../workstations/windows-game-development.md) or
[Debian native tooling](../../agents/project-tooling.md#debian-native-game-development)
for host preparation. Choose host roles for the current task; an example
workflow does not describe the operator's deployed machines.

For Linux release packaging, Antistatic's `npm run package:linux` uses its
pinned Steam Runtime SDK. Follow the checked-out project's bootstrap, SDK and
ABI gates; ordinary source builds remain independent of Basaltwater.

Keep project commands, runtime/library pins, release SDKs and detailed QA in
[Antistatic](https://github.com/bluehexagons/antistatic). Host setup does not
clone every related repository, install project dependencies or publish a build.
Do not copy private assets, release plans, deployment inventories or raw local
test evidence into this integration guide.

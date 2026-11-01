# Antistatic runner inputs

Use the [general multi-repository build guide](../../services/github-runner-repositories.md)
for runner registration, pinned checkouts and source authentication. This page
covers Antistatic's dependency layout; it is not required for other projects.

The current game build needs its checkout and `npm ci`. The lockfile pins tagged
public archives for translations, capacitor, easing, trace and the server protocol.
Windows tests build their Go lobby fixture from that installed protocol package.
Runtime assets and model exports are committed with the game.

Assets and animator repositories serve authoring; the server and database are
separate services. Add source checkouts only when a build regenerates assets or
coordinates source changes. Follow Antistatic's `docs/development/dependencies.md`:
publish dependency tags before updating the game's lockfile. A sibling checkout
does not replace an installed npm package.

When moving a workflow from hosted runners, its Windows jobs require
changing `runs-on` and installing **MSVC C++ Build Tools, CMake and Ninja** on the
Windows host. Keep its pinned Node/Go setup and `npm run bootstrap:windows` steps.
Hosted-image tools are not supplied by runner registration. The existing Linux
container job still requires a Linux runner/container runtime; WSL access does
not make a Windows runner support that job unchanged.

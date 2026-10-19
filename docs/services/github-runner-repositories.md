# Builds that use several repositories

Register the runner with repositories that **submit jobs**. One job can check
out several dependencies without a registration for each. Build components in
the same job when they share source or outputs; the host queue serializes jobs.

Prefer existing locked packages or pinned submodules. For extra source checkouts,
use separate paths under `GITHUB_WORKSPACE` and explicit commit/tag refs:

```yaml
steps:
  - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09 # v5
    with:
      path: game
      persist-credentials: false
  - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09 # v5
    with:
      repository: OWNER/DEPENDENCY
      ref: REVIEWED_COMMIT_OR_TAG
      path: dependency
      persist-credentials: false
      token: ${{ secrets.DEPENDENCY_READ_TOKEN }}
  - run: ./build-script
    working-directory: game
```

Omit `token` for public dependencies. For private ones, the default job token
only covers its own repository. Use a narrowly scoped read-only PAT, GitHub App
installation token or deploy key through workflow secrets. Runner registration
grants no source access. See [checkout's examples](https://github.com/actions/checkout#checkout-multiple-repos-private).
Never rely on another job's leftover checkout, or wait for a second queued job
while holding the host slot.

## Antistatic

The current game build needs its checkout and `npm ci`. The lockfile pins tagged
public archives for translations, capacitor, easing, trace and the server protocol.
Windows tests build their Go lobby fixture from that installed protocol package.
Runtime assets and model exports are committed with the game.

Assets and animator repositories serve authoring; the server and database are
separate services. Add source checkouts only when a build regenerates assets or
coordinates source changes. Follow Antistatic's `docs/development/dependencies.md`:
publish dependency tags before updating the game's lockfile. A sibling checkout
does not replace an installed npm package.

Antistatic currently selects hosted runners. Moving its Windows jobs requires
changing `runs-on` and installing **MSVC C++ Build Tools, CMake and Ninja** on the
Windows host. Keep its pinned Node/Go setup and `npm run bootstrap:windows` steps.
Hosted-image tools are not supplied by runner registration. The existing Linux
container job still requires a Linux runner/container runtime; WSL access does
not make a Windows runner support that job unchanged.

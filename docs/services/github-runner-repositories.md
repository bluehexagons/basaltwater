# Builds that use several repositories

Register the runner with repositories that **submit jobs**. One job can check
out several dependencies without a registration for each. Build components in
the same job when they share source or outputs; the host queue serializes jobs.

Prefer existing locked packages or pinned submodules. For extra source checkouts,
use separate paths under `GITHUB_WORKSPACE` and explicit commit/tag refs:

```yaml
steps:
  - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
    with:
      path: game
      persist-credentials: false
  - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
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

## Project-specific examples

See [Antistatic runner inputs](../integrations/antistatic/runners.md) for its
locked dependencies and native prerequisites. Other projects should follow their
own dependency and workflow contracts.

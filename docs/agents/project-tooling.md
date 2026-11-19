# Project manifests and Node runtimes

Run these commands from the project checkout. They work without provisioning
or changing a saved host setup.

```bash
basaltw manifest init --dry-run
basaltw manifest init
basaltw manifest validate --json
basaltw node status --json
basaltw node doctor --json
basaltw node exec -- npm test
```

## Choose a topic

<a id="related-checkouts-and-host-roles"></a>
<a id="initialize-a-manifest"></a>
<a id="project-browser-tests"></a>
<a id="repository-owned-ci-workflows"></a>
<a id="publishing-language-declarations"></a>
<a id="installed-cli-and-source-context"></a>
- [Project setup and manifests](project-manifests.md) Checkout layout, deployment manifests, browser checks, CI templates, and installed CLI provenance.

<a id="select-a-project-node-version"></a>
<a id="python-tooling-installation-checks"></a>
- [Node and Python runtimes](project-runtimes.md) Project-scoped runtime selection and diagnostics.

<a id="debian-native-game-development"></a>
<a id="opt-in-native-graphics-context-check"></a>
<a id="project-owned-compiler-caching"></a>
<a id="effective-workload-limits"></a>
- [Debian native development](project-native-builds.md) Native game tools, graphics contexts, compiler caching, and workload limits.

<a id="build-for-an-older-release-baseline"></a>
<a id="container-storage-and-sdk-headroom"></a>
- [Container builds and storage headroom](project-container-builds.md) Older release baselines, isolated builds, and SDK storage checks.

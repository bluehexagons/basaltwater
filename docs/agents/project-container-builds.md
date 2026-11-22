# Container builds and storage headroom

[Documentation index](project-tooling.md)

## Build for an older release baseline

A newer VM's libc and native libraries can exceed a project's supported
release ABI even when its source builds and tests pass. Use the project's
reviewed SDK/container build and retain its compatibility gates. Runtime
selection alone cannot make newer host libraries compatible with older targets.
The optional [Antistatic integration](../integrations/antistatic/README.md)
illustrates a project that uses a pinned release SDK. Its source and packaging
commands remain project-owned.

For managed Debian coding VMs that need container builds, add `--container-tools`
to the saved setup. It expands to `podman`, `uidmap`, `passt`, `slirp4netns`, and
`fuse-overlayfs` through the existing `--apt-install` package path. `passt`
provides the `pasta` helper used by Podman's default rootless network mode;
`slirp4netns` remains available when explicitly selected with
`--network slirp4netns`. Verify subordinate UID/GID mappings and run `podman info`
as the login user. Rootless Podman does not require a Docker daemon or adding
the coding account to a privileged Docker group. The images and build cache
consume several GB; include their storage in VM capacity planning.

### Container storage and SDK headroom

Read-only container diagnostics are opt-in and work with existing tools on
Debian and CachyOS; they do not install or restart an engine:

```bash
basaltw agent containers inspect --engine podman --json
basaltw agent doctor --capability host --container-storage --json
basaltw agent support-bundle --container-storage
# Example estimate: 4 GiB uncompressed image, one additional layer copy,
# plus the default 1 GiB reserve. Substitute the workload's measured size.
basaltw agent containers preflight --engine podman --image-bytes 4294967296 \
  --additional-copies 1 --json
```

Use the VM host doctor on Debian; on CachyOS use its local doctor plus
`agent containers inspect` and `agent resources`. Each engine probe has a
10-second deadline and 256 KiB output cap. Results distinguish missing,
failed, timed-out and partial observations. Podman uses the local account's
store; Docker uses an existing local rootless socket when present, otherwise
`/var/run/docker.sock`, without saved contexts or credential helpers. Remote
endpoint/context selections are refused. No sudo, registry login, container
start or deletion is performed. Engine-owned local bookkeeping may occur.

The local result includes the graph-root path, its filesystem's available
space and whether it shares the root/home filesystem, aggregate image and
container counts, running containers, size totals, and an engine-reported
reclaimable estimate. Separate build-cache totals remain `null` when the engine
does not expose them. Shareable support/readiness records omit graph-root and
mount paths, registry URLs, image/container names, labels, and repository paths.
Detailed engine output and errors are discarded.

Preflight requires an explicit estimated **uncompressed** new image size.
Required space is `image_bytes * (1 + additional_copies) + reserve_bytes`.
Adjust `--additional-copies` (0–8) and `--reserve-bytes` for the SDK's observed
behavior. `sufficient_estimate` is not a guarantee: rootless ID-mapped copies,
temporary build products, compression, shared layers and concurrent work can
change demand. Missing graph-root capacity returns `unknown`; insufficient or
unknown preflight exits nonzero. Free root space cannot supply a graph root on
a separate home mount. Reclaimable estimates are never counted as free space;
[Podman documents](https://docs.podman.io/en/latest/markdown/podman-system-df.1.html)
that shared layers can overstate recoverable space.

For cleanup, first prepare a local owner-scoped proposal: inspect
`podman system df -v`, `podman ps --all`, and the exact images produced by the
completed task. Record IDs, ownership, all container/child-image references,
and estimated space; preserve SDK caches needed by projects, running and
stopped workloads, and other owners' artifacts. These listings contain private
names and stay local. Podman image removal has no dry-run: the read-only
inventory and proposed exact-ID command are the dry-run evidence. After
cleanup is explicitly authorized, `podman image rm --no-prune EXACT_TASK_IMAGE_ID`
preserves dangling parents and refuses referenced images without `--force`.
See [Podman's removal contract](https://docs.podman.io/en/latest/markdown/podman-rmi.1.html).
Never replace this review with automatic system/image/volume pruning.

For repeated SDK work, provision a larger home or dedicated local data disk
before adding CPUs to address a space failure. A new VM can declare
`--storage home-data POOL SIZE --storage-mount home-data /home ext4 empty`.
An existing managed VM can add a blank named disk at a new empty mount using
the [named-disk workflow](../reference/command-line.md#proxmox-provisioning-flags), subject
to its existing storage checks. Mounting new storage does not migrate containers.

For a deliberately configured rootless store, review the existing
`~/.config/containers/storage.conf` and select an absolute `graphroot` on the
larger local filesystem, preserving its storage driver/options, ownership,
UID mappings, and any SELinux labels. An administrator can instead set
`rootless_storage_path` for new stores; see the
[storage configuration](https://github.com/containers/storage/blob/main/docs/containers-storage.conf.5.md).
An existing store migration requires a separate stopped-workload, backup,
copy/restore and verification plan; setup reruns never relocate it. Account
for separately configured image stores and copy-temporary directories too.
The generic preflight covers graph-root space only, not every build mount.

`--debug-tools` similarly adds GDB, strace, Valgrind, ccache, and Ninja for
native diagnosis and builds. These opt-in Debian bundles are saved and sent to
the target as explicit APT packages. They do not alter sudo authority or
container/kernel policy. Remove unwanted packages from the saved selection;
removing a bundle selection does not uninstall packages.

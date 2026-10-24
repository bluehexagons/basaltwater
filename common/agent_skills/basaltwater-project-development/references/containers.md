# Rootless test container lifecycle

Treat rootless testing containers and images as disposable working assets.
Keep project data, test inputs and useful results in source control or explicit
project artifacts outside the container so removing a container cannot lose
them. Use the project's native build and test commands where a container adds
no needed isolation or compatibility.

## Containers

Prefer a compatible image already present in the local Podman store. Give each
task its own uniquely named container; use `podman run --rm` for short-lived
work and `podman build --force-rm` to remove intermediate containers on build
failure. Interrupted or killed builds can still leave working containers.
Remove only containers created by the task, including its own stopped
Podman or Buildah build containers, after confirming they are not active. Do not
force-remove, stop or otherwise interfere with another task's active work.

## Images

For repeated test environments, use a reproducible `Containerfile` and key the
image from its relevant inputs, such as the Containerfile, dependency lockfiles,
base image digest and tool versions. A practical local naming convention is
`localhost/basaltwater/<purpose>:<key>`, where `<key>` is a short content-derived
version. Keep shared images immutable: build a new key when inputs change, and
create separate task containers from the shared image. Use native Podman; this
convention does not require or imply a shared image registry.

Create or rebuild an image lazily when a compatible keyed image is absent.
After a task, remove its one-off image when it is known to be unshared; prefer
retaining an image used by other tasks. Avoid indefinite pins by default. Keep
shared images under bounded retention and remove only images with no container
or build references and no explicit keep label. A useful default is to age out
unused images after seven days, shortened to 24 hours under storage pressure.
Container engines report creation age rather than reliable last-use time, so
retention cannot distinguish a recently used old image from an unused one.

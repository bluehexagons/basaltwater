# Coordinate workstation measurements

## Reserve shared resources

Cooperating Linux agents running as the same account can reserve GPU profiling
and shared build output roots around a foreground command:

```bash
basaltw agent reservations run --resource gpu:all \
  --build-output ~/repos/my-game/build --owner renderer-agent --run-id baseline-01 \
  --timeout 1800 -- ./scripts/measure-renderer
basaltw agent reservations status --resource gpu:all \
  --build-output ~/repos/my-game/build --json
```

Each run records an owner, run ID (generated when omitted), supervisor PID and
UTC start time in private account state. All requested locks are acquired
without waiting before execution; a conflict reports the current owner/run and
releases any partial acquisition. Up to 32 keys are accepted. Commands receive
literal argv, closed stdin, streamed output and a 1–86400 second process-group
deadline (default one hour). Their exit status is preserved; timeout returns
124 and interruption returns 130.

Use one agreed GPU name, such as `gpu:all`, for every participating profiler.
Different GPU names do not conflict. Build keys use canonical absolute output
paths, including symlink resolution. Every writer must reserve the same output
root for the entire build and consumption interval. Parent/child directories
are different keys; choose the common root deliberately. Isolated worktree
outputs remain preferable when the project supports them.

Reservations are advisory and account-scoped, not cross-account or cross-host
enforcement. They cannot stop uncooperative tools, desktop activity or hardware
contention. They require local Linux filesystem `flock` semantics; do not move
the registry to a network share. State lives under the account's fixed
`~/.local/state/basaltwater/reservations`, independent of XDG overrides.

The supervisor closes locks after success, failure, timeout, Ctrl-C or SIGTERM,
cleaning up remaining processes in the command's group before returning.
Lock descriptors are also passed to the child, so a supervisor crash does not
release a reservation while the child retains those descriptors. Commands must
stay in the foreground and retain inherited descriptors; daemonized work,
children that close them, and escaped process groups require project-owned
supervision. Never delete lock files or force-release a running reservation:
that could create a second lock inode for the same resource. Status checks the
kernel lock; an old metadata record alone does not mean a resource is busy.
Completed records remain as last-owner evidence and are replaced on reuse.

## Retain a performance session for a project recipe

Use an existing reviewed recipe in `basaltwater-agent.json`. The recipe still
owns its measurement method, warmup, repetition, renderer assertions and
acceptance thresholds:

```bash
basaltw agent visuals check renderer-benchmark --repository ~/repos/my-game \
  --performance-session --settings ~/benchmark-settings.json --json
# Explicitly request a command-scoped power-profile hold:
basaltw agent visuals check renderer-benchmark --repository ~/repos/my-game \
  --power-profile performance --timeout 1800 --json
```

`--power-profile performance|power-saver` implies `--performance-session`.
Without either flag, checks retain their existing behavior and do not collect
these observations. The flags apply to `agent visuals check`; native project
commands and declared recipe argv remain usable without Basaltwater. For
durable native development, invoke this CLI through the platform's ordinary
command supervisor. Resource reservations can wrap the entire check using the
previous section's command.

`check.json` links a private `performance.json` with snapshots before launch and
after completion, failure, timeout or interruption. A separate
`performance-active.json` records the snapshot immediately before the recipe
inside the optional hold. The original recipe argv and wrapper argv are retained
separately. Existing settings, revision, environment, log and receipt contracts
continue to apply; missing active-session evidence fails a successful check.

Snapshots include CPU cpufreq policy driver, governor, energy performance
preference and min/max/current frequency (kernel kHz strings); hwmon thermal
readings in Celsius; available DRM clock-state tables and busy percentages;
and read-only NVIDIA GPU graphics/memory MHz, temperature, utilization and watts
when `nvidia-smi` exists. Compute-process PID/memory and up to 20 `ps` process
names/PIDs/CPU/memory percentages give context for competing work. `ps` CPU
percentages are lifetime averages, not utilization over the recipe interval.
No command lines or full environments are collected. Private process names
and device evidence still need review before sharing.

Unavailable files, unsupported sensors and missing/failed/timed-out tools stay
nullable with observation issues or incomplete inventory flags. Each external
probe has a three-second deadline and 16 KiB output cap. Sysfs observations are
bounded to 128 CPU policies, 32 monitors with 32 thermal sensors each, 32 DRM
cards and 512 characters per value. NVIDIA observations retain up to 32 GPUs
and 128 compute-process records. These are point-in-time observations, not
continuous telemetry, peak temperatures, proof of quiescence or a performance
acceptance result. They can miss transient contention and load-dependent clocks.
Collection overhead is part of wrapper duration; measure the intended workload
inside the project recipe rather than treating wrapper elapsed time as a score.

The optional hold uses the existing
[Power Profiles daemon](https://gitlab.freedesktop.org/upower/power-profiles-daemon)
`powerprofilesctl launch` interface. It does not issue a persistent `set`, change
governors, lock GPU clocks or install/start a power service. Missing tooling,
unavailable profiles, denied holds, or an unobserved requested profile fail the
check before starting its recipe. The daemon owns hold release when the launch
client exits or disconnects; timeout and cancellation clean up the supervised
process group. Other holds, thermal policy and user profile changes can affect
the effective policy during a run; retain observations and compare like-for-like
sessions. Projects needing continuous thermal/load qualification must collect it
in their own harness.

## Bound optimization experiments

Before tuning, name the measured bottleneck and representative workload, keep a
baseline and correctness/quality gates, set a minimum useful improvement above
measurement noise, and choose a finite variant count and time budget. Stop at
those bounds or when remaining gains are below the threshold. A new batch needs
new bottleneck evidence or a materially different hypothesis. Keep project
rendering tolerances and acceptance decisions in the project. See the shared
[development skill](../../common/agent_skills/basaltwater-project-development/SKILL.md#bound-optimization-work).

## Qualify on the target workstation

Offline tests mock hardware and service boundaries. A later native pass should
check conflicting GPU/output reservations across two agent processes and reuse
after success, failure, timeout and termination; compare EGL software flags with
the actual driver/renderer using [graphics diagnostics](graphics-diagnostics.md);
and verify available profile holds, active observations and release after each
exit path. Include a task-owned helper that redirects output to check descendant
cleanup. Preserve other owners' processes and profile changes during these tests.
Use project-owned fixtures and thresholds for timing and
[linear PFM comparisons](visual-comparisons.md#compare-linear-hdr-pfm-captures).
Passing mocked checks does not establish native CachyOS or hardware acceptance.

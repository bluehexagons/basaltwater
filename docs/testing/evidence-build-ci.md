# Shared builder and automated results

[Documentation index](evidence.md)

## Shared Debian build and CI host

**2026-11-03 UTC; installed `550e8f7f`, including nested scheduling at
`76eb0d08`, on an owned disposable Debian 13 x86-64 QEMU guest.** Twelve critical
installed build, runner and webhook runtime files matched source. A private test
repository supplied real Actions jobs; its restricted build key granted only
the fixture projects, with no controller administrator or target credentials.

| Check level | Observed result | Qualification boundary |
| --- | --- | --- |
| **Installed setup and conversion** | Normal dedicated-runner setup registered through the controller's GitHub CLI. Conversion to `server_lite --build-server --github-runner` preserved the same working GitHub runner identity. Normal setup then added signed webhook intake and its admission worker | One private repository and an allowed human actor; organization/group, multi-repository registered execution and panel enrollment were not tested |
| **Real Actions and nested builds** | Allowed manual dispatch ran as the dedicated service account. A rerun was refused with zero workflow steps, including its `always()` canary. A synchronous same-host managed build returned verified artifacts in 5.91 seconds. With an older unrelated SSH request queued, the exact nested request ran inside its parent's slot; the older request started only after parent cleanup | Actual runner ancestry and restricted SSH transport, without workflow environment bypasses. The follow-ups below add selected process-loss and real push/PR checks; additional identity denial cases and other crash/acknowledgement timings remain open |
| **Real cancellation** | Normal GitHub cancellation of a running slow nested build produced a cancelled child, empty scratch and an empty capacity marker after cleanup | The initial 45-second observation window expired while awaiting GitHub cancellation delivery; eventual completion was confirmed against the original job and request. This does not promise a 45-second cancellation bound |
| **Three-role capacity and maintenance** | During a real Actions hold, an ordinary SSH request and a signed HTTPS webhook remained queued. Busy maintenance refused and retained its drain; another assigned Actions job was denied before any step. Explicit idle maintenance released its own drain, then the SSH and pinned public-Git/Node webhook builds succeeded without overlapping execution | HTTPS verified the private lab certificate and hostname. Bad signatures returned 403; signed and duplicate deliveries returned 202 with one retained receipt and one core job. Public DNS, ACME, Tunnel and a GitHub-configured webhook sender were not exercised |
| **Saved rerun and reboot** | Setup with all role flags omitted left all four role units active and boot-enabled, both runner credential files unchanged, and no jobs, scratch or drain active. Actual guest reboot preserved those service checks, runner UID and coordinator inode; a fresh nested workflow, ordinary SSH build and webhook signature/duplicate probes passed afterward | This pass checked service state, not the saved role fields. The later fresh-host pass exposed those fields being erased on an omitted-role setup rerun. Selected configuration only; no host reboot, power loss, storage exhaustion or interrupted installation qualification |

The initial fresh provider-provisioning attempt failed during cloud-init and
removed only its partially created guest. Qualification instead used a separately
owned full clone of the retained Debian template. Fresh provider bootstrap was
not qualified by that batch; the [fresh-host follow-up](#fresh-provider-bootstrap-and-shared-host)
below records a separate installation. Early private workflow preparation failures were
inspected and corrected before new dispatches; their original failed jobs and
cancellation evidence were retained. Intermittent pre-response controller SSH
failures were inspected through later read-only queries using the original IDs;
no uncertain mutation was replayed and no server quota was relaxed.

Cleanup retained terminal jobs, receipts, credentials and private evidence with
the owned guest stopped and autostart disabled. Existing guests and unrelated
fixtures were preserved. These selected native checks qualify the shared
build/test host; webhook-triggered deployment, GitHub status checks and release
publishing remain outside this pass. Further crash, storage and power-loss cases
remain open; this is not production or exact-release qualification.

### Shared-host crash and scratch follow-up

**2026-11-03 UTC; same installed `550e8f7f` runtime on the retained owned guest.**
All twelve critical runtime hashes still matched, as did the two runner
credential files. Real Actions jobs supplied the active nested builds; neither
process-loss test substituted a simulated runner or changed the installed code.

| Check level | Observed result | Qualification boundary |
| --- | --- | --- |
| **Guardian SIGKILL** | Killing the exact job guardian cancelled its active nested build and emptied scratch, while retaining the durable capacity marker. A later SSH request remained queued. Maintenance refused the marker; only after stopping intake, verifying the original worker/guardian were gone and confirming empty execution/role cgroups did explicit root repair clear the existing lock inode. The queued request then succeeded | An inspected repair on the owned fixture, not automatic recovery. The original cancelled job, source, logs and lease evidence were retained |
| **Managed-worker SIGKILL** | Killing the exact root worker left its detached execution service alive and its durable job claim running. The guardian failed the parent Actions job and retained capacity; a later request stayed queued. Explicit recovery stopped and verified the execution cgroup, archived the prior claim and scratch identity, released the verified original scratch mount, and used `WorkerLease.recover_interrupted()` to mark the original request `uncertain` without an artifact. The queued request then succeeded | The uncertain request was never replayed or reported successful. Root repair required empty producer/execution cgroups and exclusive ownership of the original coordinator inode |
| **Bounded scratch exhaustion** | An authorized build filled only its own 256 MiB ext4 scratch filesystem. The real command logged `ENOSPC` and exited 42; the job became failed without an artifact, scratch was removed and the capacity marker was empty | Per-build scratch exhaustion, without filling the guest/host filesystem or exhausting retained-store bytes, records or inodes. No disk stress or endurance qualification |
| **Post-fault health** | A new real nested workflow succeeded and retrieved verified artifacts. Verified-HTTPS invalid/signed/duplicate webhook probes again returned 403/202/202 with one retained receipt. All four role units were active and enabled; credentials, runner UID, coordinator inode and runtime hashes were preserved, with no active jobs, scratch or drain | The terminal uncertain history remained retained. No public ingress, power loss, additional crash/cleanup-acknowledgement timing or fresh provider-bootstrap qualification |

The private harness initially had a quoting error before fault injection;
after correction it continued against the same original workflow. Intermittent
SSH response loss/refusal interrupted final read-only observations in both
crash cases. Later queries reconciled the original request IDs and confirmed
their outcomes; no fault or mutation was repeated. No server quota was relaxed.
The owned guest was then stopped with autostart disabled, retaining the private
fixture and evidence. No product defect was reproduced in this follow-up.

### Fresh provider bootstrap and shared host

**2026-11-03 UTC; bootstrap source at `a070bc49`, saved-role fix and follow-up
at `08c41ccb`, fresh owned Debian 13 x86-64 QEMU guest from the pinned cloud
image.** The twelve critical shared-host runtime files matched source; the
follow-up also checked the controller entry point and new persistence helper.
The earlier template-clone checks remain separate from this normal public CLI
provisioning and installation.

| Check level | Observed result | Qualification boundary |
| --- | --- | --- |
| **Bootstrap diagnosis and fresh setup** | Retained cloud-init diagnostics identified the original failure: creating a new `operator` account collided with Debian's existing system group. The partial guest was removed. Detailed cloud-init status is now requested before cleanup, and fresh examples use an available private-group name. Normal CLI provisioning then completed cloud-init, authenticated host-key enrollment and combined builder/runner setup with a new non-root account; GitHub reported the new registration online/idle. Normal setup added signed webhook intake | Existing system groups were not adopted and initialization checks were not bypassed. One pinned Debian image, repository-scoped registration and private lab TLS; no public DNS/ACME/Tunnel or provider-configured webhook sender |
| **Fresh-host workloads** | A real manual-dispatch workflow completed a same-host nested build and verified its artifact. An ordinary authenticated SSH build and a signed webhook build also returned verified artifacts. Invalid/signed/duplicate HTTPS deliveries returned 403/202/202 and retained one webhook core request | Restricted workflow build authority, without controller administrator or target credentials. No deployment, status-check or release publishing |
| **Saved configuration and reboot** | An ordinary omitted-role setup initially exposed a defect: services survived while saved role flags and policies were erased. The fix preserves omitted public role settings before normal validation. Complete-role repair followed by another omitted-role setup passed 14 setting checks in both controller cache and target saved setup. Actual guest reboot preserved those settings, two credential hashes, 14 source-file hashes, four enabled/active role units, account identities and coordinator inode | Corrects the earlier service-only persistence claim. Cloud-init and the guest agent remained healthy; no queued/running jobs, scratch or drain remained after setup and reboot. One same-profile configuration; broader refresh and interrupted-installation cases remain separate |
| **Work after reboot** | A new real nested Actions workflow and a new ordinary SSH build returned verified artifacts. HTTPS probes again returned 403/202/202, retaining the original webhook receipt/core request; its previously built artifact downloaded and verified after reboot. Final inspection found all five managed jobs successful, unchanged saved settings/credentials/runtime/identities, and empty scratch/capacity with no drain | The webhook check verifies persistent deduplication and artifact retrieval, rather than a new webhook build. Public ingress and broader event/fault cases remain unqualified |

The first workload harness stopped when a later SSH connection was refused
before the webhook probe started. Authenticated guest-agent inspection found the
matching kernel `UFW LIMIT BLOCK` entry; SSH remained active and Fail2ban had no
failures or bans. Paced observations completed the remaining probe without
changing firewall or quota policy or replaying the completed workflow/build.
This establishes the cause of that refusal, not every earlier SSH interruption.
The [build guide](../services/build-systems.md) now declares the controller's
trusted SSH source and explains the connection-per-operation polling boundary.

The owned fresh guest was stopped with autostart disabled, retaining its
registration, fixture and private evidence. Its webhook inbox/preparation queue
was empty with one retained receipt. The older qualification guest remained
stopped; other guests kept their states and running process identities, with no
active hypervisor tasks or guest locks.

### Real GitHub event follow-up

**2026-11-03 UTC; unchanged installed `08c41ccb` runtime on the retained fresh
Debian 13 QEMU guest.** Sixteen shared-host and identity-gate source files
matched the controller. A branch-restricted workflow in the private fixture
repository used real GitHub deliveries, without editing the installed hook,
policy or credentials.

| Check level | Observed result | Qualification boundary |
| --- | --- | --- |
| **Real push and PR updates** | An allowed human's actual Git push and same-repository PR opening and synchronization each ran workflow steps, completed a nested managed build and verified its artifact. Step canaries and local gate outcomes matched the original GitHub run IDs and event actions | One private repository and the same allowed human as actor, sender and PR author. No fork, bot, independent actor/author or organization/group qualification |
| **Denied PR action** | Closing the unmerged fixture PR delivered the unsupported `closed` action. GitHub reported a failed job with zero workflow steps, the event-step canary was absent, and local telemetry recorded `pr-action-unsupported` | Expected denial, including prevention of the workflow's `always()` step. This does not qualify every denied event or identity combination |
| **Reopening and host preservation** | Reopening the same PR ran a fourth allowed job, completed another nested build and verified its artifact. Final inspection correlated all five event runs: four successful builds/canaries and one denied job without a canary. All nine retained managed jobs were successful; source hashes, two runner credentials, public setup settings, four active/enabled role units, account identities and the coordinator inode were unchanged, with empty scratch/capacity/intake and no drain | Existing registration was reused. No policy relaxation, new registration, injected event file or workflow-controlled hook bypass. Local telemetry and step markers are operational evidence, not independent audit records |

No product defect was reproduced. Cleanup disabled the event-only fixture
workflow, closed the fixture PR without merging, and stopped the owned guest
with autostart disabled. Registration, fixture branches and original run evidence
were retained. Other guest states and process identities were preserved, with
no active hypervisor tasks or guest locks. This follow-up changes documentation;
the installed implementation remains the previously validated revision.

## Automated validation

The November 1 documentation and privacy cleanup passed 33 focused agent and
documentation tests and 868 tests across affected Python modules. Native
Windows results belong to the separate [Windows qualification](evidence-workstations.md#native-windows-workstation-and-tasks).

| Date / source | Observed result | Qualification boundary |
| --- | --- | --- |
| 2026-10-31 / Proxmox fixes at `a4400a78` | Full `make -j2 check-ci`: 9,243 tests, six skips, 82% branch-aware aggregate coverage; static/docs/brand/metadata/wheel checks passed. [Hosted CI](https://github.com/bluehexagons/basaltwater/actions/runs/41253522451) passed full Debian/Node 24 and Node 26 compatibility jobs | Historical source revision; mocked system calls and fixture coverage do not establish live behavior of all profiles/options |
| 2026-10-31 / Shares & Data UI at `bca3b72e` | Full `check-ci`: 9,190 tests, six skips, 82% aggregate coverage; all six local Chromium/Firefox Shares cases passed | Separate historical revision and browser run, not an installed-system refresh |
| 2026-11-01 / Unicode fix `986d3061`, timer fix `7ba41724` | Each fix passed `make check-fast SUITE=web`: 1,289 tests run, one skipped, plus compilation, docs and package metadata. All eight focused local Chromium/Firefox Shares cases passed at `986d3061`; the new Unicode regression failed in both engines before the fix | Focused checks, not a new full CI or release-candidate pass. Local browser services use simulated host actions; installed-service results are recorded separately above |
| 2026-11-02 / specimen repair `785c3ffd` | CI had stopped before the full suite because the generated Shares & Data specimen lacked the existing Unicode search attributes. Regeneration fixed the two stale inputs. Full local `make -j2 check-ci` passed 9,196 tests, six skips and 82% branch-aware coverage, plus static/docs/brand/metadata/wheel checks. [Hosted CI](https://github.com/bluehexagons/basaltwater/actions/runs/41478410457) passed full Debian/Node 24 validation (9,196 tests, three skips, 82%) and Node 26 compatibility | Generated specimen repair; no application behavior changed. Automated checks do not establish browser, hardware or exact-candidate release qualification |
| 2026-11-03 / shared-host implementation at `550e8f7f` | Full local `make check-ci` passed 9,316 tests, six skips and 82% branch-aware aggregate coverage, plus static/docs/brand/metadata/wheel checks. [Hosted CI](https://github.com/bluehexagons/basaltwater/actions/runs/41551778724) also passed | Includes exact-request lease delegation, FIFO identity and cancellation regressions. Native results are the separate shared-host batch above; remaining fault and release cases are not inferred from CI |
| 2026-11-03 / bootstrap diagnostics at `a070bc49` | Full local `make check-ci` passed 9,317 tests, six skips and 82% branch-aware aggregate coverage, plus static/docs/brand/metadata/wheel checks. [Hosted CI](https://github.com/bluehexagons/basaltwater/actions/runs/41618849094) passed | Includes detailed cloud-init failure propagation. The initial system-Python invocation stopped before the suite because its wheel-build dependency was absent; the complete development-environment run passed. Native fresh-host coverage is recorded separately above |
| 2026-11-03 / saved shared-host roles at `08c41ccb` | Full local `make check-ci` passed 9,329 tests, six skips and 82% branch-aware aggregate coverage, plus static/docs/brand/metadata/wheel checks. [Hosted CI](https://github.com/bluehexagons/basaltwater/actions/runs/41622030528) passed. The focused cache/setup/runner selection passed 182 tests | Twelve new regressions cover saved public policy, remote serialization, explicit overrides/disables, restricted resource updates, scope changes and credential exclusion. Native rerun/reboot observations are recorded separately above |

Failures remain in private evidence alongside successful continuations. The
Proxmox suite initially exposed two corrected test-fixture issues; the Shares
UI run initially exposed a corrected navigation wait in its browser test.
Successful reruns do not erase those records. Later documentation/privacy checks
listed in the [overview](README.md) have their own scope and do not repeat these
live or full-suite runs.

# Shares, data, and filesystems

[Documentation index](evidence.md)

## Shares and Data

| Date / source | Check level and observed result | Qualification boundary |
| --- | --- | --- |
| 2026-10-31 / installed backend `e7f8e43f` | **Installed workflow:** authenticated HTTPS forms, CSRF, independent approval and actual root workers exercised Samba shares/accounts, password reset and enable/disable/removal, backup mirror controls and persistence, PAR2 scans/findings and targeted verify/restore/accept. SMB access and retained Unix identities/files were checked; targeted repair also has real PAR2 component evidence | Selected service qualification on a non-root Debian operator guest, not every setup profile or installed browser interaction. Cleanup retained accounts, files/parity and broker evidence, restored saved configuration and stopped the fixture |
| 2026-10-31 / follow-ups through `bca3b72e` | **Local regression/browser:** later per-task selection, filters, pagination, draft retention and canonical setup-recovery guards passed relevant regressions and six Chromium/Firefox cases, including keyboard, no JavaScript, narrow layout, text zoom and themes | Browser services/approvals were simulated; installed HTTP qualification followed on 2026-11-01 below |
| 2026-11-01 / clean installed `5184cef0` | **Installed workflow:** fresh disposable Debian guest, non-root operator, independently authenticated approvals and real root workers. Denial/cancellation and one-use submission, Samba account/share lifecycle, encrypted SMB read/write and access rejection, mirror create/edit/pause/resume/run/remove, literal paths and deletion, PAR2 scans and approved verify/restore passed. Actual HTTPS catalog checks covered 22 tasks, 26 findings, pagination, exact case-sensitive root/parity selection, independent filter/clear links, Unicode queries, unavailable selection and stale copyable drafts | Normal custom-step service setup, not a full-profile installation. Initial missing firewall prerequisites and harness failures were retained and inspected before continuation. Fresh missing-file repair exposed absent identity metadata; fixed below. Acceptance was not repeated in this run |
| 2026-11-01 / fix installed `ed2fa820` | **Installed recovery:** normal source deployment retained saved declarations, pause controls and fixture bytes; all 579 runtime files matched the controller. A fresh missing finding recorded explicit absent identity and separately approved repair reached real PAR2. **Automated:** 608 storage/recovery tests plus compilation, documentation and package checks passed for the fix | PAR2 correctly reported insufficient recovery data for an entirely missing file protected by 10% parity. The source remained absent and its finding open. This qualifies the identity fix and insufficient-parity result, not successful targeted repair |
| 2026-11-01 / installed `ed2fa820` | **Installed exclusion/persistence:** a retained synthetic setup guard blocked controls, approved dispatch and root execution; a live canonical setup owner excluded a real worker. Guarded refresh refused before source activation. Approved task removal retained parity and showed an unavailable selection. Panel restart and an owned-guest reboot preserved approved account/share settings, 21 paused scrub tasks, identities/groups, TLS/auth and exact data/parity bytes; catalog pagination/selection and encrypted SMB writes passed after boot. All 30 real requests retained the normal rate/quota policy | Refresh was an installed component probe; this custom-step fixture does not qualify public full-profile refresh. Only the owned synthetic guard was reconciled. Scheduled storage also refused that guard and resumed afterward. The fixture was stopped with evidence, data and a local snapshot retained; existing guests and host configuration were preserved |
| 2026-11-01 / clean installed `974a1652` | **Installed recovery follow-up:** normal current-source setup preserved prior declarations, pause controls and bytes, with all 574 runtime files matching. A separate paused 100%-parity fixture exercised independently approved real workers: missing-file repair reproduced the exact original, an intervening-file race failed safely with `file_changed` while preserving the new file, and deliberate replacement was accepted with old parity/originals retained. A subsequent separately approved full scan reported healthy with no open finding | Adequate recovery data qualifies this successful repair; the earlier 10%-parity failure remains valid. This is selected service setup on the owned guest, not full-profile refresh or interruption/power-loss qualification |
| 2026-11-01 / fix installed `986d3061` | **Installed browser:** Chromium 156 and Firefox 157 exercised real authenticated navigation, retained share/account display, independent filters, task/finding pagination, exact root/parity selection, keyboard disclosure and recovery review/cancel. Wide light/JavaScript and 320px dark/no-JavaScript/200% text passed. Both engines reproduced valid 200-character supplementary Unicode searches truncating to 100; corrected controls retain and submit all 200 while rejecting overlong input. Normal deployment matched all 574 runtime files and retained both fixtures | Isolated profiles trusted only the public guest CA verified through pinned SSH; TLS verification stayed enabled. Browser recovery checks stopped at review/cancel; approval/worker results are the separate real HTTPS flow above. Shared preview trust and native Firefox ESR remain open. Initial rate-limit, navigation-wait and oversized Firefox screenshot failures were harness issues, inspected before paced/viewport continuations |
| 2026-11-01 / fix installed `7ba41724` | **Installed audit scheduling:** ordinary setup exposed an active but elapsed audit timer with no future deadline. Adding a five-minute deadline relative to timer activation restored scheduling; normal installation retained declarations/controls/bytes and matched all 574 runtime files. An actual unattended export published a newer valid snapshot after about five minutes and rearmed another future deadline. Final Firefox checks also passed at this revision | Observation used the normal timer without manual export/restart or schedule overrides. This covers the selected guest exporter, not the Proxmox inventory timer or interrupted setup |

The configured interactive preview failed twice with `ERR_CERT_AUTHORITY_INVALID`;
its tab was closed. The later permitted fallback used the repository's existing
stable test runtime and isolated CA trust; it does not fix shared preview trust
or qualify native ESR. Initial setup, harness and insufficient-parity failures
remain in private evidence; no uncertain request was replayed and no server
rate/quota policy was relaxed. No host reboot, formatting or disk stress was
performed. The selected full-profile follow-up is recorded below;
interruption/power-loss remain separate checks.

Follow-up cleanup used a ninth separately approved request to remove its routine,
retaining all new data/parity/recovery evidence and restoring the prior declarations,
pause controls and fixture bytes. All 39 request records were retained, with no
pending approvals, setup/unit recovery guards or staged passwords. The owned guest
was stopped with private evidence, an online broker backup and an offline local
snapshot retained. Host configuration/boot and other guest configurations/states/PIDs
matched the fresh baseline; a same-node snapshot does not qualify off-host recovery.

### Shares and Data full-profile refresh

**2026-11-02 UTC; fresh disposable Debian 13 guest, selected `agent_vm` profile.**
Normal public setup configured a non-root operator, independently authenticated
HTTPS approval service and real root workers. Provider tools/authentication and
browser automation were explicitly excluded; no provider task or external-account
test ran. These are actual HTTP/CLI checks, without a new interactive browser run.

| Source / check level | Observed result | Qualification boundary |
| --- | --- | --- |
| Initial `13b7cf3f`, fix installed `a94119e7` | Full setup reproduced a lost `--no-browser-automation` flag during controller-to-target serialization. The fix retains the explicit opt-out in remote arguments and saved target state; normal full-profile installation omits browser reconciliation | The initial `server_lite` dry run correctly refused the approval service before mutation; the supported `agent_vm` profile was used. Initial first-observation security-retention gaps and missing test-client preparation were inspected before continuation |
| Installed `a94119e7` | Two separately approved real workers changed a writable share to read-only and created a paused daily mirror. Encrypted SMB writes worked before the change; reads passed and writes were denied afterward. Approved target declarations were explicitly reconciled into the separate controller cache before setup reruns | Disposable local data; no hard-mounted CIFS, other client OS, provider login or public TLS/DNS qualification |
| Fix installed `0ca6ddff` | Public refresh at `a94119e7` refused saved shares because plaintext passwords are intentionally absent. The fix requires matching existing Unix/Samba accounts and preserves their passwords/login flags. Actual queued refresh completed through its independent root worker. All 573 runtime files matched source; approved declarations, pause controls, identities, credentials/PKI and exact bytes survived | Fresh setup still requires credentials; automated refusal checks reject missing accounts before source changes. The original failed dry runs are retained. This qualifies the selected profile/configuration, not all profiles or interrupted source activation |
| Installed `0ca6ddff` | A populated public controller rerun and an owned-guest reboot preserved approved read-only access, empty paused mirror destination, user IDs/groups, password hashes/login flags, panel/approval credentials, certificates and file hashes. Verified HTTPS and encrypted SMB checks passed after each phase | Ordinary setup can update Samba password-set timestamps when replaying controller credentials; refresh and reboot retained the complete account listing. Same-node retention is not off-host recovery |
| Installed `0ca6ddff` | After reboot, a normal unattended audit run published a newer valid storage snapshot and retained a future deadline, verified through native D-Bus. Cleanup retained both terminal broker records, worker logs, an integrity-checked online broker backup and an offline snapshot, then left the owned guest stopped with autostart disabled | No manual export or schedule override during observation. Other guest configurations/states/PIDs and host boot/setup/storage/mounts matched baseline; no active hypervisor tasks, guest locks, failed host units or owned capture remnants remained |

Automated validation for `a94119e7` passed 2,120 agent tests, 185 additional
configuration/refresh tests, compilation, documentation and metadata checks.
For `0ca6ddff`, the core/storage selection passed 1,896 tests with one skip and
the same static checks. Focused regressions reproduced both defects before their
fixes. These checks do not constitute a new full CI or release-candidate pass.

Use [Continue testing on Proxmox](continuing.md) for subsequent bounded batches.
The Proxmox root panel is not the non-root guest approval environment required
by [Shares & Data](../services/shares-data.md).

### Storage triage and native filesystems

**2026-11-08 UTC; fresh owned Debian 13 QEMU guest, normal public `agent_vm`
setup at `2bc879f7`, followed by normal setup with recovery fix `4569eb7f`.**
The non-root operator used independently authenticated main and approval panels.
Source receipts and relevant installed module hashes matched the clean controller.
TLS certificate and hostname verification remained enabled. These are actual
HTTP forms, root workers and native filesystem checks, without a new browser run.

| Check level | Observed result | Qualification boundary |
| --- | --- | --- |
| **Installed filesystem observations** | The sandboxed root exporter and actual panel reported ext4, Btrfs and XFS loop-image mounts, escaped spaces and a read-only bind over a writable filesystem. ext4 retained count and timestamps matched history seeded offline in the disposable image. Btrfs device counters, absent scrub history and a real completed read-only native scrub record rendered correctly; omitted `unverified_errors` remained unavailable | Clean single-device Btrfs, with no hardware fault injection. Seeded ext4 history qualifies the metadata/warning path, not detection of a live I/O failure. Other filesystem types, partial-device records and scheduled native scrubs remain unqualified |
| **Installed triage workflows at `2bc879f7`** | Two-file verify, quarantine and permanent-delete batches passed exact review, independent approval and correlated per-file worker outcomes. Dirty verification retained findings. Incorrect delete confirmation was rejected. Changing one reviewed file caused both disposition items to be skipped before effects; source bytes remained present | One configured task on the guest's ext4 root. Interruption, larger batches and overlapping-task refusal retain automated coverage only; power loss remains unqualified |
| **Installed preservation at `4569eb7f`** | Quarantine retained corrupt bytes privately; deletion removed only its reviewed source bytes. Retained parity still verified known-good copies. Actual supervised mirrors preserved disposed paths and excluded quarantine; a subsequently verified healthy replacement did not release mirror protection. Fast maintenance excluded quarantine and retained disposition history. Older root-only batch records survived newer jobs, and replay of an earlier batch was refused | Guest-local mirrors, without hard-mounted network storage or automatic evidence pruning. Retained records do not make interrupted work resumable |
| **Installed recovery fix at `4569eb7f`** | A failed staged repair originally overwrote the finding's live identity with its candidate identity, incorrectly blocking restoration as `file_changed`. The fix retains the reviewed live identity on both staged failure paths. Normal deployment, another failed native repair and a separately approved restoration recovered the exact original bytes. A varied-byte fixture also passed approved repair and independent verification | Debian PAR2 0.8.1 separately reproduced repair failure on the repeated-byte fixture. Its `repairable` estimate is not a successful reconstruction. Original live bytes and failed workspaces were retained. Eighty-four focused tests, syntax and documentation checks passed; concurrent live edits still block recovery |
| **Installed native disposition components** | Actual PAR2 findings on Btrfs and XFS supported quarantine through the installed helper, preserving the inode, device and corrupt-byte hash even with parity on another filesystem | Direct installed component calls under the storage lock, separate from the ext4 HTTP/approval workflow. This guest kernel refused `xfs_scrub -n` because online metadata scrubbing was unavailable; only XFS observations and quarantine are qualified |
| **Installed reboot persistence** | An owned-guest reboot preserved saved setup, credentials/PKI, operator identity, source, file/parity bytes, dispositions, durable batch records and terminal requests. Packaged services, direct panel-socket HTTP and verified HTTPS/auth checks passed. An unattended exporter published a fresh valid snapshot and rearmed its future timer deadline | Native images were cleanly unmounted and their loop devices detached before reboot; they were not declared persistent mounts. No manual export or timer override occurred during post-boot observation. No host reboot, off-host recovery or interrupted operation was tested |

Initial harness assertions and the original failed requests remain in private
evidence. The security monitor recorded expected initial retention gaps and
fixture module activity; its subsequent ordinary check succeeded. No uncertain
mutation was replayed and no server rate/quota policy was relaxed.

Cleanup retained all nine terminal approval records, four durable batch records,
failed workspaces, findings, parity and an integrity-checked online broker backup.
Intentionally unresolved fixture findings remain recorded. Private evidence was
archived, and the owned guest was stopped with autostart disabled and an offline
snapshot. Existing guest configurations/states/running PIDs and host boot/mounts
matched baseline; no hypervisor tasks, guest locks or failed host units remained.
Same-node retention does not qualify off-host recovery.

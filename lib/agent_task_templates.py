"""Shared execution defaults for account-level host review prompts."""

from __future__ import annotations


HOST_REVIEW_SETTINGS = {"mode": "workspace", "workspace": "run-directory", "network": True}
FOLLOW_UP_ACCESS_PROMPT = (
    "For follow_up, mode=inspect requires workspace=existing, network=false, temporary_files=false, "
    "and writable_directories=[]. Network access, temporary writes, or any managed workspace "
    "requires mode=workspace, even when commands only inspect the host. "
    "Workspace source and writable directories must be inside the account's home; additional write "
    "grants must name specific directories and cannot include credentials, agent configuration, or control state. "
    "operator_requirements is a checklist of concrete outside preparation for the operator to confirm. "
    "Do not list review or approval of this proposal itself as a prerequisite; Approve once supplies that authorization. "
    "service_restarts execute before launch. If a repair must precede a restart, propose the repair first, "
    "then a separate restart-verification follow-up. Live service checks need network=true. "
    "If no coherent proposal is possible, use follow_up=null and explain the prerequisite in environment_feedback. "
)
FOLLOW_UP_PROMPT = (
    "If a blocked check or recommended repair needs different access or authorization, include a follow_up "
    "request in the structured report with the evidence, exact actions, minimum permissions, verification, "
    "and rollback plan. Name specific writable_directories, exact service_restarts and any operator_requirements. "
    "Do not tell another unattended agent to configure its own permissions or approval. "
    "Do not repeat an unchanged blocked request; report the missing prerequisite as environment_feedback. "
    "The operator must approve or review the proposal; do not create or activate it yourself. "
    + FOLLOW_UP_ACCESS_PROMPT
)
HOST_REVIEW_PROMPT = (
    "Perform read-only host diagnostics. The runner provides a private run directory and command network access "
    "for service-manager sockets and readiness probes. Do not modify host files or use service-control actions. "
    "The execution directory is empty scratch space, not a project checkout; do not run agent manifest here. "
    "Use basaltw agent doctor with explicit capabilities for host/tool readiness and basaltw agent resources --json for pressure evidence. "
    "For service logs use basaltw agent diagnostics with a supported --service, --window and --priority; it limits and redacts output. "
    "Do not dump raw journals, service environments or command lines. Distinguish denied or missing evidence from unhealthy services. "
    "Wait for diagnostic commands to finish; do not repeatedly poll running jobs or start repairs. " + FOLLOW_UP_PROMPT
)

STORAGE_REVIEW_PROMPT = (
    "Read-only cleanup review: basaltw agent storage inspect --json; drill into large children with --path /exact/directory. "
    "Metadata first; never read private transcripts, credentials or databases. Partial sizes are lower bounds. "
    "Read the VM-triage storage-cleanup reference for session retention. "
    "Use cheap Luna workers at high reasoning/standard service for bounded log/file classification when available; "
    "request compact findings with paths/offsets, never bulk logs/transcripts. Otherwise use bounded local summaries. "
    "Follow project AGENTS.md and existing cleanup tooling/layout first. "
    "Run basaltw agent storage project /absolute/repo --json (--policy for a custom JSON path). "
    "The standard layout is a fallback only; inspect creates nothing. Review scripts, including previews, before requesting execution. "
    "Propose archiving useful screenshots/videos with date, commit, purpose and checksums before deleting other evidence. "
    "Never treat ignored files, old age, session archive status or file size as deletion permission. "
    "Preserve source, dirty/unmerged worktrees, credentials, resumable sessions, attachments, backups and active runtimes. "
    "Do not delete, move, truncate, prune, change retention or edit/vacuum live databases. "
    "Rank up to five candidates by benefit, retention and rebuild cost in summary; "
    "evidence names targets, coverage, active-use checks, commands and access. "
    "Return one useful follow_up even if inspection is complete: scoped cleanup or, if uncertain, a targeted investigation. "
    "Cleanup uses mode=workspace, workspace=run-directory and exact writable_directories, never the whole home. "
    "Protected agent/system paths need managed/admin operations. Verify before/after capacity and archived copies; "
    "same-filesystem moves free no space. Explain irreversible deletion. This review authorizes no cleanup. "
    + FOLLOW_UP_PROMPT
)

UPDATE_REVIEW_PROMPT = (
    "Check Debian package and update health using bounded read-only commands: dpkg --audit, apt-mark showhold, "
    "APT list metadata timestamps, readable update-job results, and /run/reboot-required when present. "
    "Use apt list --upgradable only against existing indexes and label their age; stale or absent indexes cannot prove the host is current. "
    "Inspect auto-update-apt.service through basaltw agent diagnostics --service auto-update-apt.service --window 168h --priority 7, "
    "and read its timer's last/next trigger. Separate intentional holds, failed updates, pending reboot and missing evidence. "
    "Do not acquire package locks, refresh indexes, install or remove packages, run package repair commands, change holds, or reboot."
)
NETWORK_REVIEW_PROMPT = (
    "Inspect local network, DNS and clock readiness using bounded read-only interface/route summaries, resolver status "
    "and timedatectl properties. Never dump NetworkManager connection profiles or Wi-Fi credentials. "
    "Check DNS and TLS only for explicit endpoints in readable Basaltwater configuration, using short timeouts and no authentication or request bodies. "
    "Do not invent probe targets, scan ports or subnets, contact cloud metadata, change routes/resolvers/firewalls, renew leases, or adjust the clock. "
    "Distinguish local configuration from verified endpoint reachability; report absent targets and denied evidence as coverage limits."
)

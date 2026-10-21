"""Shared execution defaults and actionable outcomes for account-level tasks."""

from __future__ import annotations


HOST_REVIEW_SETTINGS = {"mode": "workspace", "workspace": "run-directory", "network": True}
ACTIONABLE_RESULT_PROMPT = (
    "Put substantive findings, changes, verification and next action in summary; 'review finished' is insufficient. "
    "Perform authorized work. For repairs needing separate access/authorization, propose an executable follow_up. "
    "Its standalone prompt must apply and verify the exact change without inheriting this review's no-change restriction. "
)
FOLLOW_UP_ACCESS_PROMPT = (
    "follow_up inspect requires workspace=existing, network=false, temporary_files=false, writable_directories=[]. "
    "Network, temp writes or managed workspaces require mode=workspace. Source/write grants must be inside the account home; "
    "grant exact directories, excluding credentials, agent configuration and control state. "
    "operator_requirements lists outside preparation, not approval of this proposal: Approve once authorizes it. "
    "service_restarts execute before launch; propose repairs before dependent restarts in separate tasks. "
    "Live service checks need network=true. If no valid proposal exists use follow_up=null and explain in environment_feedback. "
)
FOLLOW_UP_PROMPT = (
    "For blocked work/repairs needing different access or authorization, include a structured follow_up with evidence, "
    "exact actions, minimum permissions, verification and rollback. Name writable_directories, service_restarts and "
    "operator_requirements. Never tell an unattended agent to grant itself permissions/approval. "
    "Do not repeat unchanged blocked proposals; report the prerequisite in environment_feedback. "
    "Proposals need operator approval; never activate them yourself. "
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
    "Goal: reclaim storage with an approved cleanup follow_up. "
    "First pass read-only: basaltw agent storage inspect --json --summary; focus --path without --summary on large children. "
    "Metadata only; no private transcript, credential or database reads. Partial sizes are lower bounds. "
    "Follow project instructions, native cleanup rules and VM-triage storage-cleanup guidance. "
    "Inspect rules: basaltw agent storage project /absolute/repo --json. Review scripts before running even previews. "
    "For a generated rule, basaltw agent storage clean-generated /absolute/repo --rule NAME previews exact targets; "
    "approved removal uses --apply and target/parent write grants. "
    "Resolve exact-child classification now where possible. Preserve mixed build/evidence roots when ambiguous; "
    "choose a smaller proven disposable cache or generated target instead. "
    "Preserve source, Git data, dirty/unmerged worktrees, sessions, attachments, backups and active runtimes. "
    "Age, size, ignore rules and archiving alone never authorize deletion. No removal or retention changes in this pass. "
    "In summary rank up to five exact candidates by bytes, retention, rebuild cost and coverage; "
    "name the selected cleanup and benefit. Include measurements, not just a review-complete statement. "
    "Prefer one cleanup follow_up that rechecks ownership, active use and retention, applies exact reviewed commands, "
    "and reports removed targets, failures and filesystem available bytes before/after. Approval authorizes that cleanup; "
    "do not write another review-only prompt or inherit this pass's no-removal restriction. "
    "Use mode=workspace, workspace=run-directory, exact writable_directories, never the whole home. "
    "If no safe target exists, name the exact missing retention decision in summary; a targeted investigation must "
    "end with an apply-ready proposal or a specific operator decision, not another open-ended inventory. "
    "Protected paths need managed/admin work. Verify archives/checksums/capacity; same-disk moves free no space. "
    "Deletion is irreversible; this pass authorizes none. "
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

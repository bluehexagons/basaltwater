"""Shared execution defaults for account-level host review prompts."""

from __future__ import annotations


HOST_REVIEW_SETTINGS = {"mode": "workspace", "workspace": "run-directory", "network": True}
FOLLOW_UP_PROMPT = (
    "If a blocked check or recommended repair needs different access or authorization, include a follow_up "
    "request in the structured report with the evidence, exact actions, minimum permissions, verification, "
    "and rollback plan. The operator must review and submit that separate task; do not create or activate it yourself. "
)
HOST_REVIEW_PROMPT = (
    "Perform read-only host diagnostics. The runner provides a private run directory and command network access "
    "for service-manager sockets and readiness probes. Do not modify host files or use service-control actions. "
    "The execution directory is empty scratch space, not a project checkout; do not run agent manifest here. "
    "Use agent doctor with explicit capabilities for host/tool readiness. " + FOLLOW_UP_PROMPT
)

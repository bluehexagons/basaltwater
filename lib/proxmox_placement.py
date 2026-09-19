"""Placement advisor for Proxmox guests.

Given a resource request (cores, memory, disk) and a collection of node
summaries from registered hosts, rank candidate nodes by fit. The scoring
functions are pure and side-effect free so they can be unit-tested without
SSH. The collection helper iterates the host registry and calls
:func:`lib.proxmox_summary.get_node_summary`, skipping hosts that fail to
respond.

Used by ``basaltw proxmox plan place`` (find a home for a new guest)
and ``proxmox plan rebalance`` (flag overloaded nodes and suggest where
their guests could move).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from lib.proxmox_hosts import ProxmoxHost
from lib.proxmox_summary import (
    NodeSummary,
    ProxmoxSummaryError,
    get_node_summary,
)


_MIB = 1024 ** 2
_GIB = 1024 ** 3

# Thresholds used by rebalance to flag a node as overloaded.
HOT_CPU_FRACTION = 0.80
HOT_MEMORY_FRACTION = 0.85


@dataclass
class PlacementRequest:
    """Resource request for a prospective guest."""

    cores: int = 1
    memory_mib: int = 512
    disk_gib: int = 0
    prefer_tags: list[str] = field(default_factory=list)
    avoid_tags: list[str] = field(default_factory=list)
    exclude_nodes: list[str] = field(default_factory=list)


@dataclass
class NodeSnapshot:
    """One host's registry entry paired with its live :class:`NodeSummary`."""

    host: ProxmoxHost
    summary: NodeSummary


@dataclass
class PlacementCandidate:
    """A scored destination node for a :class:`PlacementRequest`."""

    host_name: str
    node_name: str
    score: float                # 0-100, higher is better; <0 means disqualified
    fits: bool
    reasons: list[str] = field(default_factory=list)
    free_memory_bytes: int = 0
    free_cpu_fraction: float = 0.0
    free_disk_bytes: int = 0


@dataclass
class PlacementPlan:
    """A ranked list of candidates for one :class:`PlacementRequest`."""

    request: PlacementRequest
    candidates: list[PlacementCandidate] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class RebalanceSuggestion:
    """A hot node together with possible destinations for its guests."""

    hot_host: str
    hot_node: str
    cpu_fraction: float
    memory_fraction: float
    guests: list[str] = field(default_factory=list)
    destinations: list[PlacementCandidate] = field(default_factory=list)


def collect_snapshots(
    hosts: list[ProxmoxHost],
) -> tuple[list[NodeSnapshot], list[str]]:
    """Fetch live summaries for ``hosts``. Returns (snapshots, warnings).

    Hosts that fail to respond are dropped from the snapshot list and a
    warning string is appended for each. This keeps the advisor useful
    when one node in the cluster is down.
    """
    snapshots: list[NodeSnapshot] = []
    warnings: list[str] = []
    for host in hosts:
        try:
            summary = get_node_summary(host)
        except ProxmoxSummaryError as exc:
            warnings.append(f"{host.name}: {exc}")
            continue
        snapshots.append(NodeSnapshot(host=host, summary=summary))
    return snapshots, warnings


def score_candidate(
    snapshot: NodeSnapshot,
    request: PlacementRequest,
) -> PlacementCandidate:
    """Score a single node against ``request``. Pure function."""
    host = snapshot.host
    s = snapshot.summary
    requested_mem = request.memory_mib * _MIB
    requested_disk = request.disk_gib * _GIB
    free_mem = max(0, s.memory_total - s.memory_used)
    free_disk = max(0, s.disk_total - s.disk_used)
    free_cpu = max(0.0, 1.0 - s.cpu_usage)

    reasons: list[str] = []
    fits = True

    if host.name in request.exclude_nodes or s.node_name in request.exclude_nodes:
        fits = False
        reasons.append("excluded by caller")
    if request.avoid_tags and any(t in host.tags for t in request.avoid_tags):
        fits = False
        reasons.append(f"has avoided tag ({', '.join(sorted(set(host.tags) & set(request.avoid_tags)))})")
    if request.cores > 0 and s.cpu_count > 0 and request.cores > s.cpu_count:
        fits = False
        reasons.append(f"only {s.cpu_count} cores (need {request.cores})")
    if requested_mem > free_mem:
        fits = False
        reasons.append(
            f"only {_fmt_bytes(free_mem)} free memory (need {_fmt_bytes(requested_mem)})"
        )
    if requested_disk > free_disk:
        fits = False
        reasons.append(
            f"only {_fmt_bytes(free_disk)} free root disk (need {_fmt_bytes(requested_disk)})"
        )

    if not fits:
        return PlacementCandidate(
            host_name=host.name,
            node_name=s.node_name,
            score=-1.0,
            fits=False,
            reasons=reasons,
            free_memory_bytes=free_mem,
            free_cpu_fraction=free_cpu,
            free_disk_bytes=free_disk,
        )

    # Headroom after this guest lands. Memory dominates: it is the hardest
    # constraint and the one operators feel first when a node is full.
    mem_headroom = (free_mem - requested_mem) / max(1, s.memory_total)
    disk_headroom = (free_disk - requested_disk) / max(1, s.disk_total)
    cpu_headroom = free_cpu

    score = (
        40.0 * _clamp01(mem_headroom)
        + 30.0 * _clamp01(cpu_headroom)
        + 20.0 * _clamp01(disk_headroom)
    )

    # Small balancing nudge: prefer nodes already running fewer guests when
    # the harder metrics tie.
    guest_count = s.guests_running + s.guests_stopped
    score += max(0.0, 5.0 - 0.2 * guest_count)

    if request.prefer_tags and any(t in host.tags for t in request.prefer_tags):
        score += 10.0
        matched = sorted(set(host.tags) & set(request.prefer_tags))
        reasons.append(f"matches preferred tag ({', '.join(matched)})")

    reasons.append(
        f"{_fmt_bytes(free_mem - requested_mem)} mem free after, "
        f"{cpu_headroom * 100:.0f}% cpu idle"
    )

    return PlacementCandidate(
        host_name=host.name,
        node_name=s.node_name,
        score=round(score, 2),
        fits=True,
        reasons=reasons,
        free_memory_bytes=free_mem,
        free_cpu_fraction=free_cpu,
        free_disk_bytes=free_disk,
    )


def plan_placement(
    snapshots: list[NodeSnapshot],
    request: PlacementRequest,
) -> PlacementPlan:
    """Rank ``snapshots`` for ``request``. Disqualified nodes are kept at the
    bottom so the operator can see *why* they were rejected."""
    candidates = [score_candidate(snap, request) for snap in snapshots]
    candidates.sort(key=lambda c: (not c.fits, -c.score))
    return PlacementPlan(request=request, candidates=candidates)


def is_hot(summary: NodeSummary) -> bool:
    """Return True when a node is over the rebalance thresholds."""
    if summary.cpu_usage >= HOT_CPU_FRACTION:
        return True
    if summary.memory_total > 0:
        if summary.memory_used / summary.memory_total >= HOT_MEMORY_FRACTION:
            return True
    return False


def plan_rebalance(
    snapshots: list[NodeSnapshot],
    guests_by_host: dict[str, list[str]],
) -> list[RebalanceSuggestion]:
    """For every hot node in ``snapshots``, rank where its guests could move.

    ``guests_by_host`` maps host name to a list of human-readable guest
    descriptors (e.g. ``"101 (vm) web"``). The caller is responsible for
    collecting that — typically via :func:`lib.proxmox_manage.list_containers`.

    Destination scoring uses :func:`score_candidate` with an empty request,
    so it ranks by raw headroom. Operators still pick the actual guest to
    move based on its size.
    """
    suggestions: list[RebalanceSuggestion] = []
    empty = PlacementRequest()
    for snap in snapshots:
        if not is_hot(snap.summary):
            continue
        destinations: list[PlacementCandidate] = []
        for other in snapshots:
            if other.host.name == snap.host.name:
                continue
            destinations.append(score_candidate(other, empty))
        destinations.sort(key=lambda c: (not c.fits, -c.score))
        mem_fraction = (
            snap.summary.memory_used / snap.summary.memory_total
            if snap.summary.memory_total else 0.0
        )
        suggestions.append(
            RebalanceSuggestion(
                hot_host=snap.host.name,
                hot_node=snap.summary.node_name,
                cpu_fraction=snap.summary.cpu_usage,
                memory_fraction=mem_fraction,
                guests=list(guests_by_host.get(snap.host.name, [])),
                destinations=destinations,
            )
        )
    return suggestions


def format_plan(plan: PlacementPlan, *, limit: int = 5) -> str:
    """Human-readable text rendering of :class:`PlacementPlan`."""
    req = plan.request
    lines = [
        f"Placement for {req.cores} cores, {req.memory_mib} MiB"
        + (f", {req.disk_gib} GiB disk" if req.disk_gib else "")
        + (f", prefer {','.join(req.prefer_tags)}" if req.prefer_tags else "")
        + (f", avoid {','.join(req.avoid_tags)}" if req.avoid_tags else ""),
    ]
    if not plan.candidates:
        lines.append("  (no registered hosts available)")
        return "\n".join(lines)
    shown = plan.candidates[:limit]
    for cand in shown:
        marker = "✓" if cand.fits else "✗"
        score = f"{cand.score:5.1f}" if cand.fits else "  n/a"
        lines.append(f"  {marker} {score}  {cand.host_name} ({cand.node_name})")
        for reason in cand.reasons:
            lines.append(f"        {reason}")
    if plan.warnings:
        lines.append("")
        lines.append("Warnings:")
        for w in plan.warnings:
            lines.append(f"  - {w}")
    return "\n".join(lines)


def format_rebalance(suggestions: list[RebalanceSuggestion], *, limit: int = 3) -> str:
    """Human-readable text rendering of rebalance suggestions."""
    if not suggestions:
        return "No nodes are over the rebalance thresholds."
    lines: list[str] = []
    for sug in suggestions:
        lines.append(
            f"Hot: {sug.hot_host} ({sug.hot_node}) — "
            f"CPU {sug.cpu_fraction * 100:.0f}%, memory {sug.memory_fraction * 100:.0f}%"
        )
        if sug.guests:
            lines.append(f"  Running guests ({len(sug.guests)}):")
            for g in sug.guests:
                lines.append(f"    - {g}")
        else:
            lines.append("  (could not enumerate running guests)")
        lines.append("  Best destinations:")
        if not sug.destinations:
            lines.append("    (no other registered hosts)")
        for cand in sug.destinations[:limit]:
            marker = "✓" if cand.fits else "✗"
            score = f"{cand.score:5.1f}" if cand.fits else "  n/a"
            tail = cand.reasons[-1] if cand.reasons else ""
            lines.append(
                f"    {marker} {score}  {cand.host_name} ({cand.node_name})  {tail}"
            )
        lines.append("")
    return "\n".join(lines).rstrip()


def _clamp01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def _fmt_bytes(b: int) -> str:
    if b >= _GIB:
        return f"{b / _GIB:.1f} GiB"
    if b >= _MIB:
        return f"{b / _MIB:.0f} MiB"
    return f"{b / 1024:.0f} KiB"


__all__ = [
    "HOT_CPU_FRACTION",
    "HOT_MEMORY_FRACTION",
    "NodeSnapshot",
    "PlacementCandidate",
    "PlacementPlan",
    "PlacementRequest",
    "RebalanceSuggestion",
    "collect_snapshots",
    "format_plan",
    "format_rebalance",
    "is_hot",
    "plan_placement",
    "plan_rebalance",
    "score_candidate",
]

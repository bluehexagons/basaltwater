"""Offline closed-corpus integrity, match deduplication and review summaries."""

from __future__ import annotations

import hashlib
import json

from desktop import learning_records as contract
from desktop.qa_artifacts import new_bundle
from desktop.record_bundles import _read
from lib.atomic_io import parse_json, write_bytes_atomic, write_json_atomic


def summarize(document, documents):
    """Consume already loaded pinned record graphs; never infer game outcomes."""
    sources = contract.validate_corpus(document)
    expected = {key for key, value in sources.items() if value["availability"] == "available"}
    if set(documents) != expected:
        raise ValueError("Supply exactly the available corpus sources")
    packets, identities = {}, {}
    for source_id, content in documents.items():
        source = sources[source_id]
        if len(content) != source["bytes"] or hashlib.sha256(content).hexdigest() != source["sha256"]:
            raise ValueError(f"Corpus source hash/size changed: {source_id}")
        graph = parse_json(content)
        contract.validate(graph)
        packets[source_id] = {row["id"]: row for row in graph["records"] if row["kind"] == "observation"}
        for row in graph["records"]:
            if row["id"] in identities and row != identities[row["id"]]:
                raise ValueError(f"Conflicting record ID across corpus sources: {row['id']}")
            identities[row["id"]] = row
    unique, duplicates, conditions = {}, 0, {}
    for row in sorted(document["segments"], key=lambda item: (
            item["id"], sources[item["source_id"]]["availability"] == "missing", item["source_id"])):
        if row["source_id"] in packets:
            packet = packets[row["source_id"]].get(row["packet_id"])
            if packet is None:
                raise ValueError(f"Missing observation for segment {row['id']}")
            if any(packet[key] != row[key] for key in ("match_id", "run_id", "conditions")):
                raise ValueError(f"Segment/packet identity or condition mismatch: {row['id']}")
            if contract.utc(packet["captured_at"]) > contract.utc(row["cutoff_at"]):
                raise ValueError("Segment evidence postdates its cutoff")
        previous = conditions.setdefault(row["condition_id"], row["conditions"])
        if previous != row["conditions"]:
            raise ValueError("Condition ID maps to conflicting condition fields")
        if row["id"] in unique:
            # Different source IDs are separate provenance, not new segments.
            prior = unique[row["id"]]
            if any(prior[key] != row[key] for key in row if key != "source_id"):
                raise ValueError(f"Conflicting duplicate segment: {row['id']}")
            duplicates += 1
        else:
            unique[row["id"]] = row
    matches, groups = {}, {}
    for row in unique.values():
        if not row["included"]:
            continue
        group = groups.setdefault(row["condition_id"], {"condition_id": row["condition_id"],
            "conditions": row["conditions"], "setup_failures": [], "matches": [], "segments": []})
        # Keep telemetry scoped to segments; continuations can overlap, so no sum.
        group["segments"].append({**row, "effective_evidence_availability":
            "missing" if sources[row["source_id"]]["availability"] == "missing" else row["evidence_availability"]})
        if row["started_at"] is None:
            group["setup_failures"].append({"segment_id": row["id"], "reason": row["setup_failure"]})
            continue
        matches.setdefault(row["match_id"], []).append(row)
    for match_id, rows in sorted(matches.items()):
        rows.sort(key=lambda row: (contract.utc(row["cutoff_at"]), row["id"]))
        if len({(contract.utc(row["started_at"]), row["condition_id"]) for row in rows}) != 1:
            raise ValueError(f"Conflicting start/condition for match {match_id}; review required")
        terminals = {row["eventual_outcome"] for row in rows if row["eventual_outcome"] in contract.TERMINAL_OUTCOMES}
        placements = {row["placement"] for row in rows if row["placement"] is not None}
        terminal_times = {contract.utc(row["eventual_at"]) for row in rows if row["eventual_at"] is not None}
        if len(terminals) > 1 or len(placements) > 1 or len(terminal_times) > 1:
            raise ValueError(f"Conflicting terminal outcome/placement for match {match_id}; review required")
        result = {"match_id": match_id, "started_at": rows[0]["started_at"],
                  "segment_ids": [row["id"] for row in rows], "original_window_outcome": rows[0]["window_outcome"],
                  "eventual_outcome": next(iter(terminals)) if terminals else rows[-1]["eventual_outcome"],
                  "placement": next(iter(placements)) if placements else None}
        groups[rows[0]["condition_id"]]["matches"].append(result)
    for group in groups.values():
        group["segments"].sort(key=lambda row: row["id"])
        group["setup_failures"].sort(key=lambda row: row["segment_id"])
        group["starts"] = len(group["matches"])
        for column, field in (("original_window", "original_window_outcome"), ("eventual", "eventual_outcome")):
            counts = {outcome: sum(match[field] == outcome for match in group["matches"]) for outcome in contract.OUTCOMES}
            group[column] = {"counts": counts, "fractions":
                {outcome: count / group["starts"] if group["starts"] else None for outcome, count in counts.items()}}
        group["telemetry_coverage"] = {name: {
            "known_segments": sum(row[name] is not None for row in group["segments"]),
            "unknown_segments": sum(row[name] is None for row in group["segments"])}
            for name in ("placement", "rounds", "displayed_timer", "wall_seconds", "active_seconds", "coaching_interventions")}
        group["usage_coverage"] = {name: {
            "known_segments": sum(row["usage"][name] is not None for row in group["segments"]),
            "unknown_segments": sum(row["usage"][name] is None for row in group["segments"])} for name in ("tokens", "cost")}
    return {"schema_version": 1, "kind": "learning-corpus-summary", "corpus_id": document["id"],
            "cutoff_at": document["cutoff_at"], "sources": document["sources"],
            "groups": [groups[key] for key in sorted(groups)], "duplicate_segments": duplicates,
            "excluded_segments": sorted([row for row in unique.values() if not row["included"]], key=lambda row: row["id"]),
            "missing_sources": sorted(key for key, source in sources.items() if source["availability"] == "missing"),
            "input_authority": False, "policy_release": "not-evaluated", "media_verification": "not-attempted"}


def create(source, inputs, output=None):
    """Write a private reproducible report and exact inputs, with no overwrites."""
    content = _read(source, contract.MAX_BYTES)
    document = parse_json(content)
    declarations = contract.validate_corpus(document)
    available = {key for key, row in declarations.items() if row["availability"] == "available"}
    if set(inputs) != available:
        raise ValueError("Supply exactly the available corpus source paths")
    loaded = {key: _read(inputs[key], declarations[key]["bytes"]) for key in sorted(available)}
    report = summarize(document, loaded)
    report["corpus_sha256"] = hashlib.sha256(content).hexdigest()
    folder = new_bundle(output)
    try:
        write_bytes_atomic(str(folder / "corpus.json"), content)
        report["pinned_sources"] = {}
        for index, (key, data) in enumerate(loaded.items()):
            name = f"source-{index}.json"
            write_bytes_atomic(str(folder / name), data)
            report["pinned_sources"][key] = name
        if len(json.dumps(report, indent=2, allow_nan=False).encode()) + 1 > 4 * contract.MAX_BYTES:
            raise ValueError("Corpus report exceeds 4 MiB")
        write_json_atomic(str(folder / "summary.json"), report)
    except (OSError, ValueError, RuntimeError) as exc:
        error = ValueError(str(exc))
        error.receipt = {"error": str(exc), "partial": True, "bundle": str(folder), "input_authority": False}
        raise error from exc
    return {"artifact": str(folder / "summary.json"), "schema_version": 1,
            "groups": len(report["groups"]), "missing_sources": report["missing_sources"],
            "partial": False, "input_authority": False}

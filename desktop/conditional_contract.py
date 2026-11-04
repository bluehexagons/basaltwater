"""Version-one bounded controller plans, with no embedded input authority."""

from __future__ import annotations

import json

from desktop import learning_records as records


def predicates(values):
    names = set()
    for predicate in records.sequence(values, 4):
        records.keys(predicate, "field equals")
        records.text(predicate["field"])
        records.text(predicate["equals"])
        if predicate["field"] in names:
            raise ValueError("Duplicate predicate field")
        names.add(predicate["field"])
    if not names:
        raise ValueError("At least one explicit predicate is required")


def validate(plan):
    records.bounded(plan)
    if len(json.dumps(plan, allow_nan=False).encode()) > 65536:
        raise ValueError("Conditional plan exceeds 64 KiB")
    kind = plan.get("kind") if isinstance(plan, dict) else None
    if kind not in ("conditional-plan", "event-wait"):
        raise ValueError("Unsupported controller plan kind")
    common = "schema_version kind id run_id match_id phase scene_field allowed_scenes terminal_scenes limits "
    records.keys(plan, common + ("steps" if kind == "conditional-plan" else "until cadence_ms max_frames retain_frames"))
    if type(plan["schema_version"]) is not int or plan["schema_version"] != 1:
        raise ValueError("Unsupported controller plan version")
    for key in ("id", "run_id", "match_id", "phase", "scene_field"):
        records.text(plan[key])
    scenes = []
    for key in ("allowed_scenes", "terminal_scenes"):
        values = records.sequence(plan[key], 16)
        for value in values:
            records.text(value)
        if len(set(values)) != len(values):
            raise ValueError("Duplicate scene label")
        scenes.append(set(values))
    if not scenes[0] or scenes[0] & scenes[1]:
        raise ValueError("Declare allowed scenes and disjoint terminal return branches")
    records.keys(plan["limits"], "seconds inputs storage_bytes frame_bytes")
    for key, low, high in (("seconds", 1, 20 if kind == "conditional-plan" else 300),
                           ("inputs", 0, 20), ("storage_bytes", 65536, 128 * 1024 * 1024),
                           ("frame_bytes", 1, 16 * 1024 * 1024)):
        records.integer(plan["limits"][key], low, high)
    if plan["limits"]["frame_bytes"] + 32768 > plan["limits"]["storage_bytes"]:
        raise ValueError("Storage cannot retain one frame and controller receipts")
    if kind == "event-wait":
        if plan["limits"]["inputs"] != 0:
            raise ValueError("Event waits cannot declare an input budget")
        predicates(plan["until"])
        records.integer(plan["cadence_ms"], 100, 10000)
        records.integer(plan["max_frames"], 1, 200)
        records.integer(plan["retain_frames"], 1, 16)
        return
    steps = records.sequence(plan["steps"], 20)
    if not steps or len(steps) > plan["limits"]["inputs"]:
        raise ValueError("Declare 1–20 steps within the input budget")
    identifiers = set()
    for step in steps:
        records.keys(step, "id action target preconditions effect input")
        for key in ("id", "action"):
            records.text(step[key])
        if step["id"] in identifiers:
            raise ValueError("Duplicate step ID")
        identifiers.add(step["id"])
        records.keys(step["target"], "field equals")
        predicates([step["target"]])
        predicates(step["preconditions"])
        predicates(step["effect"])
        records.keys(step["input"], "kind button hold_ms")
        if step["input"]["kind"] != "click":
            raise ValueError("Version one supports one effect-verified click per step")
        records.integer(step["input"]["button"], 1, 3)
        records.integer(step["input"]["hold_ms"], 0, 5000)

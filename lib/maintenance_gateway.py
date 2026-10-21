"""Bounded gateway evidence for the host supervisor; never retire routes here."""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable
from typing import Any

from lib.validators import validate_username


_POLICY = "/etc/basaltwater/internal-web/policy.json"
_CLI = "/usr/local/bin/basaltwater-web"
_LIMIT = 10


def collect_gateway_checks(add: Callable[..., None], command: Callable[[list[str]], subprocess.CompletedProcess[str]]) -> None:
    """Read both managed previews and ordinary forwards, including legacy sites."""
    if not os.path.isfile(_POLICY):
        return
    inventories: dict[str, list[dict[str, Any]]] = {}
    previews_complete = False
    for kind, field, readiness in (("preview", "previews", "active"), ("forward", "forwards", "ready")):
        try:
            result = command([_CLI, kind, "list", "--json"])
            if result.returncode:
                raise ValueError("Unavailable gateway inventory")
            value = json.loads(result.stdout)
            rows = value.get(field) if isinstance(value, dict) else None
            if not isinstance(rows, list):
                raise ValueError("Invalid gateway inventory")
            checked = []
            for row in rows[:_LIMIT]:
                if (not isinstance(row, dict) or not isinstance(row.get("name"), str)
                        or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", row["name"])
                        or not isinstance(row.get("owner"), str) or not validate_username(row["owner"])
                        or type(row.get(readiness)) is not bool):
                    raise ValueError("Invalid gateway route metadata")
                checked.append(row)
            inventories[kind] = checked
            if kind == "preview":
                previews_complete = len(rows) <= _LIMIT
            if len(rows) > _LIMIT:
                add("gateway-partial:" + kind, f"Gateway {kind} checks covered only {_LIMIT} of {len(rows)} entries; review the remaining inventory")
        except (OSError, ValueError, subprocess.SubprocessError):
            add("gateway-unavailable:" + kind, f"Gateway {kind} inventory could not be inspected; run basaltwater-web {kind} list --json as the route owner")

    # A preview also owns a forward. Report its failed upstream once, with the
    # preview lifecycle command rather than removing its generated route alone.
    previews = {(row["owner"], row["name"]): row for row in inventories.get("preview", [])}
    dead_previews = {key for key, row in previews.items() if not row["active"]}
    for row in inventories.get("forward", []):
        if row["ready"]:
            continue
        key = row["owner"], row["name"]
        if key in previews:
            dead_previews.add(key)
        else:
            retirement = (f"after retirement approval, run as its owner: basaltwater-web forward remove {row['name']} --json"
                          if previews_complete else "inspect the complete preview inventory to establish its lifecycle before proposing retirement")
            add("gateway-forward:" + row["owner"] + ":" + row["name"],
                f"Gateway forward {row['name']} (owner {row['owner']}) has an unreachable loopback upstream. "
                f"Recheck ownership and current use; repair it or {retirement}. Route removal does not remove project files.")
    for owner, name in sorted(dead_previews):
        add("gateway-preview:" + owner + ":" + name,
            f"Managed preview {name} (owner {owner}) is inactive or its loopback upstream is unreachable. "
            f"Recheck service health and current use; repair it or, after retirement approval, run as its owner: "
            f"basaltwater-web preview stop {name} --json. Retained project files require a separate storage cleanup.")

#!/usr/bin/env python3
"""Fixed root SSH stdin/stdout adapter; metadata only, never secret output."""

from __future__ import annotations

import json
import os
import sys

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, "/opt/basaltwater")


def execute_request(payload: bytes) -> dict:
    """One bounded request on a private authenticated SSH stdin channel.

    The controller must disable request/body logging. No retries on lost ack.
    Key delivery is deliberately separate from this metadata transport.
    """
    from lib.atomic_io import parse_json
    from lib.headscale_admin import (
        execute_admin_action, inspect_machine_keys, issue_machine_key,
        manage_machine_key, reconcile_machine_key,
    )

    try:
        if os.geteuid() != 0 or len(payload) > 8192:
            raise ValueError()
        request = parse_json(payload.decode())
        if type(request) is not dict or set(request) != {"action", "parameters"}:
            raise ValueError()
        action, parameters = request["action"], request["parameters"]
        if type(parameters) is not dict or not isinstance(action, str):
            raise ValueError()
        if action == "machine_key_issue" and set(parameters) == {"approved_service_tags"}:
            result = issue_machine_key(parameters["approved_service_tags"])
        elif action == "machine_keys_list" and not parameters:
            result = inspect_machine_keys()
        elif action in {"machine_key_expire", "machine_key_revoke", "machine_key_delete"} and set(parameters) == {"id"}:
            result = manage_machine_key(action.removeprefix("machine_key_"), parameters["id"])
        elif action == "machine_key_reconcile" and set(parameters) == {"expected_key_ids", "expected_node_ids"}:
            result = reconcile_machine_key(**parameters)
        else:
            result = execute_admin_action(action, parameters)
        if len(json.dumps(result).encode()) > 1024 * 1024:
            raise ValueError()
        return {"ok": True, "result": result}
    except Exception:
        return {"ok": False, "error": "Headscale operation unconfirmed; reconcile before retry"}


def main() -> int:
    if len(sys.argv) > 1:
        import argparse
        from lib.headscale_admin_cli import add_headscale_admin_parser, run_headscale_admin_command

        parser = argparse.ArgumentParser()
        commands = parser.add_subparsers(required=True)
        add_headscale_admin_parser(commands)
        return run_headscale_admin_command(parser.parse_args(["headscale-admin", *sys.argv[1:]]))
    result = execute_request(sys.stdin.buffer.read(8193))
    print(json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

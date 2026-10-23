"""Native webhook payloads must survive real Proxmox event metadata."""

from __future__ import annotations

import json
import re
import unittest
from unittest.mock import patch

from lib.proxmox_hosts import ProxmoxHost
from lib.proxmox_manage import _webhook_body_template, install_webhook_notifications


def _render_helpers(template: str, values: dict) -> str:
    """Model PVE's documented JSON/escape helper semantics for this template."""
    missing = object()

    def value(expression: str):
        if expression.startswith("escape "):
            text = value(expression[7:].strip("()"))
            if text is missing:
                raise ValueError("PVE escape helper requires a value")
            return json.dumps(str(text), ensure_ascii=False)[1:-1]
        if expression.startswith("json "):
            return json.dumps(value(expression[5:]), ensure_ascii=False)
        result = values
        for key in expression.split("."):
            result = result.get(key, missing)
        return result

    template = re.sub(
        r"{{#if ([\w.]+)}}(.*?){{/if}}",
        lambda match: match[2] if value(match[1]) is not missing and value(match[1]) else "",
        template,
    )
    return re.sub(r"{{\s*(.*?)\s*}}", lambda match: str(value(match[1])), template)


class TestNativeNotificationPayload(unittest.TestCase):
    def test_payload_is_valid_json_with_empty_and_nonempty_metadata(self) -> None:
        for fields in ({}, {"hostname": 'host"name', "type": "vzdump", "detail": 'line one\n"quoted" \\ Ω'}):
            with self.subTest(fields=fields):
                values = {
                    "title": 'Backup "guest"', "message": 'first\nsecond \\ "third"',
                    "severity": "error", "fields": fields,
                }
                body = json.loads(_render_helpers(_webhook_body_template(), values))
                self.assertEqual(body["message"], values["message"])
                self.assertEqual(body["hostname"], fields.get("hostname", ""))
                self.assertEqual(json.loads(body["details"].split("\nfields=", 1)[1]), fields)

    @patch("lib.proxmox_manage._ssh_run")
    def test_shared_endpoint_matcher_name_fails_before_remote_mutation(self, command) -> None:
        with self.assertRaisesRegex(ValueError, "names must differ"):
            install_webhook_notifications(
                ProxmoxHost(name="pve", address="10.0.0.10"),
                "https://notify.example/hook",
                endpoint_name="same-name", matcher_name="same-name",
            )
        command.assert_not_called()


if __name__ == "__main__":
    unittest.main()

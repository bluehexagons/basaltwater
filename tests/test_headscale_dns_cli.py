"""Local parser dry-run and private input boundary."""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lib.headscale_dns_binding import DNSBinding
from lib.headscale_dns_cli import add_headscale_dns_parser, run_headscale_dns_command


class DNSCLITests(unittest.TestCase):
    def parse(self, args):
        parser = argparse.ArgumentParser()
        add_headscale_dns_parser(parser.add_subparsers(required=True))
        return parser.parse_args(["headscale-dns", *args])

    def test_inspect_dry_run_does_not_observe_or_lock(self):
        with patch("os.geteuid", return_value=0), patch("lib.headscale_dns_publish.inspect_dns") as inspect, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(0, run_headscale_dns_command(self.parse(["inspect", "--dry-run"])))
            inspect.assert_not_called()

    def test_publish_private_input_dry_run_and_unknown_fields(self):
        binding = DNSBinding("https://vpn.example.com", "7", ("100.64.0.2",)).to_dict()
        peer = DNSBinding("https://vpn.example.com", "8", ("100.64.0.3",)).to_dict()
        authority = dict(host="dns.example.com", user="root", key="/key", known_hosts="/known")
        payload = dict(binding=binding, peer=peer, dns_remote=authority, peer_remote=authority,
                       expected=None, old_remote=None)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "request.json"
            path.write_text(json.dumps(payload))
            path.chmod(0o600)
            args = self.parse(["publish", "--input", str(path), "--dry-run"])
            with patch("os.geteuid", return_value=0), \
                    patch("lib.headscale_dns_peer.DNSRemote.validate_files"), \
                    patch("lib.headscale_dns_publish.NativeAdmin") as native, \
                    redirect_stdout(io.StringIO()):
                with patch("lib.headscale_server.regular_bytes", return_value=path.read_bytes()):
                    self.assertEqual(0, run_headscale_dns_command(args))
                    native.assert_not_called()
                    payload["health"] = True
                    path.write_text(json.dumps(payload))
                with patch("lib.headscale_server.regular_bytes", return_value=path.read_bytes()), redirect_stderr(io.StringIO()):
                    self.assertEqual(1, run_headscale_dns_command(args))

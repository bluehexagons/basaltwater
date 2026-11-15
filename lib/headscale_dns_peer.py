"""Explicit root SSH authority for the fixed DNS observation/CAS protocol."""

from __future__ import annotations

from dataclasses import dataclass
import os

from lib.headscale_transport import SSHBinding, _credential, exchange


@dataclass(frozen=True)
class DNSRemote:
    host: str
    key: str
    known_hosts: str
    user: str = "root"

    def __post_init__(self) -> None:
        if self.user != "root":
            raise ValueError("Explicit root SSH authority required")
        self._binding()

    def _binding(self) -> SSHBinding:
        return SSHBinding(self.host, self.key, self.known_hosts)

    def validate_files(self) -> None:
        if os.geteuid() != 0:
            raise ValueError("DNS publication requires root SSH authority")
        for path in (self.key, self.known_hosts):
            fd = _credential(path, private=True)
            os.close(fd)

    def call(self, action: str, **parameters) -> dict:
        self.validate_files()
        return exchange(self._binding(), "dns", {"action": action, "parameters": parameters}, timeout=300)

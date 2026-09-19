"""Live, end-to-end Proxmox guest test.

This test provisions a real Proxmox guest, exercises the management helpers in
``lib/proxmox_manage.py`` against it (status, list, start, stop, health), and
then destroys it. The VM path is the primary workflow; LXC remains available as
an explicit compatibility mode. The test is gated behind the ``live_proxmox``
expensive-test category so it never runs in the default suite.

To run it manually::

    # VM-first path (recommended)
    BASALTWATER_RUN_LIVE_PROXMOX=1 \
    PROXMOX_TEST_HOST=10.0.0.10 \
    PROXMOX_TEST_GUEST_TYPE=vm \
    PROXMOX_TEST_USER=root \
    PROXMOX_TEST_SSH_KEY=~/.ssh/proxmox_ed25519 \
    PROXMOX_TEST_IP=10.0.0.50 \
    PROXMOX_TEST_STORAGE=local-lvm \
    python3 -m unittest tests.test_proxmox_live

    # Optional for VM runs: use a pre-uploaded qcow2 or explicit URL
    PROXMOX_TEST_IMAGE=local:import/debian-13-genericcloud-amd64.qcow2

    # LXC compatibility path
    BASALTWATER_RUN_LIVE_PROXMOX=1 \
    PROXMOX_TEST_HOST=10.0.0.10 \
    PROXMOX_TEST_GUEST_TYPE=lxc \
    PROXMOX_TEST_TEMPLATE=local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst \
    PROXMOX_TEST_STORAGE=local-lvm \
    PROXMOX_TEST_BRIDGE=vmbr0 \
    PROXMOX_TEST_VMID=9999 \
    python3 -m unittest tests.test_proxmox_live

    # or via the runner:
    ./run_tests.py --expensive live_proxmox tests.test_proxmox_live

All ``PROXMOX_TEST_*`` env vars except ``PROXMOX_TEST_HOST`` and the
guest-type-specific requirements are optional and have sensible defaults.

The test always tries to destroy the guest in tearDown, even if the test
body fails, so a failing run shouldn't leave debris behind.
"""

from __future__ import annotations

import os
import shlex
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.config import SetupConfig
from lib.proxmox_vm import provision_vm
from lib.proxmox_hosts import ProxmoxHost
from lib.proxmox_manage import (
    ProxmoxManageError,
    _run_on_host,
    destroy_container,
    get_container_status,
    health_check,
    list_containers,
    start_container,
    stop_container,
)
from tests.expensive_support import expensive


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


def _guest_type_from_env() -> str:
    explicit = (_env("PROXMOX_TEST_GUEST_TYPE") or "").strip().lower()
    if explicit in {"vm", "lxc"}:
        return explicit
    if explicit:
        raise ValueError(
            "PROXMOX_TEST_GUEST_TYPE must be 'vm' or 'lxc' "
            f"(got {explicit!r})"
        )
    if _env("PROXMOX_TEST_TEMPLATE"):
        return "lxc"
    return "vm"


def _default_hostname(guest_type: str) -> str:
    if guest_type == "vm":
        guest_ip = (_env("PROXMOX_TEST_IP") or "vm").replace(".", "-")
        return f"basaltwater-live-vm-{guest_ip}"
    vmid = _env("PROXMOX_TEST_VMID", "9999") or "9999"
    return f"basaltwater-live-lxc-{vmid}"


def _required_env_missing() -> list[str]:
    missing: list[str] = []
    guest_type = _guest_type_from_env()
    required_vars = ["PROXMOX_TEST_HOST"]
    if guest_type == "vm":
        required_vars.append("PROXMOX_TEST_IP")
    else:
        required_vars.append("PROXMOX_TEST_TEMPLATE")
    for var in required_vars:
        if not _env(var):
            missing.append(var)
    return missing


def _live_host_from_env() -> ProxmoxHost:
    return ProxmoxHost(
        name=_env("PROXMOX_TEST_HOST_NAME", "live-test") or "live-test",
        address=_env("PROXMOX_TEST_HOST") or "",
        user=_env("PROXMOX_TEST_USER", "root") or "root",
        ssh_key=_env("PROXMOX_TEST_SSH_KEY"),
    )


def check_live_proxmox_prereqs() -> list[str]:
    """Return prerequisite errors for the destructive live Proxmox test."""
    errors: list[str] = []
    try:
        guest_type = _guest_type_from_env()
    except ValueError as exc:
        return [str(exc)]

    missing = _required_env_missing()
    if missing:
        errors.append("missing required env vars: " + ", ".join(missing))

    ssh_key = _env("PROXMOX_TEST_SSH_KEY")
    if ssh_key and not Path(ssh_key).expanduser().exists():
        errors.append(f"PROXMOX_TEST_SSH_KEY does not exist: {ssh_key}")

    if guest_type == "lxc":
        vmid_raw = _env("PROXMOX_TEST_VMID", "9999") or "9999"
        try:
            vmid = int(vmid_raw)
        except ValueError:
            errors.append(f"PROXMOX_TEST_VMID must be an integer: {vmid_raw!r}")
        else:
            if vmid < 100:
                errors.append("PROXMOX_TEST_VMID must be >= 100")
    else:
        from lib.cloud_images import parse_image_argument

        vm_image = _env("PROXMOX_TEST_IMAGE")
        if vm_image:
            try:
                parse_image_argument(vm_image)
            except ValueError as exc:
                errors.append(f"Invalid PROXMOX_TEST_IMAGE: {exc}")

    if _env("PROXMOX_TEST_HOST"):
        host = _live_host_from_env()
        command_name = "qm" if guest_type == "vm" else "pct"
        result = _run_on_host(host, f"command -v {command_name} >/dev/null")
        if result.returncode != 0:
            errors.append(
                f"cannot reach Proxmox host or '{command_name}' is unavailable "
                f"(rc={result.returncode}, stderr={result.stderr.strip()!r})"
            )
    return errors


@expensive(
    "live_proxmox",
    "Creates and destroys a real Proxmox guest on a host",
)
class TestProxmoxLiveLifecycle(unittest.TestCase):
    """Round-trip: create, list, start, stop, destroy a real guest."""

    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.guest_type = _guest_type_from_env()
        except ValueError as exc:
            raise unittest.SkipTest(str(exc)) from exc

        missing = _required_env_missing()
        if missing:
            raise unittest.SkipTest(
                "Missing required env vars for live Proxmox test: "
                + ", ".join(missing)
            )
        cls.host = _live_host_from_env()
        cls.storage = _env("PROXMOX_TEST_STORAGE", "local-lvm") or "local-lvm"
        cls.hostname = _env(
            "PROXMOX_TEST_HOSTNAME", _default_hostname(cls.guest_type)
        ) or _default_hostname(cls.guest_type)
        cls.password = _env("PROXMOX_TEST_PASSWORD", "basaltwater-live") or "basaltwater-live"
        cls.active_vmid: int | None = None

        if cls.guest_type == "vm":
            cls.target_ip = _env("PROXMOX_TEST_IP") or ""
            cls.vm_image = _env("PROXMOX_TEST_IMAGE")
            cls.template = None
            cls.bridge = None
            cls.vmid = None
        else:
            cls.target_ip = None
            cls.vm_image = None
            cls.template = _env("PROXMOX_TEST_TEMPLATE") or ""
            cls.bridge = _env("PROXMOX_TEST_BRIDGE", "vmbr0") or "vmbr0"
            cls.vmid = int(_env("PROXMOX_TEST_VMID", "9999") or "9999")

    def setUp(self) -> None:
        self.active_vmid = None
        # Ensure a clean slate: destroy any leftover guest from a prior run.
        self._safe_destroy()

    def tearDown(self) -> None:
        self._safe_destroy()

    def _find_vm_vmid(self) -> int | None:
        matches = [
            guest.vmid
            for guest in list_containers(self.host)
            if guest.guest_type == "vm" and guest.name == self.hostname
        ]
        if len(matches) > 1:
            self.fail(f"Multiple live VMs matched hostname {self.hostname!r}: {matches}")
        if not matches:
            return None
        return matches[0]

    def _safe_destroy(self) -> None:
        vmids: list[int] = []
        if self.guest_type == "vm":
            vmid = self.active_vmid or self._find_vm_vmid()
            if vmid is not None:
                vmids.append(vmid)
        elif self.vmid is not None:
            vmids.append(self.vmid)

        for vmid in vmids:
            try:
                destroy_container(self.host, vmid, force=True, purge=True)
            except ProxmoxManageError:
                # Guest probably didn't exist; that's fine.
                continue

    def _create_lxc(self) -> int:
        assert self.template is not None
        assert self.bridge is not None
        assert self.vmid is not None
        cmd = (
            f"pct create {self.vmid} {shlex.quote(self.template)} "
            f"--hostname {shlex.quote(self.hostname)} "
            f"--memory 256 --cores 1 --rootfs {shlex.quote(f'{self.storage}:1')} "
            f"--net0 {shlex.quote(f'name=eth0,bridge={self.bridge},ip=dhcp,type=veth')} "
            f"--password {shlex.quote(self.password)} --unprivileged 1 --start 0"
        )
        result = _run_on_host(self.host, cmd)
        if result.returncode != 0:
            self.fail(
                f"pct create failed (rc={result.returncode}): "
                f"stdout={result.stdout!r} stderr={result.stderr!r}"
            )
        return self.vmid

    def _create_vm(self) -> int:
        assert self.target_ip is not None
        config = SetupConfig(
            host=self.target_ip,
            username="root",
            system_type="server_web",
            machine_type="vm",
            friendly_name=self.hostname,
            hosted_node=self.host.address,
            hosted_user=self.host.user,
            hosted_key=self.host.ssh_key,
            container_memory="2G",
            container_storage=[["root", self.storage, "10G"]],
            container_cores=1,
            container_base="debian",
            vm_image=self.vm_image,
        )
        provision_vm(config, image=self.vm_image)
        vmid = self._find_vm_vmid()
        if vmid is None:
            self.fail(f"Provisioned VM {self.hostname!r} was not found in qm list output")
        return vmid

    def _create(self) -> int:
        if self.guest_type == "vm":
            return self._create_vm()
        return self._create_lxc()

    def test_full_lifecycle(self) -> None:
        # Create
        self.active_vmid = self._create()

        # List should include our VMID
        containers = list_containers(self.host)
        guests = {c.vmid: c for c in containers}
        self.assertIn(
            self.active_vmid, guests,
            f"VMID {self.active_vmid} not in list: {sorted(guests)}",
        )
        self.assertEqual(guests[self.active_vmid].guest_type, self.guest_type)

        # Status should be 'stopped' right after creation (we passed --start 0)
        status = get_container_status(self.host, self.active_vmid)
        expected_initial_status = "running" if self.guest_type == "vm" else "stopped"
        self.assertEqual(status, expected_initial_status)

        # Start it
        start_container(self.host, self.active_vmid)
        self.assertEqual(get_container_status(self.host, self.active_vmid), "running")

        # Health check (best-effort - we don't require the guest to be
        # pingable from the test runner since networking varies wildly).
        report = health_check(self.host, self.active_vmid, ssh_probe=False)
        self.assertEqual(report.status, "running")
        self.assertEqual(report.guest_type, self.guest_type)

        # Stop it gracefully
        stop_container(self.host, self.active_vmid)
        self.assertEqual(get_container_status(self.host, self.active_vmid), "stopped")

        # Destroy it
        destroyed_vmid = self.active_vmid
        destroy_container(self.host, destroyed_vmid, purge=True)
        self.active_vmid = None
        containers_after = list_containers(self.host)
        self.assertNotIn(
            destroyed_vmid, {c.vmid for c in containers_after},
            "Guest still present after destroy",
        )


if __name__ == "__main__":
    unittest.main()

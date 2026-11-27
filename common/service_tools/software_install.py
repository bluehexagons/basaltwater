"""Plan or apply one reviewed software selection on a managed Debian machine."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lib.atomic_io import read_json_file, write_json_atomic
from lib.machine_state import PERSISTENT_SETUP_CONFIG_FILE
from lib.privilege_policy import MAX_MESSAGE, digest, protected_path
from lib.software_requests import apt_plan, save_selection, selection


def run(mode: str, parameters: dict, expected: str | None = None) -> dict:
    import apt_pkg
    from contextlib import nullcontext
    from lib.setup_common import _target_setup_lock
    from common.service_tools.admin_job import _refresh_recovery_block_reason

    _field, names = selection(parameters)
    apply = mode == "apply"
    if mode not in {"preview", "apply"} or apply and os.geteuid() != 0:
        raise ValueError("Invalid software worker invocation")
    apt_pkg.init()
    with _target_setup_lock() if apply else nullcontext():
        if apply and (_refresh_recovery_block_reason() or
                      Path(PERSISTENT_SETUP_CONFIG_FILE).with_name("setup-operation.json").exists()):
            raise ValueError("Resolve the interrupted setup before installing software")
        protected_path(PERSISTENT_SETUP_CONFIG_FILE)
        config = read_json_file(PERSISTENT_SETUP_CONFIG_FILE)
        updated = save_selection(config, {**parameters, "scope": "saved"})
        with apt_pkg.SystemLock() if apply else nullcontext():
            cache, summary, rows = apt_plan(parameters)
            summary.update(profile=config["system_type"], username=config["username"])
            summary["package_digest"] = summary["digest"]
            summary["digest"] = digest([parameters, summary["profile"], summary["username"], summary["package_digest"]])
            if not apply:
                return summary
            if expected != summary["digest"]:
                raise ValueError("APT transaction changed after review")
            if summary["changes"]:
                os.environ.update(HOME="/root", DEBIAN_FRONTEND="noninteractive")
                os.umask(0o022)
                apt_pkg.config.set("Dpkg::Options::", "--force-confold")
                if not cache.commit(allow_unauthenticated=False):
                    raise RuntimeError("APT installation failed")
            cache.open()
            if (any(cache[name]._pkg.current_state != apt_pkg.CURSTATE_INSTALLED for name in names)
                    or any(cache[row["package"]]._pkg.current_state != apt_pkg.CURSTATE_INSTALLED
                           or cache[row["package"]].installed.version != row["version"] for row in rows)):
                raise RuntimeError("The installed packages differ from the reviewed transaction")
            if parameters["scope"] == "saved" and updated != config:
                write_json_atomic(PERSISTENT_SETUP_CONFIG_FILE, updated, mode=0o600)
            return summary


if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["controller"] and os.geteuid() == 0:
            from lib.privilege_client import exchange
            raw = sys.stdin.buffer.read(MAX_MESSAGE + 1)
            if len(raw) > MAX_MESSAGE:
                raise ValueError("Controller request exceeds size limit")
            message = json.loads(raw)
            if not isinstance(message, dict) or message.get("action") not in {"software-queue", "software-claim", "software-saved"}:
                raise ValueError("Invalid controller operation")
            print(json.dumps(exchange(message)))
            raise SystemExit(0)
        mode, scope, kind, name, *rest = sys.argv[1:]
        if mode == "details" and not rest:
            _cache, summary, rows = apt_plan({"scope": scope, "kind": kind, "name": name})
            print(json.dumps({**summary, "packages": rows}))
            raise SystemExit(0)
        if len(rest) != int(mode == "apply"):
            raise ValueError("Invalid software plan arguments")
        print(json.dumps(run(mode, {"kind": kind, "scope": scope, "name": name}, rest[0] if rest else None)))
    except (OSError, ImportError, ValueError, RuntimeError, KeyError, SystemError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)

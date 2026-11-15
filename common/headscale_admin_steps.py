"""Distribution runtime dependencies for the fixed private native adapter."""

from __future__ import annotations

from lib.config import SetupConfig
from lib.remote_utils import is_dry_run, missing_packages, run


def configure_headscale_administration(config: SetupConfig) -> None:
    if not config.headscale or config.dry_run or is_dry_run():
        return
    from lib.headscale_admin import NativeAdmin

    packages = ["python3-grpcio", "python3-protobuf"]
    if missing_packages(packages):
        run(["apt-get", "install", "-y", *packages], timeout=300)
    if missing_packages(packages):
        raise RuntimeError("Native Headscale administration runtime was not installed")
    adapter = NativeAdmin()
    adapter.verify()
    # Exercise the pin's actual socket/schema before advertising administration.
    adapter.users()

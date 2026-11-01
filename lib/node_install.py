"""Serialize binary NVM installs and preserve interrupted installation prefixes."""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Iterator

if __package__ in {None, ""}:
    # Setup invokes this with -I to keep lib/types.py out of stdlib lookup.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.node_runtime_ownership import runtime_directory, runtime_lock


@contextmanager
def node_install_guard(nvm_dir: str | Path, version: str) -> Iterator[bool]:
    """Hold the maintenance lock and retain any incomplete target before NVM runs.

    Yield whether the target existed before recovery, so repairing a legacy or
    project installation never grants automatic cleanup ownership.
    """
    with runtime_lock(nvm_dir):
        target = runtime_directory(nvm_dir, version)
        existed = target.exists()
        # NVM tests only bin/node. An interrupted extraction can also leave
        # node in place while npm's symlink target has not been moved yet.
        complete = all((target / "bin" / tool).is_file()
                       and os.access(target / "bin" / tool, os.X_OK)
                       for tool in ("node", "npm"))
        if existed and not complete:
            recovery = Path(tempfile.mkdtemp(
                prefix=".basaltwater-incomplete-", dir=target.parents[2],
            ))
            try:
                target.rename(recovery / target.name)
            except OSError:
                recovery.rmdir()
                raise
            print(f"Retained incomplete Node installation at {recovery / target.name}; "
                  "retrying the binary install", file=sys.stderr, flush=True)
        yield existed


def main() -> int:
    """Run inside setup's supervised process group, as the runtime's owner."""
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("nvm_dir")
    parser.add_argument("version")
    args = parser.parse_args()
    try:
        with node_install_guard(args.nvm_dir, args.version):
            # Inherit setup's process group: its supervisor terminates this
            # helper, NVM, and all download/extraction children on Ctrl+C or
            # timeout. A nested session would escape that cancellation.
            return subprocess.call([
                "/bin/bash", "--noprofile", "--norc", "-c",
                'export NVM_DIR="$1" && . "$NVM_DIR/nvm.sh" --no-use && nvm install -b "$2"',
                "basaltwater-node-install", args.nvm_dir, args.version,
            ])
    except KeyboardInterrupt:
        print("Node installation canceled; rerun setup to resume safely.", file=sys.stderr)
        return 130
    except (OSError, ValueError) as exc:
        print(f"Node binary installation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

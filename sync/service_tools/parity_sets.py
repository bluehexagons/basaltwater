"""Immutable PAR2 generations selected by a single atomic metadata record."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import tempfile

from lib.atomic_io import _fsync_directory, read_json_file, write_json_atomic


def _location(file_path: str, directory: str, database: str) -> tuple[str, str]:
    from sync.service_tools.scrub_par2 import _confined_path
    from sync.service_tools.scrub_findings import METADATA_DIR

    _confined_path(file_path, directory)
    if Path(os.path.abspath(file_path)).is_relative_to(Path(os.path.abspath(database))):
        raise ValueError('Cannot protect parity database contents as source data')
    relative = os.path.relpath(file_path, directory)
    if relative == '.' or Path(relative).parts[0] == METADATA_DIR:
        raise ValueError('Invalid protected file path')
    identity = hashlib.sha256(os.fsencode(relative)).hexdigest()
    root = os.path.join(database, METADATA_DIR, 'sets', identity)
    _confined_path(root, database)
    return relative, root


def locate(file_path: str, directory: str, database: str) -> tuple[str, list[str]]:
    """Prefer an active generation; retain legacy sets until explicit acceptance."""
    from sync.service_tools.scrub_par2 import _confined_path, _parity_files

    relative, root = _location(file_path, directory, database)
    active = os.path.join(root, 'active.json')
    try:
        state = read_json_file(active, max_bytes=16384)
    except FileNotFoundError:
        base = os.path.join(database, relative + '.par2')
        # Legacy volume naming cannot identify these filenames unambiguously.
        ambiguous = relative.endswith('.par2') or re.search(r'\.vol\d+\+\d+$', relative)
        alias = os.path.join(database, relative if relative.endswith('.par2') else re.sub(r'\.vol\d+\+\d+$', '', relative) + '.par2')
        if ambiguous and (_parity_files(base) or _parity_files(alias)):
            raise ValueError('Ambiguous legacy parity filename; review old evidence and use a separately configured empty parity database')
        _confined_path(base, database)
        return base, _parity_files(base)
    if (not isinstance(state, dict) or state.get('version') != 1 or state.get('file') != relative
            or not isinstance(state.get('generation'), str)
            or not re.fullmatch(r'g-[a-zA-Z0-9_-]+', state['generation'])
            or not isinstance(state.get('files'), list) or not state['files']
            or any(not isinstance(name, str) or not re.fullmatch(r'set(?:\.vol\d+\+\d+)?\.par2', name) for name in state['files'])):
        raise ValueError('Invalid active parity generation')
    base = os.path.join(root, state['generation'], 'set.par2')
    _confined_path(base, database)
    paths = _parity_files(base)
    if sorted(os.path.basename(path) for path in paths) != sorted(state['files']):
        raise ValueError('Active parity generation is missing')
    for path in paths:
        _confined_path(path, database)
    return base, paths


def publish(file_path: str, directory: str, database: str, paths: list[str]) -> None:
    """Durably copy a verified set, then atomically switch its active record."""
    from sync.service_tools.scrub_findings import _copy_durable

    if not paths:
        raise ValueError('Cannot publish an empty parity set')
    relative, root = _location(file_path, directory, database)
    os.makedirs(root, mode=0o700, exist_ok=True)
    generation = tempfile.mkdtemp(prefix='g-', dir=root)
    names = set()
    for path in paths:
        volume = re.search(r'(\.vol\d+\+\d+\.par2)$', path)
        name = 'set' + volume[1] if volume else 'set.par2'
        if name in names or not path.endswith('.par2'):
            raise ValueError('Conflicting parity filenames')
        names.add(name)
        _copy_durable(path, os.path.join(generation, name))
    _fsync_directory(generation)
    # Persist newly created ancestor entries as well as the generation files.
    for parent in (root, os.path.dirname(root), os.path.dirname(os.path.dirname(root)), database):
        _fsync_directory(parent)
    write_json_atomic(os.path.join(root, 'active.json'), {
        'version': 1, 'file': relative, 'generation': os.path.basename(generation), 'files': sorted(names),
    })


def protected_files(database: str) -> list[str]:
    """List generation-backed files, including missing source files."""
    from sync.service_tools.scrub_par2 import _confined_path
    from sync.service_tools.scrub_findings import METADATA_DIR

    root = os.path.join(database, METADATA_DIR, 'sets')
    _confined_path(root, database)
    if not os.path.isdir(root):
        return []
    result = []
    for entry in os.scandir(root):
        if not re.fullmatch(r'[a-f0-9]{64}', entry.name) or not entry.is_dir(follow_symlinks=False):
            raise ValueError('Invalid parity set directory')
        path = os.path.join(entry.path, 'active.json')
        _confined_path(path, database)
        try:
            state = read_json_file(path, max_bytes=16384)
        except FileNotFoundError:
            continue  # Interrupted, unpublished generation.
        relative = state.get('file') if isinstance(state, dict) else None
        if (not isinstance(relative, str) or os.path.isabs(relative) or '..' in Path(relative).parts
                or relative in ('', '.') or hashlib.sha256(os.fsencode(relative)).hexdigest() != entry.name):
            raise ValueError('Invalid parity file identity')
        result.append(relative)
    return result

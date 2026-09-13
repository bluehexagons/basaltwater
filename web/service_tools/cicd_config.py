"""Shared CI/CD configuration schema and service-readable persistence."""

from __future__ import annotations

import grp
import json
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from lib.atomic_io import read_json_file, write_json_atomic
from lib.validation import validate_filesystem_path
from lib.validators import validate_host
from web.service_tools.cicd_security import validate_branch_ref, validate_repo_url


def validate_config(config: object) -> dict:
    """Validate version 1, including legacy configurations without a version."""
    if not isinstance(config, dict) or type(config.get('version', 1)) is not int or config.get('version', 1) != 1:
        raise ValueError('CI/CD configuration must be a version 1 JSON object')
    repositories = config.get('repositories')
    if not isinstance(repositories, list):
        raise ValueError('CI/CD repositories must be a list')
    urls: set[str] = set()
    for repo in repositories:
        if not isinstance(repo, dict):
            raise ValueError('CI/CD repository must be an object')
        url = validate_repo_url(repo.get('url'))
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.query or parsed.fragment:
            raise ValueError('CI/CD repository URL must use HTTPS without query or fragment')
        if not validate_host(parsed.hostname):
            raise ValueError('Invalid CI/CD repository host')
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError('Invalid CI/CD repository port')
        if url in urls:
            raise ValueError('Duplicate CI/CD repository URL')
        urls.add(url)
        branches = repo.get('branches', ['main'])
        if not isinstance(branches, list) or not branches:
            raise ValueError('CI/CD branches must be a nonempty list')
        for branch in branches:
            if not isinstance(branch, str):
                raise ValueError('CI/CD branch must be a string')
            validate_branch_ref('refs/heads/' + branch)
        scripts = repo.get('scripts', {})
        if not isinstance(scripts, dict):
            raise ValueError('CI/CD scripts must be an object')
        for stage, path in scripts.items():
            if stage not in {'install', 'build', 'test', 'deploy'} or not isinstance(path, str) or not path:
                raise ValueError('Invalid CI/CD script stage or path')
            validate_filesystem_path(path)
            if PurePosixPath(path).is_absolute() or '..' in PurePosixPath(path).parts:
                raise ValueError('CI/CD scripts must stay within the repository')
        for field in ('deploy_target', 'deploy_spec'):
            if field in repo and (not isinstance(repo[field], str) or not repo[field]):
                raise ValueError(f'CI/CD {field} must be a nonempty string')
            if field in repo:
                validate_filesystem_path(repo[field])
    return config


def load_config_file(path: str) -> dict:
    """Fail explicitly on missing, unreadable, or malformed configuration."""
    return validate_config(read_json_file(path))


def save_config_file(path: str, config: dict) -> None:
    """Publish root:webhook/0640 before the atomic rename makes it visible."""
    validate_config(config)
    if len((json.dumps(config, indent=2) + '\n').encode('utf-8')) > 1024 * 1024:
        raise ValueError('CI/CD configuration exceeds 1 MiB')
    write_json_atomic(path, config, mode=0o640, uid=0, gid=grp.getgrnam('webhook').gr_gid)

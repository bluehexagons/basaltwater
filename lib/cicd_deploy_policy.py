"""Structured input validation for privileged CI/CD nginx rendering."""

from __future__ import annotations

import posixpath
import re

from lib.validation import validate_filesystem_path, validate_no_control_characters
from lib.validators import validate_host


def validate_nginx_path(value: object) -> str:
    """Accept normalized absolute paths that cannot introduce nginx syntax."""
    if not isinstance(value, str):
        raise ValueError("nginx path must be a string")
    validate_filesystem_path(value)
    if (
        len(value) > 4096
        or not re.fullmatch(r"/[A-Za-z0-9/._-]*", value)
        or value.startswith('//')
        or (posixpath.normpath(value) != value.rstrip('/') and value != '/')
    ):
        raise ValueError("nginx path must be absolute, normalized, and contain only safe components")
    return value


def validate_nginx_deployment(value: object) -> dict[str, str | bool]:
    """Accept known site parameters and a static-only SPA option."""
    fields = {'domain', 'path', 'serve_path', 'project_type'}
    allowed_fields = fields | {'spa'}
    if not isinstance(value, dict) or not fields <= set(value) or not set(value) <= allowed_fields:
        raise ValueError("nginx deployment requires domain, path, serve_path, and project_type, with optional spa")
    domain = value['domain']
    if not isinstance(domain, str):
        raise ValueError("deployment domain must be a hostname")
    validate_no_control_characters(domain, 'deployment domain')
    if len(domain) > 253 or not validate_host(domain) or domain.endswith('.'):
        raise ValueError("deployment domain must be a hostname")
    path = validate_nginx_path(value['path'])
    serve_path = validate_nginx_path(value['serve_path'])
    project_type = value['project_type']
    if not isinstance(project_type, str) or project_type not in {'static', 'node', 'unknown', 'godot-web'}:
        raise ValueError("unsupported remote deployment project type")
    deployment: dict[str, str | bool] = {
        'domain': domain,
        'path': path,
        'serve_path': serve_path,
        'project_type': project_type,
    }
    if 'spa' in value:
        if project_type != 'static':
            raise ValueError("spa is only supported for static deployments")
        if type(value['spa']) is not bool:
            raise ValueError("spa must be a boolean")
        deployment['spa'] = value['spa']
    return deployment

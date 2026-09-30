"""Compatibility helpers for upstream T3 Code service runtime state."""

from __future__ import annotations


T3_SUPPORTED_SERVICE_PROTOCOLS = frozenset((2, 3))


def is_supported_t3_service_protocol(value: object) -> bool:
    """Return whether ``value`` is a known T3 service-state protocol."""

    return type(value) is int and value in T3_SUPPORTED_SERVICE_PROTOCOLS

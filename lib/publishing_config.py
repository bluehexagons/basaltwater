"""Non-secret setup selection for engine-independent publishing tools."""

from __future__ import annotations

from lib.validation import validate_package_name

PUBLISHING_TOOLS = ("butler", "steamcmd")


def validate_publishing_tools(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("publishing_tools must be a list")
    result = []
    for tool in value:
        validate_package_name(tool)
        if tool not in PUBLISHING_TOOLS:
            raise ValueError("Publishing tools are butler and steamcmd")
        if tool not in result:
            result.append(tool)
    return result

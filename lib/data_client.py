"""Panel client for the local recovery service."""

from __future__ import annotations

import json
import socket

from lib.data_actions import DATA_MESSAGE_BYTES, DATA_SOCKET, read_data_message


class RecoveryRequestUnconfirmed(ValueError):
    """The local recovery service returned a public reason for uncertainty."""


def exchange(message: dict) -> dict:
    data = (json.dumps(message) + "\n").encode()
    if len(data) > DATA_MESSAGE_BYTES:
        raise ValueError("Recovery message exceeds the size limit")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(8)
        connection.connect(DATA_SOCKET)
        connection.sendall(data)
        with connection.makefile("rb") as stream:
            response = read_data_message(stream)
    if not isinstance(response, dict):
        raise ValueError("Invalid recovery response")
    if response.get("ok") is False and isinstance(response.get("error"), str):
        raise RecoveryRequestUnconfirmed(response["error"])
    if response.get("ok") is not True or not isinstance(response.get("result"), dict):
        raise ValueError("Invalid recovery response")
    return response["result"]

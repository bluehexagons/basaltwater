#!/usr/bin/env python3

from __future__ import annotations

import re


def validate_ip_address(ip: str) -> bool:
    pattern = r'([0-9]{1,3}\.){3}[0-9]{1,3}'
    if not isinstance(ip, str) or not re.fullmatch(pattern, ip):
        return False
    octets = ip.split('.')
    return all(0 <= int(octet) <= 255 for octet in octets)


def validate_host(host: str) -> bool:
    if not isinstance(host, str):
        return False
    normalized_host = host.lower().removesuffix('.')
    if len(normalized_host) > 253:
        return False
    if validate_ip_address(normalized_host):
        return True
    hostname_pattern = r'^([a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)*[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?$'
    return bool(re.fullmatch(hostname_pattern, normalized_host))


def validate_username(username: str) -> bool:
    pattern = r'^[a-z_][a-z0-9_-]{0,31}$'
    return isinstance(username, str) and bool(re.fullmatch(pattern, username))


def validate_steam_account_name(username: str) -> bool:
    """Accept bounded SteamCMD account tokens independently of Unix usernames."""
    return isinstance(username, str) and bool(re.fullmatch(r"[A-Za-z0-9_]{2,64}", username))


def validate_github_login(login: str) -> bool:
    """Return whether a login follows GitHub's public account-name rules."""
    pattern = r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$"
    return bool(re.fullmatch(pattern, login))

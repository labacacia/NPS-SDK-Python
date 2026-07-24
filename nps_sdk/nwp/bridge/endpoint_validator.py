# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0
"""Validates outbound Bridge endpoints before dereferencing them
(port of .NET ``BridgeEndpointValidator``). SSRF guard."""
from __future__ import annotations

from urllib.parse import SplitResult, urlsplit

from nps_sdk.nwp.action_node_server import is_private_host
from nps_sdk.nwp.bridge import target_parser
from nps_sdk.nwp.bridge.errors import BridgeDispatchException, BridgeErrorCodes
from nps_sdk.nwp.bridge.types import BridgeTarget


def parse_http_endpoint(target: BridgeTarget) -> SplitResult:
    """Parse and validate an HTTP(S) Bridge endpoint.

    By default both ``http://`` and ``https://`` are accepted, while private
    and loopback hosts are rejected as an SSRF guard.
    """
    if target is None:
        raise BridgeDispatchException(BridgeErrorCodes.ENDPOINT_INVALID, "bridge_target is required.")

    parts = urlsplit(target.endpoint)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise BridgeDispatchException(
            BridgeErrorCodes.ENDPOINT_INVALID,
            "bridge_target.endpoint must be an absolute http:// or https:// URI.",
        )

    if not _get_bool(target, "allow_http", True) and parts.scheme == "http":
        raise BridgeDispatchException(
            BridgeErrorCodes.ENDPOINT_INVALID,
            "bridge_target.endpoint MUST use https:// unless bridge_target.allow_http is true.",
        )

    allowed_prefixes = _get_string_list(target, "allowed_prefixes")
    if allowed_prefixes and not any(_matches_allowed_prefix(parts, p) for p in allowed_prefixes):
        raise BridgeDispatchException(
            BridgeErrorCodes.ENDPOINT_INVALID,
            f"bridge_target.endpoint '{target.endpoint}' is not in bridge_target.allowed_prefixes.",
        )

    if _get_bool(target, "reject_private", True) and is_private_host(parts.hostname or ""):
        raise BridgeDispatchException(
            BridgeErrorCodes.ENDPOINT_INVALID,
            f"bridge_target.endpoint host '{parts.hostname}' is private or loopback (SSRF guard).",
        )

    return parts


def _get_bool(target: BridgeTarget, name: str, default: bool) -> bool:
    found, value = target_parser.try_get_json(target, name)
    if not found:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
    return default


def _get_string_list(target: BridgeTarget, name: str) -> list[str]:
    found, value = target_parser.try_get_json(target, name)
    if not found:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, str) and item.strip()]
    return []


def _matches_allowed_prefix(endpoint: SplitResult, raw_prefix: str) -> bool:
    prefix = urlsplit(raw_prefix)
    if not prefix.scheme or not prefix.netloc:
        return False

    if (
        endpoint.scheme.lower() != prefix.scheme.lower()
        or (endpoint.hostname or "").lower() != (prefix.hostname or "").lower()
        or _port(endpoint) != _port(prefix)
    ):
        return False

    prefix_path = prefix.path or "/"
    if prefix_path == "/":
        return True

    endpoint_path = endpoint.path or "/"
    if not endpoint_path.lower().startswith(prefix_path.lower()):
        return False

    return (
        len(endpoint_path) == len(prefix_path)
        or prefix_path.endswith("/")
        or endpoint_path[len(prefix_path)] == "/"
    )


def _port(parts: SplitResult) -> int:
    if parts.port is not None:
        return parts.port
    return 443 if parts.scheme.lower() == "https" else 80

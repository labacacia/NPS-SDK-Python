# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0
"""Parser and accessors for the ``bridge_target`` action parameter
(port of .NET ``BridgeTargetParser``)."""
from __future__ import annotations

from typing import Any

from nps_sdk.nwp.frames import ActionFrame
from nps_sdk.nwp.bridge.errors import BridgeDispatchException, BridgeErrorCodes
from nps_sdk.nwp.bridge.types import BridgeTarget

_MISSING = object()


def from_action_frame(frame: ActionFrame) -> BridgeTarget:
    """Parse ``params.bridge_target`` from an action frame."""
    if frame is None:
        raise BridgeDispatchException(BridgeErrorCodes.TARGET_INVALID, "params.bridge_target is required.")

    params = frame.params
    if params is None:
        raise BridgeDispatchException(BridgeErrorCodes.TARGET_INVALID, "params.bridge_target is required.")

    target = params
    if isinstance(params, dict) and "bridge_target" in params:
        target = params["bridge_target"]

    return from_json(target)


def from_json(target: Any) -> BridgeTarget:
    """Parse a ``bridge_target`` JSON object (dict)."""
    if not isinstance(target, dict):
        raise BridgeDispatchException(BridgeErrorCodes.TARGET_INVALID, "bridge_target must be an object.")

    protocol = _read_required_string(target, "protocol")
    endpoint = _read_required_string(target, "endpoint")

    extras: dict[str, Any] = {}
    for name, value in target.items():
        if name in ("protocol", "endpoint"):
            continue
        if name == "extras" and isinstance(value, dict):
            for k, v in value.items():
                extras[k] = v
            continue
        extras[name] = value

    return BridgeTarget(protocol=protocol, endpoint=endpoint, extras=extras or None)


def get_string(target: BridgeTarget, name: str, default: str | None = None) -> str | None:
    """Read a string-coerced extra from a target (case-insensitive key match)."""
    value = _get_extra(target, name)
    if value is _MISSING or value is None:
        return default
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        # Mirror .NET bool.TrueString / bool.FalseString casing.
        return "True" if value else "False"
    return str(value)


def try_get_json(target: BridgeTarget, name: str) -> tuple[bool, Any]:
    """Return ``(found, value)`` for an extra (case-insensitive key match)."""
    value = _get_extra(target, name)
    if value is _MISSING:
        return False, None
    return True, value


def _get_extra(target: BridgeTarget, name: str) -> Any:
    if target.extras is None:
        return _MISSING
    if name in target.extras:
        return target.extras[name]
    lowered = name.lower()
    for key, value in target.extras.items():
        if key.lower() == lowered:
            return value
    return _MISSING


def _read_required_string(obj: dict[str, Any], name: str) -> str:
    value = obj.get(name)
    if not isinstance(value, str) or not value.strip():
        raise BridgeDispatchException(
            BridgeErrorCodes.TARGET_INVALID, f"bridge_target.{name} is required."
        )
    return value

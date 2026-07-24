# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
NOP JSONPath input mapper (NPS-5 §3.1.3).

Resolves ``$.node_id.field.subfield`` expressions against a dictionary of
upstream node results and builds ``DelegateFrame.params`` objects from a
node's ``input_mapping``.

A "JSON value" here is a native Python object: dict, list, str, int/float,
bool, or None (as produced by JSON / MsgPack decoding).
"""

from __future__ import annotations

from typing import Any, Mapping

from nps_sdk.nop.constants import NopConstants
from nps_sdk.nop.error_codes import NOP_INPUT_MAPPING_ERROR


class NopMappingError(Exception):
    """Raised when an input-mapping path cannot be resolved (malformed / too deep)."""

    def __init__(self, message: str, error_code: str = NOP_INPUT_MAPPING_ERROR) -> None:
        super().__init__(message)
        self.error_code = error_code


# Sentinel distinguishing "resolved to null/None" from "path missing".
_MISSING = object()


def resolve(path: str, context: Mapping[str, Any]) -> Any:
    """
    Resolve a single JSONPath expression against the upstream result context.

    Returns the resolved value, or ``None`` when the path leads to a missing
    property. ``$`` alone returns the entire context as a dict.

    Raises:
        NopMappingError: for malformed paths or depth violations.
    """
    if not path or not path.strip():
        raise NopMappingError("Input mapping path must not be empty.")

    if not path.startswith("$."):
        raise NopMappingError(
            f"Input mapping path must start with '$.' — got: {path}"
        )

    # Split: "$", "node_id", "field", "sub", ... (drop empty segments)
    parts = [p for p in path.split(".") if p != ""]
    # parts[0] == "$"

    if len(parts) > NopConstants.MAX_INPUT_MAPPING_DEPTH + 1:
        raise NopMappingError(
            f"Input mapping path depth {len(parts) - 1} exceeds maximum "
            f"{NopConstants.MAX_INPUT_MAPPING_DEPTH}: {path}"
        )

    if len(parts) == 1:
        # Just "$" -> the entire context object
        return dict(context)

    node_id = parts[1]
    if node_id not in context:
        return None

    current: Any = context[node_id]
    if len(parts) == 2:
        return current  # "$.node_id" -> full result

    for i in range(2, len(parts)):
        if not isinstance(current, Mapping):
            return None
        if parts[i] not in current:
            return None
        current = current[parts[i]]
    return current


def build_params(
    input_mapping: Mapping[str, Any] | None,
    context: Mapping[str, Any],
) -> dict[str, Any]:
    """
    Build an operation ``params`` object by resolving all ``input_mapping``
    entries against the upstream result context.

    Each mapping value may be a string JSONPath, a list of JSONPaths, or a
    literal (returned verbatim).
    """
    if not input_mapping:
        return {}

    out: dict[str, Any] = {}
    for param_name, spec in input_mapping.items():
        if isinstance(spec, str):
            out[param_name] = resolve(spec, context)
        elif isinstance(spec, (list, tuple)):
            out[param_name] = [
                resolve(p, context) if isinstance(p, str) else p for p in spec
            ]
        else:
            out[param_name] = spec
    return out

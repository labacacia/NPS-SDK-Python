# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0
"""Bridge error codes and dispatch exception (port of .NET ``BridgeErrorCodes`` /
``BridgeDispatchException``)."""
from __future__ import annotations


class BridgeErrorCodes:
    """NWP error codes used by Bridge dispatchers and inbound server adapters."""

    #: The invocation does not contain a valid ``bridge_target``.
    TARGET_INVALID = "NWP-BRIDGE-TARGET-INVALID"
    #: The requested bridge protocol has no registered dispatcher.
    PROTOCOL_UNSUPPORTED = "NWP-BRIDGE-PROTOCOL-UNSUPPORTED"
    #: The target endpoint is invalid or disallowed.
    ENDPOINT_INVALID = "NWP-BRIDGE-ENDPOINT-INVALID"
    #: The external call failed or returned an unusable response.
    UPSTREAM_FAILED = "NWP-BRIDGE-UPSTREAM-FAILED"
    #: An inbound Bridge server request named a tool/action that is not exposed.
    SERVER_TOOL_NOT_FOUND = "NWP-BRIDGE-SERVER-TOOL-NOT-FOUND"
    #: An inbound Bridge server was not configured with a local action dispatcher.
    SERVER_DISPATCHER_MISSING = "NWP-BRIDGE-SERVER-DISPATCHER-MISSING"
    #: An inbound Bridge server local action dispatch failed unexpectedly.
    SERVER_DISPATCH_FAILED = "NWP-BRIDGE-SERVER-DISPATCH-FAILED"


class BridgeDispatchException(Exception):
    """Raised when a Bridge Node cannot parse, route, or execute a bridge
    invocation. ``error_code`` carries the NWP-compatible failure code."""

    def __init__(self, error_code: str, message: str, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        if cause is not None:
            self.__cause__ = cause

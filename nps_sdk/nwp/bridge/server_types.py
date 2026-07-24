# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0
"""Protocol version / state constants for inbound MCP and A2A Bridge servers
(port of .NET ``McpServerTypes`` / ``A2aServerTypes``). Wire objects are built
as plain dicts by the server bridges to match .NET JSON field names exactly."""
from __future__ import annotations


class McpServerProtocol:
    """MCP protocol version implemented by the Bridge server adapter."""

    VERSION = "2024-11-05"


class A2aServerProtocol:
    """A2A protocol version implemented by the Bridge server adapter."""

    VERSION = "0.2"


class A2aTaskState:
    COMPLETED = "completed"
    FAILED = "failed"

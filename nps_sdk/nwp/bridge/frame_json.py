# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0
"""Frame → JSON helpers for inbound Bridge server adapters
(port of .NET ``BridgeFrameJson``)."""
from __future__ import annotations

import json
from typing import Any


def to_element(frame: Any) -> dict[str, Any]:
    """Serialize an NPS frame to a plain JSON-compatible dict."""
    if hasattr(frame, "to_dict"):
        return frame.to_dict()
    return dict(frame)


def serialize(frame: Any) -> str:
    """Serialize an NPS frame to a compact JSON string."""
    return json.dumps(to_element(frame), separators=(",", ":"), ensure_ascii=False)

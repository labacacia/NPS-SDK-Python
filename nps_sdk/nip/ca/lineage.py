# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
Re-export of the signed lineage types for orchestrator groups / session NIDs
(NPS-CR-0003 §5.1.3).

The canonical definitions live in :mod:`nps_sdk.nip.frames` alongside
``IdentFrame`` (mirroring the .NET reference, which colocates ``IdentLineage``
with ``IdentFrame``). This module re-exports them under the ``nip.ca`` namespace
for ergonomic CA-side imports.
"""

from nps_sdk.nip.frames import IdentLineage, IdentLineageRole

__all__ = ["IdentLineage", "IdentLineageRole"]

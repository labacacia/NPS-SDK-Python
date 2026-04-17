# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""NPS SDK exception hierarchy."""


class NpsError(Exception):
    """Base exception for all NPS SDK errors."""


class NpsFrameError(NpsError):
    """Raised when a frame cannot be parsed or is structurally invalid."""


class NpsCodecError(NpsError):
    """Raised when a frame cannot be encoded or decoded."""


class NpsAnchorNotFoundError(NpsError):
    """Raised when an anchor_id is referenced but not present in the cache."""

    def __init__(self, anchor_id: str) -> None:
        super().__init__(f"AnchorFrame not found in cache: {anchor_id!r}")
        self.anchor_id = anchor_id


class NpsAnchorPoisonError(NpsError):
    """
    Raised when an AnchorFrame with an already-cached anchor_id arrives
    with a different schema (NPS-1 §7.2 — anchor poisoning protection).
    """

    def __init__(self, anchor_id: str) -> None:
        super().__init__(
            f"Anchor poisoning detected for {anchor_id!r}: "
            "incoming schema does not match the cached schema."
        )
        self.anchor_id = anchor_id

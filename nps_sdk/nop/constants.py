# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""Protocol-level NOP limits (NPS-5 §8.2)."""

from __future__ import annotations


class NopConstants:
    """Protocol-level limits defined by NPS-5 §8.2."""

    #: Maximum number of nodes in a single DAG.
    MAX_DAG_NODES = 32

    #: Maximum delegation chain depth (Orchestrator -> Worker -> Sub-Worker).
    MAX_DELEGATE_CHAIN_DEPTH = 3

    #: Maximum length of a CEL condition expression in characters.
    MAX_CONDITION_LENGTH = 512

    #: Maximum JSONPath nesting depth in input_mapping values.
    MAX_INPUT_MAPPING_DEPTH = 8

    #: Default task timeout in milliseconds.
    DEFAULT_TIMEOUT_MS = 30_000

    #: Maximum task timeout in milliseconds (1 hour).
    MAX_TIMEOUT_MS = 3_600_000

    #: Default AnchorFrame TTL in seconds.
    DEFAULT_ANCHOR_TTL = 3_600

    #: Maximum number of callback POST attempts with exponential backoff (NPS-5 §8.4).
    CALLBACK_MAX_RETRIES = 3

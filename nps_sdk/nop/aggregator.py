# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
Result aggregation strategies for end-node / sync results (NPS-5 §3.3.2).

Results are native Python JSON values (dict / list / scalars).
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from nps_sdk.nop.models import AggregateStrategy


def merge(results: Sequence[Any]) -> dict[str, Any]:
    """
    Merge all object results into one (last-write-wins on key conflicts).
    Non-object results are added under ``_result_{i}`` keys.
    """
    merged: dict[str, Any] = {}
    for i, result in enumerate(results):
        if isinstance(result, Mapping):
            for k, v in result.items():
                merged[k] = v
        else:
            merged[f"_result_{i}"] = result
    return merged


def build_array(results: Sequence[Any]) -> list[Any]:
    """Return all results as a list."""
    return list(results)


def aggregate(
    strategy: str,
    results: Sequence[Any],
    min_required: int = 0,
) -> Any:
    """
    Aggregate ``results`` using ``strategy``.

    ``min_required`` is honoured only by ``fastest_k`` (number of results to keep).
    """
    if len(results) == 0:
        return {}

    if strategy == AggregateStrategy.FIRST:
        return results[0]
    if strategy == AggregateStrategy.ALL:
        return build_array(results)
    if strategy == AggregateStrategy.FASTEST_K:
        k = min_required if min_required > 0 else len(results)
        return build_array(list(results)[:k])
    # "merge", "merge_all", and default
    return merge(results)


def aggregate_end_nodes(
    end_node_ids: Sequence[str],
    all_results: Mapping[str, Any],
    strategy: str = AggregateStrategy.MERGE,
) -> Any:
    """Filter ``all_results`` to end nodes (in the given order) then aggregate."""
    end_results = [all_results[nid] for nid in end_node_ids if nid in all_results]
    return aggregate(strategy, end_results)

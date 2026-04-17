# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
NopClient — async HTTP client for submitting NOP tasks to a Gateway Node.

An Agent uses NopClient to:
  1. Submit a TaskFrame to a Gateway Node's /task endpoint.
  2. Poll for task status.
  3. Cancel a running task.
  4. Await completion (polling loop with configurable interval and timeout).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from nps_sdk.core.codec import NpsFrameCodec
from nps_sdk.core.frames import EncodingTier
from nps_sdk.core.registry import FrameRegistry
from nps_sdk.nop.frames import TaskFrame
from nps_sdk.nop.models import TaskState


_CONTENT_TYPE = "application/x-nps-frame"
_ACCEPT       = "application/x-nps-frame"

# Terminal states — polling stops when the task reaches one of these.
_TERMINAL_STATES = {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED}


class NopTaskStatus:
    """Parsed status response from a NOP gateway."""

    def __init__(self, raw: dict[str, Any]) -> None:
        self._raw = raw

    @property
    def task_id(self) -> str:
        return self._raw["task_id"]

    @property
    def state(self) -> TaskState:
        return TaskState(self._raw["state"])

    @property
    def is_terminal(self) -> bool:
        return self.state in _TERMINAL_STATES

    @property
    def aggregated_result(self) -> Any:
        return self._raw.get("aggregated_result")

    @property
    def error_code(self) -> str | None:
        return self._raw.get("error_code")

    @property
    def error_message(self) -> str | None:
        return self._raw.get("error_message")

    @property
    def node_results(self) -> dict[str, Any]:
        return self._raw.get("node_results", {})

    @property
    def raw(self) -> dict[str, Any]:
        return self._raw

    def __repr__(self) -> str:
        return f"NopTaskStatus(task_id={self.task_id!r}, state={self.state!r})"


class NopClient:
    """
    Async HTTP client for NOP Gateway Nodes (NPS-5).

    Usage::

        async with NopClient("https://gateway.example.com") as client:
            task_id = await client.submit(task_frame)
            status  = await client.wait(task_id, timeout=60.0)
            if status.state == TaskState.COMPLETED:
                print(status.aggregated_result)
    """

    def __init__(
        self,
        base_url: str,
        *,
        default_tier: EncodingTier = EncodingTier.MSGPACK,
        timeout: float = 30.0,
        http_client: httpx.AsyncClient | None = None,
        registry: FrameRegistry | None = None,
    ) -> None:
        self._base_url    = base_url.rstrip("/")
        self._tier        = default_tier
        self._registry    = registry or FrameRegistry.create_full()
        self._codec       = NpsFrameCodec(self._registry)
        self._timeout     = timeout
        self._owns_client = http_client is None
        self._http        = http_client or httpx.AsyncClient(timeout=timeout)

    # ── Context manager ───────────────────────────────────────────────────────

    async def __aenter__(self) -> "NopClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._owns_client:
            await self._http.aclose()

    # ── Public API ────────────────────────────────────────────────────────────

    async def submit(self, frame: TaskFrame) -> str:
        """
        Submit a TaskFrame to the gateway's /task endpoint.

        Returns:
            The task_id string from the gateway response.

        Raises:
            httpx.HTTPStatusError: on HTTP 4xx/5xx.
            KeyError: if the response JSON does not contain 'task_id'.
        """
        wire = self._codec.encode(frame, override_tier=self._tier)
        response = await self._http.post(
            f"{self._base_url}/task",
            content=wire,
            headers={"Content-Type": _CONTENT_TYPE, "Accept": "application/json"},
        )
        response.raise_for_status()
        data = response.json()
        return data["task_id"]

    async def get_status(self, task_id: str) -> NopTaskStatus:
        """
        Poll the gateway for the current status of a task.

        Returns:
            NopTaskStatus wrapping the gateway's JSON response.

        Raises:
            httpx.HTTPStatusError: on HTTP 4xx/5xx.
        """
        response = await self._http.get(
            f"{self._base_url}/task/{task_id}",
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        return NopTaskStatus(response.json())

    async def cancel(self, task_id: str) -> None:
        """
        Request cancellation of a running task.

        Raises:
            httpx.HTTPStatusError: on HTTP 4xx/5xx.
        """
        response = await self._http.post(
            f"{self._base_url}/task/{task_id}/cancel",
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()

    async def wait(
        self,
        task_id: str,
        *,
        poll_interval: float = 1.0,
        timeout: float = 30.0,
    ) -> NopTaskStatus:
        """
        Poll until the task reaches a terminal state or *timeout* seconds elapse.

        Args:
            task_id:       The task to poll.
            poll_interval: Seconds between status requests.
            timeout:       Maximum total seconds to wait.

        Returns:
            The final NopTaskStatus (may be Completed, Failed, or Cancelled).

        Raises:
            asyncio.TimeoutError: if the task does not complete within *timeout*.
        """
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            status = await self.get_status(task_id)
            if status.is_terminal:
                return status
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise asyncio.TimeoutError(
                    f"Task '{task_id}' did not complete within {timeout}s "
                    f"(current state: {status.state!r})."
                )
            await asyncio.sleep(min(poll_interval, remaining))

    async def close(self) -> None:
        """Explicitly close the underlying HTTP client."""
        if self._owns_client:
            await self._http.aclose()

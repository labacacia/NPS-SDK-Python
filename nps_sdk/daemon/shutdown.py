# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
Graceful shutdown coordination (port of ``GracefulShutdown`` + ``ShutdownState``).

Provides a portable async coordinator that:

  * flips a liveness gate the moment a shutdown signal arrives so ``/healthz``
    can start failing before the listener closes (load-balancer drain);
  * runs registered async drain hooks with a bounded drain timeout
    (default 30s, matching the .NET reference / NPS-Dev #45);
  * optionally installs SIGTERM/SIGINT handlers on the running event loop.
"""

from __future__ import annotations

import asyncio
import signal
from typing import Awaitable, Callable

#: Default drain timeout for NPS daemons (NPS-Dev #45).
DEFAULT_DRAIN_TIMEOUT_SECONDS = 30.0


class ShutdownState:
    """Liveness flag flipped on shutdown; read by health probes."""

    def __init__(self) -> None:
        self._stopping = False

    @property
    def is_stopping(self) -> bool:
        return self._stopping

    def mark_stopping(self) -> None:
        self._stopping = True


DrainHook = Callable[[], Awaitable[None]]


class GracefulShutdown:
    """Coordinates ordered drain of a daemon on shutdown.

    Register drain hooks with :meth:`on_shutdown`; call :meth:`trigger` (or wire
    :meth:`install_signal_handlers`) to mark the state stopping and run every
    hook within :attr:`drain_timeout`. :attr:`state` exposes the liveness gate
    for a readiness probe.
    """

    def __init__(self, drain_timeout: float = DEFAULT_DRAIN_TIMEOUT_SECONDS) -> None:
        self.drain_timeout = drain_timeout
        self.state = ShutdownState()
        self._hooks: list[DrainHook] = []
        self._done = asyncio.Event()

    def on_shutdown(self, hook: DrainHook) -> None:
        """Register an async drain hook, run in registration order on shutdown."""
        self._hooks.append(hook)

    async def trigger(self) -> None:
        """Mark stopping and run all drain hooks, bounded by the drain timeout.

        Idempotent: a second call is a no-op once drain has completed.
        """
        if self.state.is_stopping:
            await self._done.wait()
            return
        self.state.mark_stopping()
        try:
            await asyncio.wait_for(self._run_hooks(), timeout=self.drain_timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            self._done.set()

    async def _run_hooks(self) -> None:
        for hook in self._hooks:
            await hook()

    def install_signal_handlers(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Install SIGTERM/SIGINT handlers that schedule :meth:`trigger`."""
        loop = loop or asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, lambda: asyncio.ensure_future(self.trigger()))
            except (NotImplementedError, ValueError):
                # add_signal_handler is unavailable on some platforms / threads.
                pass

    async def wait_for_shutdown(self) -> None:
        """Await completion of a triggered drain."""
        await self._done.wait()

# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
NOP orchestration instrumentation (port of ``NopInstrumentation`` +
``NopTelemetry``).

Instrument names match the .NET reference exactly:

  * ``nps.nop.task.duration_ms``  histogram  ms
  * ``nps.nop.node.duration_ms``  histogram  ms
  * ``nps.nop.node.retries``      counter    {retries}
  * ``nps.nop.tasks.completed``   counter    {tasks}
  * ``nps.nop.tasks.failed``      counter    {tasks}
"""

from __future__ import annotations

from nps_sdk.core.telemetry import Meter, Tracer

#: ActivitySource / Meter name for the NOP layer (matches .NET).
ACTIVITY_SOURCE_NAME = "nps.nop"
METER_NAME = "nps.nop"
VERSION = "1.0.0"

source = Tracer(ACTIVITY_SOURCE_NAME)
meter = Meter(METER_NAME, VERSION)

task_duration_ms = meter.create_histogram(
    "nps.nop.task.duration_ms", unit="ms",
    description="NOP task total execution duration")
node_duration_ms = meter.create_histogram(
    "nps.nop.node.duration_ms", unit="ms",
    description="NOP DAG node execution duration")
node_retries = meter.create_counter(
    "nps.nop.node.retries", unit="{retries}",
    description="NOP DAG node retry attempts")
tasks_completed = meter.create_counter(
    "nps.nop.tasks.completed", unit="{tasks}",
    description="NOP tasks completed successfully")
tasks_failed = meter.create_counter(
    "nps.nop.tasks.failed", unit="{tasks}",
    description="NOP tasks that failed or timed out")


def reset() -> None:
    """Reset all NOP instruments and recorded spans (test aid)."""
    meter.reset()
    source.reset()

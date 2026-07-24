# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
NWP frame-processing instrumentation (port of ``NwpInstrumentation`` +
``NwpTelemetry``).

Instrument names match the .NET reference exactly so cross-language metric
identities are preserved:

  * ``nps.frames.processed``      counter    {frames}
  * ``nps.frames.processing_ms``  histogram  ms
  * ``nps.cgn.consumed``          counter    {cgn}
  * ``nps.frames.errors``         counter    {frames}
"""

from __future__ import annotations

from nps_sdk.core.telemetry import Meter, Tracer

#: ActivitySource / Meter name for the NWP layer (matches .NET).
ACTIVITY_SOURCE_NAME = "nps.nwp"
METER_NAME = "nps.nwp"
VERSION = "1.0.0"

#: Module-level source + meter. All call sites share these singletons so a
#: single reader/snapshot sees every recorded value.
source = Tracer(ACTIVITY_SOURCE_NAME)
meter = Meter(METER_NAME, VERSION)

frames_processed = meter.create_counter(
    "nps.frames.processed", unit="{frames}",
    description="Total NWP frames processed")
frame_duration_ms = meter.create_histogram(
    "nps.frames.processing_ms", unit="ms",
    description="NWP frame processing duration")
cgn_consumed = meter.create_counter(
    "nps.cgn.consumed", unit="{cgn}",
    description="CGN units consumed in NWP responses")
frame_errors = meter.create_counter(
    "nps.frames.errors", unit="{frames}",
    description="NWP frames that returned an error response")


def reset() -> None:
    """Reset all NWP instruments and recorded spans (test aid)."""
    meter.reset()
    source.reset()

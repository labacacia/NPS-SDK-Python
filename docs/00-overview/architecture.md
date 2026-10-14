# Architecture

## Purpose and lifecycle

Public Python release projection; canonical source is in NPS-Dev.

## Boundaries

nps_sdk/: package.

tests/: tests.

spec/: released snapshot.

doc/: documentation.

nip-ca-server/: development integration.

Keep responsibilities and dependency direction explicit. Do not add layers or cross-repository sources of truth without a concrete requirement. Record architecture-significant decisions under `docs/04-decisions/`.

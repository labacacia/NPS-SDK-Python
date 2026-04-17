# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""NPS SDK — Python client library for the Neural Protocol Suite."""

from importlib.metadata import version, PackageNotFoundError

try:
    __version__: str = version("nps-sdk")
except PackageNotFoundError:
    __version__ = "0.1.0"

__all__ = ["__version__"]

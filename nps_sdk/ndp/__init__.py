# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""NPS.NDP — Neural Discovery Protocol: node announcement, resolution, graph sync."""

from nps_sdk.ndp.dns_txt import (
    DNS_TXT_DEFAULT_TTL,
    DnsTxtLookup,
    SystemDnsTxtLookup,
    parse_nps_txt_record,
)
from nps_sdk.ndp.frames import (
    AnnounceFrame,
    GraphFrame,
    NdpAddress,
    NdpGraphEdge,
    NdpGraphNode,
    NdpResolveResult,
    ResolveFrame,
)
from nps_sdk.ndp.federation import (
    MAX_FEDERATION_HOPS,
    NDP_FORWARDED_BY_HEADER,
    NdpFederationLoopError,
    append_forwarded_by,
    parse_forwarded_by,
)
from nps_sdk.ndp.cluster import (
    DEFAULT_CLUSTER_EPOCH,
    NdpClusterResolutionMixin,
    NdpClusterSplitError,
    effective_cluster_epoch,
    resolve_cluster_from,
)
from nps_sdk.ndp.security import SecurityProfile
from nps_sdk.ndp.registry import InMemoryNdpRegistry
from nps_sdk.ndp.registry_profile import (
    NdpClusterSelection,
    NdpRegistryAdmission,
    NdpRegistryDecision,
    NdpRegistryProfile,
    canonical_announce_json,
    verify_announce_signature,
)
from nps_sdk.ndp.validator import NdpAnnounceResult, NdpAnnounceValidator
from nps_sdk.ndp import error_codes
from nps_sdk.ndp.error_codes import NDP_ERROR_TO_NPS_STATUS

__all__ = [
    "AnnounceFrame",
    "DNS_TXT_DEFAULT_TTL",
    "DnsTxtLookup",
    "GraphFrame",
    "NdpAddress",
    "NdpGraphEdge",
    "NdpGraphNode",
    "NdpResolveResult",
    "ResolveFrame",
    "MAX_FEDERATION_HOPS",
    "NDP_FORWARDED_BY_HEADER",
    "NdpFederationLoopError",
    "append_forwarded_by",
    "parse_forwarded_by",
    "InMemoryNdpRegistry",
    "NdpClusterSelection",
    "NdpRegistryAdmission",
    "NdpRegistryDecision",
    "NdpRegistryProfile",
    # Multi-Anchor HA cluster resolution (NPS-CR-0009)
    "DEFAULT_CLUSTER_EPOCH",
    "NdpClusterResolutionMixin",
    "NdpClusterSplitError",
    "effective_cluster_epoch",
    "resolve_cluster_from",
    "NdpAnnounceResult",
    "NdpAnnounceValidator",
    "SecurityProfile",
    "SystemDnsTxtLookup",
    "parse_nps_txt_record",
    "canonical_announce_json",
    "verify_announce_signature",
    "error_codes",
    "NDP_ERROR_TO_NPS_STATUS",
]

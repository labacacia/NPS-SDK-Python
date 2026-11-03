English | [中文](RESULTS.cn.md)

# Experimental public result HTTP client

`ResultHttpClient` explicitly selects data-results revision1 on one host-configured
HTTPS route. It supplies create/read/aggregate/release with immutable descriptor
fences, affirmative discovery and bounded responses. Development source only: this
new surface is not a claim that the publishedalpha.19 package contains it. D20-05's
.NET/Python integration is IN_PROGRESS; six-SDK qualification remains D20-08.

## Entry and ownership

Configure actual TLS/mTLS trust on a caller-owned `httpx.AsyncClient`, then pass its
pinned public gateway route and expected result issuer. Authentication, current NIP/
resource/field policy and model egress belong to the host. A result reference and a
model-provided subject/issuer hint never grant access. This public leg requests only
`nps.data_results=1`; the gateway owns its separate authenticated downstream projection.
No native result route, result subscription or default feature offer is installed.

```python
import ssl
import uuid
import httpx
from nps_sdk.nwp import ResultHttpClient

# Operator-provisioned local files; do not derive these from model parameters.
trust = ssl.create_default_context(cafile="demo-ca.pem")
trust.load_cert_chain("owner.pem", "owner-key.pem")
async with httpx.AsyncClient(verify=trust, trust_env=False) as http:
    async with ResultHttpClient("https://node.example/gateway",
                                "urn:nps:node:example:results", http_client=http) as results:
        logical_key = uuid.uuid4()
        captured = await results.create("orders", logical_key, ttl_seconds=300)
        try:
            aggregate = await results.aggregate(captured, ["reason"], sum_field="amount_minor")
            page = await results.read(captured, 5, fields=["id", "reason", "amount_minor"])
            # The application separately enforces its cumulative inspection/model budget.
        finally:
            await results.release(captured)
```

Closing the result client closes only its owned transport. Supplied transports stay
caller-owned. Closing connections does not release server results. The store's process
retention/expiry and current source policy still apply; restart/endpoint replacement
does not replicate or recreate a lost result.

## Request and response boundaries

Before every POST, require a pinned affirmative versioned NWM, exact issuer/route/
auth/capability and result revision1. Reject rollback/same-version content changes.
No redirects, semantic rewriting, fallback, automatic retry or pagination. Creation
requires UUID-v4 and only source/filter/fields/order/TTL parameters. The server owns
source allowlists and query validation. Requests stay within64KiB and the profile's
integer-only JSON subset; local unsupported values fail before network dispatch.

Reads use exact issuer/result/snapshot/schema descriptors, limit1..1000 and explicit
optional fields/cursor. COUNT and optional integer SUM use at most4group fields.
Descriptor equality, primitive types, capsule/selected-feature echo and decompressed
body/page/group limits are checked before results return. Duplicate JSON members,
unsafe integers/floats/surrogates and malformed authority/discovery data fail closed.
The returned page is bounded; cumulative inspected bytes/rows/turns remain the
application's responsibility. No preview rows are emitted by create.

`ResultHttpError` exposes bounded status/code and `outcome_unknown`, without raw
server bodies or credentials. A failed discovery sends no operation. Transport/
malformed success after possible POST may have allocated a result; explicitly recover
creation with the SAME logical key/parameters under fresh current authorization.
Cancellation propagates and does not prove rollback. The client never silently tries
another key, issuer or endpoint. Release is explicit and owner-authorized.

## Verification and limits

MockTransport tests exercise wire contracts, zero-POST discovery rejection, wrong
bindings, limits, errors, cancellation and ownership. They do not establish real
transport identity or deployed server qualification. That evidence belongs to
[D20-05](../../../../docs/plans/2026-11-02-data-workflow-demo-implementation.md).
The existing .NET [reference AC](../../../../docs/plans/2026-11-02-result-profile-ac-evidence.md)
and [result contract](../../../../spec/profiles/data-results.md) define dependencies.
No paid calls occur in these client tests.

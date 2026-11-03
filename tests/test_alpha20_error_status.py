import json
from .conformance_fixtures import conformance_fixture
import pytest
from nps_sdk.ncp import error_codes as ncp
from nps_sdk.nwp import error_codes as nwp
from nps_sdk.nip import error_codes as nip
from nps_sdk.nop import error_codes as nop
from nps_sdk.core.status_codes import to_http_status

CASES = json.loads(conformance_fixture("alpha20", "error_status_vectors.json").read_text())["cases"]
assert len(CASES) == 17


@pytest.mark.parametrize("vector", CASES, ids=lambda v: v["code"])
def test_alpha20_error_status(vector):
    protocol = vector["code"][:3]
    module = {"NCP": ncp, "NWP": nwp, "NIP": nip, "NOP": nop}[protocol]
    assert vector["code"] in vars(module).values()
    status = getattr(module, protocol + "_ERROR_TO_NPS_STATUS")[vector["code"]]
    assert status == vector["status"]
    assert to_http_status(status) == vector["http"]

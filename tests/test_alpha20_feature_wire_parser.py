from dataclasses import FrozenInstanceError
import json
from .conformance_fixtures import conformance_fixture

import pytest
from nps_sdk.alpha20.feature_wire_parser import (
    FeatureFormatError, parse_feature_offer, parse_required_feature_header,
)

CASES = json.loads(conformance_fixture("alpha20", "feature_negotiation_vectors.json").read_text())["cases"]


@pytest.mark.parametrize("vector", CASES, ids=lambda v: v["id"])
def test_shared_parser_corpus(vector):
    expected = vector["expected"]
    native = vector["kind"] == "native-offer"
    def parse():
        return parse_feature_offer(vector["raw"]) if native else parse_required_feature_header(vector["headers"])
    if expected.get("error") in ("NCP-FEATURE-NEGOTIATION-INVALID", "NWP-FEATURE-REQUIREMENTS-INVALID"):
        with pytest.raises(FeatureFormatError) as error:
            parse()
        assert error.value.code == expected["error"]
    elif native:
        # Selection and local eligibility are not parsing responsibilities.
        parsed = parse()
        raw = json.loads(vector["raw"])
        assert {key: [vars(rev) for rev in values] for key, values in parsed.features.items()} == {
            value["id"]: value["revisions"] for value in raw.get("feature_offer", [])}
        assert parsed.required == tuple(raw.get("required_features", []))
    else:
        assert [vars(value) for value in parse()] == expected["required"]


@pytest.mark.parametrize("revision", ["1.0", "1e0", "-1", "null", "true", "NaN", "Infinity"])
def test_numeric_spelling(revision):
    with pytest.raises(FeatureFormatError):
        parse_feature_offer('{"feature_negotiation":1,"feature_offer":[{"id":"example.echo",'
            '"revisions":[{"revision":' + revision + ',"lifecycle":"stable"}]}]}')


@pytest.mark.parametrize("raw", ['{"feature_negotiation":1,"feature_\\u006eegotiation":1}',
    '{"feature_negotiation":true}', '{"feature_negotiation":1,"required_features":["example.echo\\ud800"]}',
    '{"feature_negotiation":1,"required_features":["example.echo\ud800"]}', '[' * 2000 + ']' * 2000])
def test_raw_edge_cases(raw):
    with pytest.raises(FeatureFormatError):
        parse_feature_offer(raw)


def test_immutable():
    value = parse_feature_offer('{"feature_negotiation":1,"feature_offer":[{"id":"example.echo",'
        '"revisions":[{"revision":1,"lifecycle":"stable"}]}]}')
    with pytest.raises(TypeError):
        value.features["other.id"] = ()
    with pytest.raises(FrozenInstanceError):
        value.features["example.echo"][0].revision = 2
    with pytest.raises(FrozenInstanceError):
        value.required = ("other.id",)


@pytest.mark.parametrize("raw", ["example.echo=1\n", "example.echo\r=1", "example.echo=1\u2028"])
def test_header_line_terminators(raw):
    with pytest.raises(FeatureFormatError):
        parse_required_feature_header([raw])

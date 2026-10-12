import pytest

from a4w_core.errors import SpecInvalid, UnsupportedSpecSource
from a4w_core.spec_parsers import JsonSpecParser, YamlSpecParser


def test_parses_openapi_yaml():
    doc = YamlSpecParser().parse(b"openapi: 3.1.0\ninfo: {title: t, version: '1'}\npaths: {}\n")
    assert doc["openapi"] == "3.1.0" and doc["paths"] == {}


def test_dates_stay_strings():
    doc = YamlSpecParser().parse(b"openapi: 3.1.0\npaths: {}\ninfo: {version: 2026-10-12}\n")
    assert doc["info"]["version"] == "2026-10-12"


def test_mapping_keys_become_strings():
    doc = YamlSpecParser().parse(b"openapi: 3.1.0\npaths:\n  /a:\n    get:\n      responses:\n        200: {description: ok}\n")
    assert list(doc["paths"]["/a"]["get"]["responses"]) == ["200"]


@pytest.mark.parametrize("raw", [b": : :\n  - [", b"- a\n- b\n", b"openapi: 2.0\npaths: {}\n", b"openapi: 3.1.0\n", b"\xff\xfe"])
def test_rejects_non_openapi3(raw):
    with pytest.raises(SpecInvalid):
        YamlSpecParser().parse(raw)


def test_json_parser_is_placeholder():
    with pytest.raises(UnsupportedSpecSource):
        JsonSpecParser().parse(b"{}")

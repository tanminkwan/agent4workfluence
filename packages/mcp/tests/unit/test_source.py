"""Manufacture 조회 client (Phase 3 설계 6.1절)."""

from __future__ import annotations

import httpx
import pytest

from a4w_mcp.source import ManufactureToolsetSource, ToolsetMissing, ToolsetUnavailable

from ..fakes import TARGET, FakeManufacture, bundle


def source(fake, token=None):
    return ManufactureToolsetSource(base_url="http://m:1", token=token, timeout=5,
                                    transport=httpx.MockTransport(fake))


def test_current_version_then_304_with_etag():
    fake = FakeManufacture(bundle())
    s = source(fake)
    first = s.current_version(TARGET, None)
    assert first.tool_version == "tv-000001-aaaaaaaa" and first.etag == '"tv-000001-aaaaaaaa"'
    again = s.current_version(TARGET, first.etag)
    assert again.tool_version is None and again.etag == first.etag
    assert fake.requests[-1].headers["if-none-match"] == first.etag


def test_fetch_by_version():
    fake = FakeManufacture(bundle())
    b = source(fake).fetch(TARGET, "tv-000001-aaaaaaaa")
    assert b["toolVersion"] == "tv-000001-aaaaaaaa"
    assert fake.requests[-1].url.path == f"/v1/targets/{TARGET}/toolsets/tv-000001-aaaaaaaa"


def test_auth_header_only_when_token():
    fake = FakeManufacture(bundle())
    source(fake).current_version(TARGET, None)
    assert "authorization" not in fake.requests[-1].headers
    source(fake, token="mf-token").current_version(TARGET, None)
    assert fake.requests[-1].headers["authorization"] == "Bearer mf-token"


def test_no_version_yet_is_missing():
    with pytest.raises(ToolsetMissing):
        source(FakeManufacture()).current_version(TARGET, None)


@pytest.mark.parametrize("status", [500, 502, 401, 404])
def test_failures_are_unavailable(status):
    fake = FakeManufacture(bundle())
    fake.fail = status
    with pytest.raises(ToolsetUnavailable):
        source(fake).current_version(TARGET, None)


def test_connect_error_is_unavailable():
    def boom(req):
        raise httpx.ConnectError("x")

    with pytest.raises(ToolsetUnavailable):
        source(boom).fetch(TARGET, "tv-000001-aaaaaaaa")


def test_non_json_is_unavailable():
    with pytest.raises(ToolsetUnavailable):
        source(lambda req: httpx.Response(200, text="<html>")).fetch(TARGET, "tv-000001-aaaaaaaa")

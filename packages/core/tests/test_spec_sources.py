import hashlib

import httpx
import pytest

from a4w_core.errors import SpecFetchFailed, UnsupportedSpecSource
from a4w_core.spec_sources import HashUrlSpecSource, NoHashSpecSource

SPEC = b"openapi: 3.1.0\npaths: {}\n"
HASH = hashlib.sha256(SPEC).hexdigest()


def source(handler, max_bytes=1000):
    return HashUrlSpecSource(
        spec_url="https://t/openapi.yaml",
        hash_url="https://t/openapi.sha256",
        max_bytes=max_bytes,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def handler_ok(request):
    if request.url.path.endswith(".sha256"):
        return httpx.Response(200, text=HASH.upper() + "\n")
    return httpx.Response(200, content=SPEC)


def test_fetch_hash_is_normalized_lowercase():
    assert source(handler_ok).fetch_hash() == HASH


def test_fetch_spec_returns_bytes_as_is():
    assert source(handler_ok).fetch_spec() == SPEC


@pytest.mark.parametrize("body", ["not-a-hash", "abc", HASH + "00"])
def test_bad_hash_body_fails(body):
    src = source(lambda r: httpx.Response(200, text=body))
    with pytest.raises(SpecFetchFailed):
        src.fetch_hash()


@pytest.mark.parametrize("status", [404, 500, 302])
def test_non_2xx_fails(status):
    src = source(lambda r: httpx.Response(status, text=""))
    with pytest.raises(SpecFetchFailed):
        src.fetch_spec()
    with pytest.raises(SpecFetchFailed):
        src.fetch_hash()


def test_network_error_fails():
    def boom(request):
        raise httpx.ConnectError("refused", request=request)
    with pytest.raises(SpecFetchFailed):
        source(boom).fetch_spec()


def test_oversized_spec_fails():
    src = source(lambda r: httpx.Response(200, content=b"x" * 2000), max_bytes=1000)
    with pytest.raises(SpecFetchFailed):
        src.fetch_spec()


def test_error_message_does_not_contain_body():
    secret = "SECRET-BODY"
    src = source(lambda r: httpx.Response(500, text=secret))
    with pytest.raises(SpecFetchFailed) as e:
        src.fetch_spec()
    assert secret not in str(e.value)


def test_no_hash_source_is_placeholder():
    src = NoHashSpecSource()
    with pytest.raises(UnsupportedSpecSource):
        src.fetch_hash()
    with pytest.raises(UnsupportedSpecSource):
        src.fetch_spec()

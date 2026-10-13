import base64
import json

import pytest

from a4w_core.errors import ScopeUnknown
from a4w_target_workfluence.scopes import WorkfluenceJwtScopeProvider


def jwt(payload):
    def enc(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    return f"{enc({'alg': 'HS256', 'typ': 'JWT'})}.{enc(payload)}.signature"


def test_reads_space_separated_scope_claim():
    p = WorkfluenceJwtScopeProvider()
    assert p.scopes(jwt({"scope": "read write admin", "sub": "u"})) == frozenset({"read", "write", "admin"})


def test_missing_claim_is_unknown():
    with pytest.raises(ScopeUnknown):
        WorkfluenceJwtScopeProvider().scopes(jwt({"sub": "u"}))


@pytest.mark.parametrize("token", ["", "not-a-jwt", "a.b", "a.!!!.c", None])
def test_malformed_token_is_unknown(token):
    with pytest.raises(ScopeUnknown):
        WorkfluenceJwtScopeProvider().scopes(token)


def test_error_message_does_not_contain_token():
    token = jwt({"sub": "u"})
    with pytest.raises(ScopeUnknown) as e:
        WorkfluenceJwtScopeProvider().scopes(token)
    assert token not in str(e.value)

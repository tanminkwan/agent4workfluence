import pytest

from a4w_core.config import TargetConfig
from a4w_core.errors import ConfigError
from a4w_core.interfaces import ScopeProvider
from a4w_core.scopes import ConfigScopeProvider


def cfg(**values):
    return TargetConfig("wf", {"TARGET_ADAPTER": "x", **values})


def test_config_provider_reads_list():
    p = ConfigScopeProvider.from_config(cfg(TARGET_TOKEN_SCOPES="read, write"))
    assert isinstance(p, ScopeProvider)
    assert p.scopes("ignored") == frozenset({"read", "write"})


def test_config_provider_requires_value():
    with pytest.raises(ConfigError):
        ConfigScopeProvider.from_config(cfg())


def test_builtin_registration():
    from a4w_core.builtin import register_builtins
    from a4w_core.registry import Registry

    reg = Registry()
    register_builtins(reg)
    assert "config" in reg.names("scope_provider")

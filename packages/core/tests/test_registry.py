import pytest

from a4w_core.errors import ConfigError
from a4w_core.registry import Registry


def test_register_and_create():
    reg = Registry()
    reg.register("review_policy", "p", lambda cfg: ("built", cfg))
    assert reg.create("review_policy", "p", "cfg") == ("built", "cfg")


def test_duplicate_registration_fails():
    reg = Registry()
    reg.register("spec_source", "x", lambda cfg: None)
    with pytest.raises(ValueError):
        reg.register("spec_source", "x", lambda cfg: None)


def test_unknown_name_fails_with_config_error():
    with pytest.raises(ConfigError):
        Registry().create("spec_parser", "nope", None)


def test_unknown_kind_fails():
    with pytest.raises(ValueError):
        Registry().register("bogus", "x", lambda cfg: None)


def test_builtins_are_registered():
    from a4w_core.builtin import register_builtins
    reg = Registry()
    register_builtins(reg)
    assert reg.names("spec_source") == ["hash_url", "no_hash"]
    assert reg.names("spec_parser") == ["json", "yaml"]
    assert reg.names("spec_normalizer") == ["canonical", "identity"]

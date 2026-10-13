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


def test_new_kind_is_created_on_register():
    """서비스마다 다른 종류(LLM, RAG, 검증 환경 등)를 core 수정 없이 더한다 (Phase 2 설계 2.1절)."""
    reg = Registry()
    reg.register("llm_client", "fake", lambda cfg: "llm")
    assert reg.create("llm_client", "fake", None) == "llm"
    assert reg.names("llm_client") == ["fake"]


def test_unknown_kind_on_create_fails_with_config_error():
    with pytest.raises(ConfigError):
        Registry().create("bogus", "x", None)


def test_names_of_unknown_kind_is_empty():
    assert Registry().names("bogus") == []


def test_kind_name_format_is_checked():
    with pytest.raises(ValueError):
        Registry().register("Bad Kind", "x", lambda cfg: None)


def test_builtins_are_registered():
    from a4w_core.builtin import register_builtins
    reg = Registry()
    register_builtins(reg)
    assert reg.names("spec_source") == ["hash_url", "no_hash"]
    assert reg.names("spec_parser") == ["json", "yaml"]
    assert reg.names("spec_normalizer") == ["canonical", "identity"]

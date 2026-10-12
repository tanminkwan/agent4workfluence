import pytest

from a4w_core.config import TargetConfig, load_target_configs, parse_env_text
from a4w_core.errors import ConfigError


def test_parse_env_text_skips_comments_and_blanks():
    assert parse_env_text("# c\n\nA=1\nB = two words \nC=\n") == {"A": "1", "B": "two words", "C": ""}


def test_parse_env_text_strips_matching_quotes():
    assert parse_env_text("A='x y'\nB=\"z\"\n") == {"A": "x y", "B": "z"}


def test_load_target_configs_reads_one_file_per_target(tmp_path):
    (tmp_path / "wf.env").write_text("TARGET_ADAPTER=workfluence\nSPEC_URL=u\n")
    cfgs = load_target_configs(tmp_path, ["wf"])
    assert cfgs["wf"].target_id == "wf"
    assert cfgs["wf"].adapter == "workfluence"
    assert cfgs["wf"].require("SPEC_URL") == "u"


def test_missing_target_file_fails(tmp_path):
    with pytest.raises(ConfigError):
        load_target_configs(tmp_path, ["nope"])


def test_invalid_target_id_fails(tmp_path):
    with pytest.raises(ConfigError):
        load_target_configs(tmp_path, ["../etc"])


def test_require_missing_or_empty_fails():
    cfg = TargetConfig("t", {"TARGET_ADAPTER": "a", "EMPTY": ""})
    with pytest.raises(ConfigError):
        cfg.require("NOPE")
    with pytest.raises(ConfigError):
        cfg.require("EMPTY")


def test_typed_accessors():
    cfg = TargetConfig("t", {"TARGET_ADAPTER": "a", "N": "30", "B1": "true", "B2": "FALSE", "BAD": "x"})
    assert cfg.require_int("N") == 30
    assert cfg.require_bool("B1") is True and cfg.require_bool("B2") is False
    with pytest.raises(ConfigError):
        cfg.require_int("BAD")
    with pytest.raises(ConfigError):
        cfg.require_bool("BAD")
    assert cfg.optional("NOPE") is None


def test_target_config_requires_adapter():
    with pytest.raises(ConfigError):
        TargetConfig("t", {})

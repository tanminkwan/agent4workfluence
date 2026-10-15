"""설정·시스템 프롬프트·쓰기 표시 설정 (설계 4.3·7.2, 부록 B.7 G1~G4, AC-I13)."""

from __future__ import annotations

import pytest

from a4w_core.errors import ConfigError
from a4w_inference.config import AgentSettings, InferenceSettings, load_system_prompt
from a4w_inference.engine.marks import AuthorshipMarker
from a4w_inference.engine.prompts import SystemPrompt
from datetime import datetime, timedelta, timezone

NOW = datetime(2026, 10, 15, 9, 30, tzinfo=timezone(timedelta(hours=9)))


def env_with(**over):
    base = {"INFERENCE_TIMEZONE": "Asia/Seoul"}
    base.update(over)
    return base


# ---- G1 시스템 프롬프트 읽기 ----

def test_G1_no_prompt_anywhere_refuses_to_start(tmp_path):
    with pytest.raises(ConfigError) as e:
        load_system_prompt(env_with(), agent_env={})
    assert "INFERENCE_SYSTEM_PROMPT" in str(e.value)


def test_G1_text_from_env():
    assert load_system_prompt(env_with(INFERENCE_SYSTEM_PROMPT="너는 agent다"), agent_env={}) == "너는 agent다"


def test_G1_file_wins_over_text_and_must_exist(tmp_path):
    f = tmp_path / "system.md"
    f.write_text("파일 프롬프트\n여러 줄", encoding="utf-8")
    env = env_with(INFERENCE_SYSTEM_PROMPT="환경 text", INFERENCE_SYSTEM_PROMPT_FILE=str(f))
    assert load_system_prompt(env, agent_env={}) == "파일 프롬프트\n여러 줄"
    with pytest.raises(ConfigError):
        load_system_prompt(env_with(INFERENCE_SYSTEM_PROMPT_FILE=str(tmp_path / "없음.md")), agent_env={})


def test_G1_agent_setting_overrides_service_setting(tmp_path):
    f = tmp_path / "agent.md"
    f.write_text("agent 전용", encoding="utf-8")
    env = env_with(INFERENCE_SYSTEM_PROMPT="서비스 기본")
    assert load_system_prompt(env, agent_env={"AGENT_SYSTEM_PROMPT_FILE": str(f)}) == "agent 전용"
    assert load_system_prompt(env, agent_env={"AGENT_SYSTEM_PROMPT": "agent text"}) == "agent text"


def test_G1_empty_file_is_an_error(tmp_path):
    f = tmp_path / "e.md"
    f.write_text("  \n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_system_prompt(env_with(INFERENCE_SYSTEM_PROMPT_FILE=str(f)), agent_env={})


# ---- G2 자리표시자 ----

def test_G2_known_placeholders_render():
    p = SystemPrompt.parse("${agent_id} ${targets} ${now} ${data_open} ${data_close}")
    out = p.render(data_open="<o>", data_close="<c>", now=NOW, agent_id="aiagent", targets=("workfluence", "other"))
    assert out == "aiagent workfluence, other 2026-10-15T09:30:00+09:00 <o> <c>"


def test_G2_unknown_placeholder_refuses_to_start():
    with pytest.raises(ConfigError) as e:
        SystemPrompt.parse("너는 ${agnt_id}다")
    assert "agnt_id" in str(e.value)


def test_G2_json_braces_and_dollar_signs_in_text_are_fine():
    p = SystemPrompt.parse('예: {"a": 1} 그리고 가격은 $$5 이다. ${agent_id}')
    assert '{"a": 1}' in p.render(data_open="o", data_close="c", now=NOW, agent_id="x", targets=("t",))


# ---- G3 AGENT_WRITE_MARKS ----

def test_G3_parse_marks():
    m = AuthorshipMarker.parse_marks("create_page:body,add_page_label:-,edit:title+body")
    assert m == {"create_page": ("body",), "add_page_label": (), "edit": ("title", "body")}


@pytest.mark.parametrize("raw", ["create_page", "create_page:", ":body", "a:b,a:c", "a:b++c", " "])
def test_G3_bad_marks_are_config_errors(raw):
    with pytest.raises(ConfigError):
        AuthorshipMarker.parse_marks(raw)


def test_G3_marker_applies_and_does_not_mutate_the_input():
    marker = AuthorshipMarker({"wf": {"create_page": ("body",), "add_page_label": ()}}, "\n--AI")
    args = {"title": "t", "body": "본문"}
    out = marker.apply("wf", "create_page", args)
    assert out == {"title": "t", "body": "본문\n--AI"} and args == {"title": "t", "body": "본문"}
    assert marker.apply("wf", "create_page", out) == out                      # 두 번 붙지 않는다
    assert marker.apply("wf", "add_page_label", {"name": "x"}) == {"name": "x"}


def test_G3_marker_errors_are_typed():
    from a4w_inference.engine.marks import MarkNotApplicable, MarkNotConfigured
    marker = AuthorshipMarker({"wf": {"create_page": ("body",)}}, "--AI")
    with pytest.raises(MarkNotConfigured):
        marker.apply("wf", "archive_page", {"id": "x"})
    with pytest.raises(MarkNotConfigured):
        marker.apply("other_target", "create_page", {"body": "x"})
    with pytest.raises(MarkNotApplicable):
        marker.apply("wf", "create_page", {"body": 5})


def test_G3_suffix_decodes_backslash_n():
    assert AuthorshipMarker.decode_suffix(r"\n\n---\n_AI_") == "\n\n---\n_AI_"


# ---- G4 서비스 설정 검사 ----

GOOD = {
    "INFERENCE_PORT": "18003", "INFERENCE_AUTH_MODE": "none", "INFERENCE_STORE_PATH": "/tmp/x", "INFERENCE_AGENTS": "aiagent",
    "INFERENCE_AGENTS_DIR": "/config/agents", "INFERENCE_TARGETS_DIR": "/config/targets", "INFERENCE_TIMEZONE": "Asia/Seoul",
    "INFERENCE_TEXT_MAX_CHARS": "8000", "INFERENCE_REQUESTER_MAX_CHARS": "128", "INFERENCE_MAX_CONCURRENT_PER_AGENT": "2",
    "INFERENCE_RETENTION_DAYS": "30", "INFERENCE_PURGE_INTERVAL_SECONDS": "3600", "INFERENCE_HISTORY_MAX_TURNS": "50",
    "INFERENCE_SEARCH_TOP_K": "8", "INFERENCE_SEARCH_MIN_SCORE": "0", "INFERENCE_SEARCH_HISTORY_TURNS": "3",
    "INFERENCE_SEARCH_QUERY_MAX_CHARS": "1000", "INFERENCE_LLM_MODEL": "openai/gpt-oss-120b", "INFERENCE_LLM_TIMEOUT_SECONDS": "120",
    "INFERENCE_LLM_MAX_OUTPUT_TOKENS": "4096", "INFERENCE_LLM_TEMPERATURE": "0.2", "INFERENCE_LLM_REASONING_EFFORT": "low",
    "INFERENCE_LLM_MAX_RETRIES": "2", "INFERENCE_MAX_STEPS": "12", "INFERENCE_MAX_TOOL_CALLS": "12", "INFERENCE_MAX_WRITE_CALLS": "3",
    "INFERENCE_MAX_SECONDS": "300", "INFERENCE_MAX_SAME_ERROR": "2", "INFERENCE_RETRY_SEARCH_MAX": "1",
    "INFERENCE_MAX_INPUT_TOKENS": "131072", "INFERENCE_INPUT_SAFETY_RATIO": "0.8", "INFERENCE_CHARS_PER_TOKEN": "1.5",
    "INFERENCE_TOOL_RESULT_MAX_CHARS": "20000", "INFERENCE_TRIM_KEEP_CHARS": "1500", "INFERENCE_AI_MARK_SUFFIX": r"\n\n---\n_AI_",
    "INFERENCE_RECORD_ARG_MAX_CHARS": "200", "INFERENCE_SYSTEM_PROMPT": "너는 ${agent_id}다",
}


def test_G4_good_settings_load_with_the_documented_defaults_coming_only_from_env():
    s = InferenceSettings.from_environ(GOOD)
    assert s.engine.max_input_tokens == 131072 and s.engine.safety_ratio == 0.8 and s.engine.chars_per_token == 1.5
    assert s.engine.max_write_calls == 3 and s.engine.search_history_turns == 3 and s.port == 18003
    assert s.ai_mark_suffix == "\n\n---\n_AI_" and s.auth_mode == "none" and s.agents == ("aiagent",)


def test_G4_nothing_is_defaulted_in_code():
    for key in GOOD:
        if key in ("INFERENCE_SYSTEM_PROMPT", "INFERENCE_LLM_TEMPERATURE", "INFERENCE_LLM_REASONING_EFFORT"):
            continue                                                         # 선택 값
        bad = {k: v for k, v in GOOD.items() if k != key}
        with pytest.raises(ConfigError) as e:
            InferenceSettings.from_environ(bad)
        assert key in str(e.value), key


@pytest.mark.parametrize("ratio", ["0", "-0.1", "1.01", "abc"])
def test_G4_safety_ratio_must_be_in_0_1(ratio):
    with pytest.raises(ConfigError):
        InferenceSettings.from_environ({**GOOD, "INFERENCE_INPUT_SAFETY_RATIO": ratio})


@pytest.mark.parametrize("key,value", [("INFERENCE_MAX_INPUT_TOKENS", "0"), ("INFERENCE_CHARS_PER_TOKEN", "0"),
                                       ("INFERENCE_MAX_STEPS", "0"), ("INFERENCE_MAX_CONCURRENT_PER_AGENT", "0"),
                                       ("INFERENCE_AUTH_MODE", "basic"), ("INFERENCE_TIMEZONE", "Mars/Base")])
def test_G4_nonsense_values_are_rejected(key, value):
    with pytest.raises(ConfigError):
        InferenceSettings.from_environ({**GOOD, key: value})


def test_G4_bearer_requires_a_token_and_the_error_names_the_variable_not_the_value():
    with pytest.raises(ConfigError) as e:
        InferenceSettings.from_environ({**GOOD, "INFERENCE_AUTH_MODE": "bearer"})
    assert "INFERENCE_API_TOKEN" in str(e.value)
    ok_ = InferenceSettings.from_environ({**GOOD, "INFERENCE_AUTH_MODE": "bearer", "INFERENCE_API_TOKEN": "tok-SECRET"})
    assert "tok-SECRET" not in repr(ok_)


def test_agent_settings_need_targets_and_parse_optional_overrides():
    a = AgentSettings.from_values("aiagent", {"AGENT_TARGETS": "workfluence, other", "AGENT_MAX_CONCURRENT_REQUESTS": "3"})
    assert a.targets == ("workfluence", "other") and a.max_concurrent == 3
    with pytest.raises(ConfigError):
        AgentSettings.from_values("aiagent", {})
    assert AgentSettings.from_values("aiagent", {"AGENT_TARGETS": "w"}).max_concurrent is None

"""채팅 설정 파일 읽기: token 파일·응답 문구·프롬프트 덧붙임 (Phase 5 설계 7절, NFR-C01·C03, AC-C12).

채팅 token은 agent token(MCP 자식 몫)과 다른 파일·디렉터리에 둔다. 오류 메시지에는 경로와 변수 이름만 쓰고 값은 쓰지 않는다.
"""

from __future__ import annotations

import pytest

from a4w_core.errors import ConfigError
from a4w_inference.channels.chat.config import ChatReplies, load_chat_notes, load_chat_replies, load_chat_token
from a4w_inference.channels.chat.worker import ReplyTexts

SECRET = "tok-CHAT-SECRET-123"
TEXTS = {"answer_empty": "답이 비었습니다", "limit_reached": "한도에 걸렸습니다", "failed": "처리하지 못했습니다", "rejected": "요청을 받을 수 없습니다",
         "busy": "지금 바빠서 처리하지 못했습니다", "queue_full": "앞선 요청이 끝난 뒤 다시 보내 주세요", "timeout": "결과를 가져오지 못했습니다",
         "interrupted": "재시작으로 중단되었습니다"}


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ── token ─────────────────────────────────────────────────────────────────

def test_F1_the_token_is_read_from_the_agent_and_target_file(tmp_path):
    write(tmp_path / "aiagent" / "workfluence.env", f"# 채팅 token\nCHAT_API_TOKEN={SECRET}\n")
    assert load_chat_token(tmp_path, "aiagent", "workfluence") == SECRET


def test_F2_a_missing_file_is_a_config_error_naming_the_path_only(tmp_path):
    with pytest.raises(ConfigError) as e:
        load_chat_token(tmp_path, "aiagent", "workfluence")
    assert "aiagent" in str(e.value) and "workfluence" in str(e.value)


@pytest.mark.parametrize("content", ["", "# 비었음\n", "CHAT_API_TOKEN=\n", "OTHER=1\n", "CHAT_API_TOKEN=   \n"])
def test_F3_a_file_without_a_usable_token_is_a_config_error_naming_the_variable(tmp_path, content):
    write(tmp_path / "aiagent" / "workfluence.env", content)
    with pytest.raises(ConfigError) as e:
        load_chat_token(tmp_path, "aiagent", "workfluence")
    assert "CHAT_API_TOKEN" in str(e.value)


def test_F4_the_agent_mcp_token_file_is_never_used_as_a_fallback(tmp_path):
    """MCP 자식이 읽는 agent token(TARGET_API_TOKEN)을 채팅이 대신 쓰면 비밀 경계가 깨진다."""
    write(tmp_path / "aiagent" / "workfluence.env", "TARGET_API_TOKEN=tok-AGENT-MCP\n")
    with pytest.raises(ConfigError):
        load_chat_token(tmp_path, "aiagent", "workfluence")


def test_F5_ids_with_path_separators_are_refused(tmp_path):
    for agent, target in (("../x", "workfluence"), ("aiagent", "a/b"), ("", "workfluence"), ("aiagent", "")):
        with pytest.raises(ConfigError):
            load_chat_token(tmp_path, agent, target)


def test_F6_no_error_message_contains_the_token_value(tmp_path):
    write(tmp_path / "aiagent" / "workfluence.env", f"CHAT_API_TOKEN={SECRET}\nBROKEN LINE {SECRET}\n")
    try:
        load_chat_token(tmp_path, "aiagent", "workfluence")
    except ConfigError as e:
        assert SECRET not in str(e)


# ── 응답 문구 ─────────────────────────────────────────────────────────────

def yaml_text(texts=TEXTS, labels=None, notice="나머지는 올리지 않았습니다."):
    lines = ["texts:"] + [f"  {k}: \"{v}\"" for k, v in texts.items()]
    lines += ["stop_labels:"] + [f"  {k}: \"{v}\"" for k, v in (labels if labels is not None else {"LIMIT_STEPS": "(차례 한도)"}).items()]
    if notice is not None:
        lines.append(f"truncate_notice: \"{notice}\"")
    return "\n".join(lines) + "\n"


def test_F7_replies_are_loaded_into_reply_texts_and_a_truncate_notice(tmp_path):
    f = write(tmp_path / "chat_replies.yaml", yaml_text())
    r = load_chat_replies(f)
    assert isinstance(r, ChatReplies) and isinstance(r.texts, ReplyTexts)
    assert r.texts.get("busy") == "지금 바빠서 처리하지 못했습니다" and r.texts.stop_label("LIMIT_STEPS") == "(차례 한도)"
    assert r.texts.stop_label("NOPE") == "" and r.truncate_notice == "나머지는 올리지 않았습니다."


@pytest.mark.parametrize("missing", list(TEXTS))
def test_F8_a_missing_reply_text_is_a_config_error_naming_the_key(tmp_path, missing):
    f = write(tmp_path / "r.yaml", yaml_text({k: v for k, v in TEXTS.items() if k != missing}))
    with pytest.raises(ConfigError) as e:
        load_chat_replies(f)
    assert missing in str(e.value)


def test_F9_missing_notice_missing_file_and_bad_shapes_are_config_errors(tmp_path):
    with pytest.raises(ConfigError) as e:
        load_chat_replies(write(tmp_path / "a.yaml", yaml_text(notice=None)))
    assert "truncate_notice" in str(e.value)
    with pytest.raises(ConfigError) as e2:
        load_chat_replies(tmp_path / "없음.yaml")
    assert "없음.yaml" in str(e2.value)
    for bad in ("- a\n- b\n", "just text", "texts: 5\n", "texts: {}\nstop_labels: 3\ntruncate_notice: x\n", ":::\n\t"):
        with pytest.raises(ConfigError):
            load_chat_replies(write(tmp_path / "b.yaml", bad))


def test_F10_stop_labels_may_be_empty(tmp_path):
    r = load_chat_replies(write(tmp_path / "r.yaml", yaml_text(labels={})))
    assert r.texts.stop_label("LIMIT_STEPS") == ""


# ── 프롬프트 덧붙임 ───────────────────────────────────────────────────────

def test_F11_notes_are_read_and_trimmed(tmp_path):
    assert load_chat_notes(write(tmp_path / "n.md", "\n채팅에서는 평문으로 짧게 답하라.\n여러 줄도 된다.\n\n")) == "채팅에서는 평문으로 짧게 답하라.\n여러 줄도 된다."


def test_F12_no_notes_path_or_an_empty_file_means_none(tmp_path):
    assert load_chat_notes(None) is None
    assert load_chat_notes(write(tmp_path / "e.md", "  \n\n")) is None


def test_F13_a_notes_path_that_does_not_exist_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError) as e:
        load_chat_notes(tmp_path / "없음.md")
    assert "없음.md" in str(e.value)

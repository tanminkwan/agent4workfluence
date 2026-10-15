"""채팅 token 경계와 배포 파일 (Phase 5 설계 7·12절, NFR-C03, AC-C11·C12).

채팅 token은 본체만 읽고 MCP 자식은 읽지 않는다. 자식 환경변수 허용 목록에 CHAT_ 이름을 넣을 수 없고, 환경에 CHAT_ 값이 있어도
자식에게 가지 않는다. 배포 파일(.env.example, compose, 예시 파일)이 설정과 맞는지도 본다(저장소 루트가 보일 때만).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from a4w_core.errors import ConfigError
from a4w_inference.adapters.mcp_pool import child_env
from a4w_inference.channels.chat.config import ChatSettings, load_chat_notes, load_chat_replies
from tests.unit.test_chat_config import GOOD


def repo_root():
    for parent in Path(__file__).resolve().parents:
        if (parent / "docker-compose.yml").exists() and (parent / ".env.example").exists():
            return parent
    return None


ROOT = repo_root()
needs_repo = pytest.mark.skipif(ROOT is None, reason="저장소 루트가 보이지 않는 환경(이미지 안 코드)")


# ── 자식 환경 ─────────────────────────────────────────────────────────────

def test_I1_chat_names_cannot_be_put_in_the_mcp_child_allow_list():
    for prefixes, names in ((("MCP_", "CHAT_"), ()), (("MCP_",), ("CHAT_API_TOKEN",)), (("CHAT",), ())):
        with pytest.raises(ConfigError):
            child_env({}, agent_id="aiagent", targets=("workfluence",), prefixes=prefixes, names=names)


def test_I2_chat_values_in_the_environment_never_reach_the_child():
    environ = {**GOOD, "CHAT_API_TOKEN": "tok-CHAT", "MCP_SECRETS_DIR": "/s", "LOG_LEVEL": "INFO", "PATH": "/bin"}
    env = child_env(environ, agent_id="aiagent", targets=("workfluence",), prefixes=("MCP_", "LOG_LEVEL"), names=("PATH",))
    assert not [k for k in env if k.startswith("CHAT_")] and "tok-CHAT" not in env.values()
    assert env["MCP_AGENT_ID"] == "aiagent"


def test_I3_the_chat_secrets_dir_is_not_under_the_mcp_secrets_dir():
    """MCP 자식은 MCP_SECRETS_DIR 아래만 읽는다. 채팅 token 디렉터리가 그 안에 있으면 안 된다."""
    s = ChatSettings.from_environ({**GOOD, "CHAT_SECRETS_DIR": "/config/secrets/chat"})
    assert not Path(s.secrets_dir).is_relative_to("/config/secrets/agents") and not Path("/config/secrets/agents").is_relative_to(s.secrets_dir)


# ── 배포 파일 ─────────────────────────────────────────────────────────────

def env_example_values():
    out = {}
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^(CHAT_[A-Z0-9_]+)=(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


@needs_repo
def test_I4_env_example_documents_every_chat_variable_and_loads_cleanly():
    values = env_example_values()
    missing = [k for k in GOOD if k not in values and k not in ("CHAT_TLS_CA_FILE", "CHAT_NOTES_FILE", "CHAT_WRITE_ALLOWED_USERS")]
    assert missing == [], f".env.example에 없는 값: {missing}"
    assert values["CHAT_ENABLED"] == "false"                                # 기본은 끈다(Phase 4와 같은 동작)
    ChatSettings.from_environ({**values, "CHAT_ENABLED": "true"})           # 켜도 예시 값이 서로 맞는다


@needs_repo
def test_I5_the_example_values_are_not_secrets_or_hard_coded_user_ids():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    chat_lines = [l for l in text.splitlines() if l.startswith("CHAT_")]
    assert not any("TOKEN" in l.split("=")[0] and l.split("=", 1)[1].strip() for l in chat_lines)
    assert not re.search(r"CHAT_WRITE_ALLOWED_USERS=\S*[0-9a-f]{8}-[0-9a-f]{4}", text)


@needs_repo
def test_I6_compose_mounts_the_chat_secrets_for_the_service_only_and_read_only():
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    block = text[text.index("  inference:"):text.index("\nvolumes:")]
    assert re.search(r"\./config/secrets/chat:\$\{CHAT_SECRETS_DIR\}:ro", block)
    assert "./config/secrets/agents:${MCP_SECRETS_DIR}:ro" in block                 # 기존 agent token 마운트는 그대로
    assert "config/secrets/chat" not in text[:text.index("  inference:")]            # 다른 서비스에는 붙이지 않는다


@needs_repo
def test_I7_example_files_exist_and_load():
    prompts = ROOT / "config" / "prompts"
    r = load_chat_replies(prompts / "chat_replies.yaml.example")
    assert r.truncate_notice
    assert load_chat_notes(prompts / "chat_notes.md.example")
    token_example = (ROOT / "config" / "secrets" / "chat" / "aiagent" / "workfluence.env.example").read_text(encoding="utf-8")
    assert "CHAT_API_TOKEN=" in token_example and not re.search(r"CHAT_API_TOKEN=\S", token_example)


@needs_repo
def test_I8_gitignore_keeps_the_chat_secrets_and_local_prompt_files_out_of_git():
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "config/secrets/**/*.env" in text                                         # 채팅 token 파일은 이 규칙에 걸린다
    assert "config/prompts/chat_replies.yaml" in text.replace("*.example", "")

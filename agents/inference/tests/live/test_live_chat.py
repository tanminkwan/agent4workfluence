"""채팅 채널 실호출 (Phase 5 설계 13.3, AC-C9). A4W_LIVE_TESTS=1 이고 고정 시험 방이 설정에 있을 때만 돈다.

실행 중인 Inference(compose, CHAT_ENABLED=true)가 aiagent로 채팅에 붙어 있어야 한다. 요청자는 aiagent-peer다
(peer token은 config/secrets/targets/workfluence.peer.env, INFERENCE_TEST_SECRETS로 붙인다).
고정 방 두 개(1:1, 여럿)를 재사용한다(방·메시지는 지울 수 없다). 메시지마다 실행 id를 넣어 새로 돈 결과임을 확인한다.
쓰기 시험이 단 댓글은 끝에 agent token으로 지운다.
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

import httpx
import pytest

from a4w_core.config import parse_env_text

pytestmark = [pytest.mark.live,
              pytest.mark.skipif(os.environ.get("A4W_LIVE_TESTS") != "1", reason="A4W_LIVE_TESTS=1 일 때만"),
              pytest.mark.skipif(not os.environ.get("INFERENCE_LIVE_CHAT_DIRECT_ROOM"), reason="고정 시험 방 설정이 없다")]

AGENT_USERNAME = "aiagent"
BASE_PAGE_TITLE = "a4w 시험 바탕 페이지"          # homeSpace의 고정 자료 (Phase 3 결과서)
REPLY_WAIT_SECONDS = 180
SILENCE_SECONDS = 20


def client(token: str) -> httpx.Client:
    return httpx.Client(base_url=os.environ["CHAT_API_BASE_URL"], verify=os.environ.get("CHAT_TLS_CA_FILE") or True,
                        headers={"Authorization": f"Bearer {token}"}, timeout=30)


@pytest.fixture(scope="module")
def peer():
    with client(os.environ["TARGET_API_TOKEN"]) as c:      # peer 비밀 파일의 token (--env-file)
        yield c


@pytest.fixture(scope="module")
def agent():
    secrets = parse_env_text((Path(os.environ["INFERENCE_SECRETS_DIR"]) / "aiagent" / "workfluence.env").read_text(encoding="utf-8"))
    with client(secrets["TARGET_API_TOKEN"]) as c:
        yield c


@pytest.fixture(scope="module")
def agent_user_id(peer):
    room = peer.get(f"/chat/rooms/{os.environ['INFERENCE_LIVE_CHAT_DIRECT_ROOM']}").json()
    return next(m["userId"] for m in room["members"] if m.get("username") == AGENT_USERNAME)


@pytest.fixture(scope="module", autouse=True)
def chat_is_connected():
    health = httpx.get(os.environ["INFERENCE_LIVE_BASE_URL"] + "/health", timeout=30).json()
    assert health.get("chat", {}).get("state") == "connected", health.get("chat")


def send(peer, room: str, body: str) -> int:
    r = peer.post(f"/chat/rooms/{room}/messages", json={"body": body, "clientMsgId": f"live:{uuid.uuid4().hex[:20]}"})
    assert r.status_code == 201, r.text
    return r.json()["seq"]


def replies(peer, room: str, after: int, sender: str) -> list[dict]:
    items = peer.get(f"/chat/rooms/{room}/messages", params={"afterSeq": after, "limit": 50}).json()["items"]
    return [m for m in items if m["senderId"] == sender]


def wait_reply(peer, room: str, after: int, sender: str, seconds: float = REPLY_WAIT_SECONDS) -> str:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        found = replies(peer, room, after, sender)
        if found:
            return found[0]["body"]
        time.sleep(1)
    raise AssertionError(f"{seconds}초 안에 답이 없다")


def check_short_plain(text: str) -> None:
    limit = int(os.environ["CHAT_ANSWER_MAX_CHARS"]) + 200        # 잘렸으면 안내 줄이 붙는다
    assert text.strip() and len(text) <= limit
    assert "```" not in text and "**" not in text and "\n|" not in text


def test_L1_direct_room_read_request_gets_a_short_plain_answer(peer, agent_user_id):
    room, run = os.environ["INFERENCE_LIVE_CHAT_DIRECT_ROOM"], uuid.uuid4().hex[:8]
    seq = send(peer, room, f"내가 볼 수 있는 스페이스 목록 알려줘 (시험 {run})")
    check_short_plain(wait_reply(peer, room, seq, agent_user_id))


def test_L2_direct_room_write_request_comments_on_the_base_page_then_the_test_removes_it(peer, agent, agent_user_id):
    room, run = os.environ["INFERENCE_LIVE_CHAT_DIRECT_ROOM"], uuid.uuid4().hex[:8]
    marker = f"채팅 시험 {run}"
    seq = send(peer, room, f"'{BASE_PAGE_TITLE}' 페이지에 '{marker}' 라고 댓글 달아줘")
    check_short_plain(wait_reply(peer, room, seq, agent_user_id))
    home = dict(kv.split("=", 1) for kv in parse_env_text(
        (Path(os.environ["INFERENCE_AGENTS_DIR"]) / "aiagent" / "workfluence.env").read_text(encoding="utf-8"))["AGENT_CONTEXT"].split(","))
    pages = agent.get("/pages", params={"space": home["homeSpace"]}).json()["items"]
    page = next(p for p in pages if p["title"] == BASE_PAGE_TITLE)
    mine = [c for c in agent.get(f"/pages/{page['id']}/comments").json()["items"] if marker in (c.get("body") or "")]
    try:
        assert len(mine) == 1                                      # 요청자 지시대로 한 번만 썼다
    finally:
        for c in mine:
            agent.delete(f"/comments/{c['id']}")


def test_L3_group_room_answers_only_when_called_by_name(peer, agent_user_id):
    room, run = os.environ["INFERENCE_LIVE_CHAT_GROUP_ROOM"], uuid.uuid4().hex[:8]
    quiet = send(peer, room, f"오늘 점심 뭐 먹지 (시험 {run})")
    time.sleep(SILENCE_SECONDS)
    assert replies(peer, room, quiet, agent_user_id) == []
    seq = send(peer, room, f"AI Agent 내가 볼 수 있는 스페이스 목록 알려줘 (시험 {run})")
    check_short_plain(wait_reply(peer, room, seq, agent_user_id))

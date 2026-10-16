"""채팅 답에서 id 같은 내부 식별자를 뺀다 (Phase 5 설계 20절, 2026-10-16 사용자 시험).

"답에 id를 쓰지 않는다"는 덧붙임 프롬프트 규칙만으로는 모델이 어겼다("페이지 ID는 67b7eca0-… 에 있습니다").
보내기 직전에 코드가 뺀다: id가 든 괄호는 통째로, 그래도 id가 남은 문장은 뺀다. 문장을 다 빼게 되면 id만 지운다.
식별자 모양은 설정(CHAT_ANSWER_HIDE_PATTERN)이고 비우면 끈다.
"""

from __future__ import annotations

import re

import pytest

from a4w_core.errors import ConfigError
from a4w_inference.channels.chat.config import ChatSettings
from a4w_inference.channels.chat.textfmt import hide_identifiers
from tests.unit.test_chat_config import GOOD
from tests.unit.test_chat_sender import make

pytestmark = pytest.mark.anyio
UUIDISH = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}")
REPORTED = "댓글을 “test” 내용으로 추가했습니다. 페이지 ID는 67b7eca0-a5ab-43e5-88ff-f06d f06d1cc12ce7 에 있습니다."


@pytest.mark.parametrize("text, expected", [
    (REPORTED, "댓글을 “test” 내용으로 추가했습니다."),
    ("Crazy Study를 만들었습니다(id: f8e35749-84cc-4e10-a297-720e9d1a21e7). 멤버 2명을 넣었습니다.",
     "Crazy Study를 만들었습니다. 멤버 2명을 넣었습니다."),
    ("- 회의록 (3fa85f64-5717-4562-b3fc-2c963f66afa6)\n- 공지", "- 회의록\n- 공지"),
    ("만들었습니다!\n스페이스 id: f8e35749-84cc-4e10-a297-720e9d1a21e7\n다음 할 일을 알려 주세요.", "만들었습니다!\n다음 할 일을 알려 주세요."),
    ("id 없는 답입니다. 버전 3이고 2026-10-16에 고쳤습니다.", "id 없는 답입니다. 버전 3이고 2026-10-16에 고쳤습니다."),
])
def test_H1_sentences_and_brackets_with_an_identifier_are_removed(text, expected):
    assert hide_identifiers(text, UUIDISH) == expected


def test_H2_if_every_sentence_has_an_identifier_the_notice_is_sent_or_only_the_identifiers_go():
    only = "Crazy Study 스페이스 ID는 f8e35749-84cc-4e10-a297-720e9d1a21e7 입니다."      # 실호출에서 본 답
    assert hide_identifiers(only, UUIDISH, fallback="내부 id는 채팅에 보여 드리지 않습니다.") == "내부 id는 채팅에 보여 드리지 않습니다."
    assert hide_identifiers("f8e35749-84cc-4e10-a297-720e9d1a21e7 스페이스를 만들었습니다.", UUIDISH) == "스페이스를 만들었습니다."


async def test_H6_the_sender_uses_the_notice_when_nothing_is_left():
    sender, api, _, _ = make(hide_pattern=UUIDISH, hidden_notice="[id 숨김]", answer_max_chars=200)
    assert await sender.post("room", "ID는 f8e35749-84cc-4e10-a297-720e9d1a21e7 입니다.", client_msg_id="c-2")
    assert api.sent[0][1] == "[id 숨김]"


def test_H7_the_replies_file_may_carry_the_notice(tmp_path):
    from a4w_inference.channels.chat.config import load_chat_replies
    from tests.unit.test_chat_wiring import REPLIES
    f = tmp_path / "r.yaml"
    f.write_text(REPLIES, encoding="utf-8")
    assert load_chat_replies(f).hidden_ids_notice is None
    f.write_text(REPLIES + 'hidden_ids_notice: "[id 숨김]"\n', encoding="utf-8")
    assert load_chat_replies(f).hidden_ids_notice == "[id 숨김]"


def test_H3_without_a_pattern_nothing_changes():
    assert hide_identifiers(REPORTED, None) == REPORTED


async def test_H4_the_sender_hides_identifiers_before_sending():
    sender, api, _, _ = make(hide_pattern=UUIDISH, answer_max_chars=200)
    assert await sender.post("room", REPORTED, client_msg_id="c-1")
    assert api.sent[0][1] == "댓글을 “test” 내용으로 추가했습니다."


def test_H5_the_setting_is_optional_and_a_bad_regex_stops_the_service():
    assert ChatSettings.from_environ(GOOD).answer_hide_pattern is None
    s = ChatSettings.from_environ({**GOOD, "CHAT_ANSWER_HIDE_PATTERN": UUIDISH.pattern})
    assert s.answer_hide_pattern.pattern == UUIDISH.pattern
    with pytest.raises(ConfigError) as e:
        ChatSettings.from_environ({**GOOD, "CHAT_ANSWER_HIDE_PATTERN": "([unclosed"})
    assert "CHAT_ANSWER_HIDE_PATTERN" in str(e.value)

"""채팅 프레임 해석 (Phase 5 설계 4절·13.1 S1, FR-C05·C06, AC-C2).

순수 함수만 다룬다. 알 수 없는 프레임·필드는 무시하고 기록할 수 있게 돌려주고, 필수 필드가 없으면 버린다(BadFrame).
어떤 입력도 예외를 내지 않는다. hello·ack의 세부 필드는 시험 스택 녹화 전(설계 17절)이라 원본을 그대로 들고만 있다.
"""

from __future__ import annotations

import json

import pytest

from a4w_inference.channels.chat.frames import parse_frame, parse_message
from a4w_inference.channels.chat.models import (AckFrame, BadFrame, ChatMessage, ErrorFrame, HelloFrame, MessageFrame,
                                                RoomFrame, UnknownFrame)

ROOM = "bfe0ce49-02f4-40da-b46c-a9c8f84deb25"
SENDER = "d15736fd-d217-4f85-9219-bfb6435143ac"


def msg(**over):
    base = {"type": "message", "roomId": ROOM, "seq": 7, "senderId": SENDER, "body": "회의록 찾아줘", "kind": "user",
            "createdAt": "2026-10-15T06:14:07.882Z"}
    base.update(over)
    return base


def raw(d):
    return json.dumps(d, ensure_ascii=False)


# ── message ───────────────────────────────────────────────────────────────

def test_C1_message_frame_is_parsed_into_a_chat_message():
    f = parse_frame(raw(msg()))
    assert isinstance(f, MessageFrame)
    m = f.message
    assert m == ChatMessage(room_id=ROOM, seq=7, sender_id=SENDER, body="회의록 찾아줘", kind="user", created_at="2026-10-15T06:14:07.882Z")


def test_C2_bytes_and_dict_inputs_are_accepted_too():
    assert isinstance(parse_frame(raw(msg()).encode("utf-8")), MessageFrame)
    assert isinstance(parse_frame(msg()), MessageFrame)


@pytest.mark.parametrize("kind,is_user", [("user", True), ("join", False), ("leave", False), ("system", False), ("", False)])
def test_C3_only_kind_user_is_a_request_candidate(kind, is_user):
    f = parse_frame(msg(kind=kind))
    assert isinstance(f, MessageFrame)
    assert f.message.kind == kind and f.message.is_user is is_user


def test_C4_unknown_extra_fields_are_ignored():
    f = parse_frame(msg(futureField={"a": 1}, mentions=["x"]))
    assert isinstance(f, MessageFrame) and f.message.seq == 7


@pytest.mark.parametrize("missing", ["roomId", "seq", "senderId", "body", "kind", "createdAt"])
def test_C5_a_message_without_a_required_field_is_dropped_as_bad(missing):
    d = msg()
    del d[missing]
    f = parse_frame(raw(d))
    assert isinstance(f, BadFrame) and missing in f.reason


@pytest.mark.parametrize("field,value", [("seq", "7"), ("seq", 7.5), ("seq", True), ("seq", None), ("roomId", 5), ("senderId", None),
                                          ("body", 123), ("kind", None), ("createdAt", 0)])
def test_C6_wrong_typed_required_fields_are_bad(field, value):
    f = parse_frame(raw(msg(**{field: value})))
    assert isinstance(f, BadFrame) and field in f.reason


def test_C7_a_message_with_empty_body_is_still_parsed():
    f = parse_frame(msg(body=""))
    assert isinstance(f, MessageFrame) and f.message.body == ""


def test_C8_message_without_kind_is_never_treated_as_user():
    """kind가 없으면 BadFrame이라 요청이 될 수 없다. 오래된 형식을 user로 가정하지 않는다(안전 기본)."""
    d = msg()
    del d["kind"]
    assert isinstance(parse_frame(raw(d)), BadFrame)


# ── room ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("change", ["joined", "members", "left", "somethingNew"])
def test_C9_room_frame_keeps_any_change_value(change):
    f = parse_frame(raw({"type": "room", "roomId": ROOM, "change": change}))
    assert isinstance(f, RoomFrame) and f.room_id == ROOM and f.change == change


@pytest.mark.parametrize("d", [{"type": "room", "change": "joined"}, {"type": "room", "roomId": ROOM},
                               {"type": "room", "roomId": 1, "change": "joined"}, {"type": "room", "roomId": ROOM, "change": None}])
def test_C10_room_frame_without_required_fields_is_bad(d):
    assert isinstance(parse_frame(raw(d)), BadFrame)


# ── hello · ack · error ───────────────────────────────────────────────────

def test_C11_hello_carries_my_user_id_and_the_room_ids_at_connect_time():
    """workf 확인(2026-10-15): hello는 {type, userId, roomIds}. userId는 이 연결 주인(=내 id)이다."""
    h = parse_frame(raw({"type": "hello", "userId": "u-1", "roomIds": [ROOM, "r-2"], "futureField": 1}))
    assert isinstance(h, HelloFrame) and h.user_id == "u-1" and h.room_ids == (ROOM, "r-2")


@pytest.mark.parametrize("d", [{"type": "hello", "roomIds": []}, {"type": "hello", "userId": "u-1"},
                               {"type": "hello", "userId": 5, "roomIds": []}, {"type": "hello", "userId": "u-1", "roomIds": "x"},
                               {"type": "hello", "userId": "u-1", "roomIds": [1]}])
def test_C11b_hello_without_my_id_or_with_wrong_types_is_bad(d):
    """내 userId를 모르면 자기 메시지를 걸러낼 수 없다(FR-C05). 그런 hello는 받아들이지 않는다."""
    assert isinstance(parse_frame(raw(d)), BadFrame)


def test_C11c_ack_keeps_its_raw_fields():
    a = parse_frame(raw({"type": "ack", "clientMsgId": "reply:r-1", "seq": 9}))
    assert isinstance(a, AckFrame) and a.data["clientMsgId"] == "reply:r-1" and "type" not in a.data


def test_C12_error_frame_has_optional_code_and_message():
    e = parse_frame(raw({"type": "error", "code": "RATE_LIMITED", "message": "느리게", "details": {"retryAfterSec": 12}}))
    assert isinstance(e, ErrorFrame) and e.code == "RATE_LIMITED" and e.message == "느리게" and e.details == {"retryAfterSec": 12}
    e2 = parse_frame(raw({"type": "error"}))
    assert isinstance(e2, ErrorFrame) and e2.code is None and e2.message is None and e2.details == {}


def test_C12b_insufficient_scope_error_keeps_room_and_client_msg_id():
    """쓰기 scope가 없는 token의 send는 연결을 닫지 않고 error 프레임만 온다(workf 확인)."""
    e = parse_frame(raw({"type": "error", "code": "INSUFFICIENT_SCOPE", "roomId": ROOM, "clientMsgId": "reply:r-1"}))
    assert isinstance(e, ErrorFrame) and e.code == "INSUFFICIENT_SCOPE" and e.room_id == ROOM and e.client_msg_id == "reply:r-1"


# ── 알 수 없는 것·깨진 것 ─────────────────────────────────────────────────

def test_C13_unknown_frame_type_is_reported_not_raised():
    f = parse_frame(raw({"type": "typing", "roomId": ROOM}))
    assert isinstance(f, UnknownFrame) and f.type == "typing"


@pytest.mark.parametrize("junk", ["", "not json", "[1, 2]", "null", "123", '"str"', "{", b"\xff\xfe\x00", "{}", '{"type": 5}'])
def test_C14_garbage_never_raises_and_is_bad(junk):
    f = parse_frame(junk)
    assert isinstance(f, (BadFrame, UnknownFrame))
    if isinstance(f, BadFrame):
        assert f.reason


def test_C15_bad_frame_reason_does_not_echo_the_message_body():
    """본문이 로그로 새지 않게 이유에는 필드 이름만 담는다(NFR-C03)."""
    d = msg(seq="not-a-number", body="비밀스러운 본문 내용")
    f = parse_frame(raw(d))
    assert isinstance(f, BadFrame) and "비밀스러운" not in f.reason and "not-a-number" not in f.reason


# ── REST 메시지 (보충) ────────────────────────────────────────────────────

def test_C16_rest_message_dict_has_the_same_shape_without_type():
    d = msg()
    del d["type"]
    d["id"] = "bea27c2d-48e8-471f-94ac-d9b63d490d3b"
    m = parse_message(d)
    assert isinstance(m, ChatMessage) and m.seq == 7 and m.kind == "user"


def test_C17_rest_message_missing_fields_returns_bad():
    d = msg()
    del d["type"], d["seq"]
    assert isinstance(parse_message(d), BadFrame)


def test_C18_message_model_is_immutable():
    m = parse_message({k: v for k, v in msg().items() if k != "type"})
    with pytest.raises(Exception):
        m.seq = 8  # type: ignore[misc]

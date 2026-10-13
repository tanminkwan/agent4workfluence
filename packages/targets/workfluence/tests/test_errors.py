import json

from a4w_target_workfluence.errors import WorkfluenceErrorInterpreter


def body(code, message="m", request_id="rq-1"):
    return json.dumps({"error": {"code": code, "message": message, "requestId": request_id}}).encode()


def test_extracts_code_and_request_id():
    e = WorkfluenceErrorInterpreter().interpret(404, {}, body("NOT_FOUND"))
    assert e.code == "TARGET_ERROR"
    assert e.target_code == "NOT_FOUND"
    assert e.request_id == "rq-1"
    assert e.http_status == 404


def test_token_errors_say_do_not_retry():
    e = WorkfluenceErrorInterpreter().interpret(401, {}, body("TOKEN_EXPIRED"))
    assert not e.retryable
    assert "다시" in e.hint


def test_version_conflict_hint_mentions_base_version():
    e = WorkfluenceErrorInterpreter().interpret(409, {}, body("VERSION_CONFLICT"))
    assert "baseVersionNo" in e.hint


def test_unknown_409_gets_status_hint():
    e = WorkfluenceErrorInterpreter().interpret(409, {}, body("SOMETHING_NEW"))
    assert e.hint


def test_unknown_code_keeps_message_only():
    e = WorkfluenceErrorInterpreter().interpret(400, {}, body("BRAND_NEW", message="칸 x가 틀림"))
    assert e.message == "칸 x가 틀림"


def test_non_json_body():
    e = WorkfluenceErrorInterpreter().interpret(502, {}, b"<html>bad gateway</html>")
    assert e.retryable
    assert e.target_code is None

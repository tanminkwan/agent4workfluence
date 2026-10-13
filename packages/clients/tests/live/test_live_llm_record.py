"""실제 LLM 엔드포인트 응답을 녹화한다 (Phase 2 설계 10.2절: 계약 시험은 녹화한 HTTP 응답으로).

A4W_LIVE_TESTS=1 이고 LLM 비밀이 있을 때만 돈다. 녹화본은 tests/contract/recordings/에 쓰고 Git에 올린다.
남기지 않는 것: 요청(key가 든 header), 응답의 reasoning_content(사고 내용), 응답 header(content-type·retry-after 말고).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest

from a4w_clients.llm.base import LlmError, LlmRequest
from a4w_clients.llm.openai_compatible import OpenAiCompatibleClient

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("A4W_LIVE_TESTS") != "1" or not os.environ.get("LLM_API_KEY"),
                       reason="A4W_LIVE_TESTS=1 이고 LLM 비밀이 있을 때만"),
]

OUT = Path(os.environ.get("A4W_RECORD_DIR", Path(__file__).resolve().parents[1] / "contract" / "recordings"))
SCHEMA = {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}},
          "additionalProperties": False}
KEEP_HEADERS = ("content-type", "retry-after")


def _clean(body: bytes):
    try:
        doc = json.loads(body)
    except ValueError:
        return {"_text": body.decode("utf-8", "replace")[:2000]}
    for c in doc.get("choices", []) if isinstance(doc, dict) else []:
        (c.get("message") or {}).pop("reasoning_content", None)
        (c.get("message") or {}).pop("reasoning", None)
    return doc


def recorder(case: str):
    saved = []

    def hook(response: httpx.Response) -> None:
        response.read()
        saved.append({"status": response.status_code,
                      "headers": {k: v for k, v in response.headers.items() if k.lower() in KEEP_HEADERS},
                      "body": _clean(response.content)})

    def flush(extra=None):
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / f"{case}.json").write_text(json.dumps({"case": case, "model": os.environ["LLM_MODEL"],
                                                     "responses": saved, **(extra or {})},
                                                    ensure_ascii=False, indent=1), encoding="utf-8")

    return hook, flush, saved


def client(hook, key=None, retries=0, timeout=None):
    http = httpx.Client(trust_env=True, event_hooks={"response": [hook]})
    return OpenAiCompatibleClient(base_url=os.environ["LLM_BASE_URL"], api_key=key or os.environ["LLM_API_KEY"],
                                  response_format=os.environ["LLM_RESPONSE_FORMAT"], max_retries=retries, http=http)


def request(**over):
    kw = dict(system="너는 API tool 이름을 짓는다.", user="페이지에 댓글을 다는 tool의 영어 snake_case 이름 하나를 정하라.",
              output_schema=SCHEMA, model=os.environ["LLM_MODEL"], timeout_seconds=300, max_output_tokens=4000,
              temperature=0.2)
    kw.update(over)
    return LlmRequest(**kw)


def test_record_normal():
    hook, flush, saved = recorder("normal")
    r = client(hook).complete(request())
    flush()
    assert r.value["name"] and saved[0]["status"] == 200
    assert saved[0]["body"]["choices"][0]["finish_reason"] == "stop"


def test_record_length():
    hook, flush, saved = recorder("length")
    with pytest.raises(LlmError) as e:
        client(hook).complete(request(max_output_tokens=16))
    flush()
    assert e.value.kind == "output_format"
    assert saved[0]["body"]["choices"][0]["finish_reason"] == "length"


def test_record_auth():
    hook, flush, saved = recorder("auth")
    with pytest.raises(LlmError) as e:
        client(hook, key="sk-not-a-real-key").complete(request())
    flush()
    assert e.value.kind == "auth"
    assert saved[0]["status"] in (401, 403)


def test_timeout_is_classified():
    """시간 초과는 응답이 없어 녹화할 것이 없다. 분류만 실제로 확인한다."""
    hook, flush, saved = recorder("timeout")
    with pytest.raises(LlmError) as e:
        client(hook).complete(request(timeout_seconds=0.05))
    flush({"note": "응답 없음 (클라이언트 시간 초과)"})
    assert e.value.kind == "timeout" and saved == []

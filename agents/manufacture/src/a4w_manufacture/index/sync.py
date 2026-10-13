"""색인 문서 만들기와 색인 맞추기 (Phase 2 설계 14.3·14.4절).

바라는 상태 = 현재 포인터 묶음. index_state(지난번 기록)와 비교해 바뀐 것만 upsert·delete 하고,
마지막에 AI Pro Plus 목록과 비교해 우리 source 앞머리인데 모르는 point를 지운다.
실패는 반영을 되돌리지 않는다. drift로 알리고 다음 반영 때 같은 비교로 맞춘다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from a4w_clients.rag.base import RagDocument, RagError, RagSpace, RagStore

log = logging.getLogger(__name__)

# point ID 이름공간 (식별 규칙이라 설정 대상이 아니다, 설계 14.3절)
NAMESPACE = uuid.UUID("6f3f6c2e-4b1d-5a7e-9c55-a4a4a4a4a4a4")
DESCRIPTION_IN_EXTENDED = 300


def point_id(target_id: str, tool_name: str) -> str:
    return str(uuid.uuid5(NAMESPACE, f"{target_id}:{tool_name}"))


def index_document(target_id: str, tool: Mapping[str, Any], *, tool_version: str | None, max_chars: int) -> RagDocument:
    index = tool.get("index", {})
    content = f"{tool['title']}. {index.get('summary', '')}\n" + " / ".join(index.get("examples", []))
    extended = {
        "targetId": target_id, "name": tool["name"], "revision": tool["revision"], "kind": tool["kind"],
        "title": tool["title"], "description": tool["description"][:DESCRIPTION_IN_EXTENDED], "risk": tool["risk"],
        "scopes": list(tool["scopes"]), "group": tool.get("group"), "toolVersion": tool_version,
    }
    return RagDocument(id=point_id(target_id, tool["name"]), content=content[:max_chars],
                       extended_content=json.dumps(extended, ensure_ascii=False, sort_keys=True),
                       source=f"{target_id}/{tool['name']}@{tool['revision']}")


class IndexSync:
    def __init__(self, rag: RagStore, db, *, content_max_chars: int, list_limit: int, clock: Callable[[], str]):
        self._rag = rag
        self._db = db
        self._max = content_max_chars
        self._limit = list_limit
        self._clock = clock

    def sync(self, target_id: str, space: RagSpace, tools: Mapping[str, Mapping[str, Any]],
             *, tool_version: str | None) -> dict[str, Any]:
        out = {"upserted": 0, "deleted": 0, "failed": 0, "drift": False}
        try:
            ref = self._rag.ensure_space(space)
        except RagError as e:
            log.warning("index space failed: target=%s err=%s", target_id, e)
            out.update(failed=max(1, len(tools)), drift=True)
            return out
        recorded = self._db.index_state(target_id)
        desired = {}
        for name, tool in tools.items():
            doc = index_document(target_id, tool, tool_version=tool_version, max_chars=self._max)
            desired[name] = doc
            sha = hashlib.sha256(doc.content.encode("utf-8")).hexdigest()
            rec = recorded.get(name)
            if rec and rec["revision"] == tool["revision"] and rec["contentSha256"] == sha:
                continue
            try:
                self._rag.upsert(ref, doc)
            except RagError as e:
                log.warning("index upsert failed: tool=%s err=%s", name, e)
                out["failed"] += 1
                continue
            self._db.index_upsert(target_id, name, point_id=doc.id, revision=tool["revision"], content_sha256=sha,
                                  at=self._clock())
            out["upserted"] += 1
        for name, rec in recorded.items():
            if name in desired:
                continue
            try:
                self._rag.delete(ref, rec["pointId"])
            except RagError:
                out["failed"] += 1
                continue
            self._db.index_delete(target_id, name)
            out["deleted"] += 1
        try:
            listed = self._rag.list_ids(ref, limit=self._limit)
        except RagError:
            listed = {}
            out["failed"] += 1
        wanted = {d.id for d in desired.values()}
        for pid, source in listed.items():
            if pid not in wanted and source.startswith(f"{target_id}/"):
                try:
                    self._rag.delete(ref, pid)
                    out["deleted"] += 1
                except RagError:
                    out["failed"] += 1
        out["drift"] = out["failed"] > 0
        return out

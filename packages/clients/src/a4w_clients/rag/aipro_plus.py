"""AI Pro Plus(Qdrant 기반 RAG 관리 시스템) 클라이언트 (Phase 2 설계 14.2절).

- 시험 서버는 nobody 모드: Authorization을 보내지 않고 X-Virtual-User만 보낸다.
- 우리 이름 앞머리 밖의 컬렉션·도메인은 쓰지도 지우지도 않는다. 컬렉션·도메인 삭제는 구현하지 않는다.
- 검색에는 collection_id를 늘 넣는다(빠지면 전 컬렉션 검색).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .base import RagDocument, RagError, RagHit, RagSpace, RagSpaceRef, RagStore

log = logging.getLogger(__name__)
AUTH_MODES = ("none", "bearer")


class AiProPlusStore(RagStore):
    def __init__(
        self,
        *,
        base_url: str,
        auth_mode: str,
        api_key: str | None,
        virtual_user: str | None,
        collection_prefix: str,
        domain_prefix: str,
        max_attempts: int,
        http: httpx.Client,
    ):
        if auth_mode not in AUTH_MODES:
            raise ValueError(f"RAG_AUTH_MODE는 {AUTH_MODES} 중 하나: {auth_mode}")
        if auth_mode == "bearer" and not api_key:
            raise ValueError("bearer 모드에는 RAG_API_KEY가 필요하다")
        if not collection_prefix or not domain_prefix:
            raise ValueError("RAG 이름 앞머리가 비었다")
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}"} if auth_mode == "bearer" else {}
        if auth_mode == "none" and virtual_user:
            self._headers["X-Virtual-User"] = virtual_user
        self._cprefix = collection_prefix
        self._dprefix = domain_prefix
        self._attempts = max_attempts
        self._http = http

    def __repr__(self) -> str:
        return f"AiProPlusStore(base_url={self._base!r})"

    def _call(self, method: str, path: str, *, json: Any = None) -> Any:
        last = ""
        for _ in range(self._attempts):
            try:
                resp = self._http.request(method, self._base + path, json=json, headers=self._headers)
            except httpx.TransportError as e:
                last = type(e).__name__
                continue
            if resp.status_code >= 500:
                last = f"{resp.status_code} {resp.text[:200]}"
                continue
            if resp.status_code >= 400:
                raise RagError(f"{method} {path}: {resp.status_code} {resp.text[:200]}")
            return resp.json() if resp.content else None
        raise RagError(f"{method} {path}: {last}")

    def _guard(self, space: RagSpace) -> None:
        if not space.collection.startswith(self._cprefix) or not space.domain.startswith(self._dprefix):
            raise RagError(f"우리 이름 규약 밖이다: {space.collection} / {space.domain}")

    def collections(self) -> list[dict[str, Any]]:
        return self._call("GET", "/api/collections") or []

    def ensure_space(self, space: RagSpace) -> RagSpaceRef:
        self._guard(space)
        if not any(c.get("collection_name") == space.collection for c in self.collections()):
            self._call("POST", "/api/collections", json={"collection_name": space.collection, "name": space.collection,
                                                         "description": space.description, "search_method": "vector"})
        domains = self._call("GET", "/api/domains") or []
        found = next((d for d in domains if d.get("name") == space.domain), None)
        if found is None:
            found = self._call("POST", "/api/domains", json={"name": space.domain})
        return RagSpaceRef(collection=space.collection, domain_id=int(found["id"]))

    def upsert(self, ref: RagSpaceRef, doc: RagDocument) -> None:
        self._own(ref)
        self._call("POST", "/api/rag/knowledge", json={
            "collection_name": ref.collection, "domain_id": ref.domain_id, "content": doc.content,
            "extended_content": doc.extended_content, "source": doc.source, "point_id": doc.id,
        })

    def delete(self, ref: RagSpaceRef, doc_id: str) -> None:
        self._own(ref)
        self._call("DELETE", f"/api/rag/knowledge/{ref.collection}/{doc_id}")

    def list_ids(self, ref: RagSpaceRef, *, limit: int) -> dict[str, str]:
        # 전용 목록 API가 없다. query 없는 search가 목록이다(쪽 나누기 없음)
        hits = self._call("POST", "/api/rag/search", json={"collection_id": ref.collection, "domain_id": ref.domain_id,
                                                           "limit": limit}) or []
        return {str(h["id"]): h.get("source", "") for h in hits}

    def search(self, ref: RagSpaceRef, query: str, *, limit: int) -> list[RagHit]:
        hits = self._call("POST", "/api/rag/search", json={"collection_id": ref.collection, "domain_id": ref.domain_id,
                                                           "query": query, "search_method": "vector", "limit": limit}) or []
        return [RagHit(id=str(h["id"]), score=float(h.get("score", 0.0)), content=h.get("content", ""),
                       extended_content=h.get("extended_content", ""), source=h.get("source", "")) for h in hits]

    def _own(self, ref: RagSpaceRef) -> None:
        if not ref.collection.startswith(self._cprefix):
            raise RagError(f"우리 컬렉션이 아니다: {ref.collection}")


def from_settings(env, secrets) -> AiProPlusStore:
    auth = env.require("RAG_AUTH_MODE")
    return AiProPlusStore(
        base_url=env.require("RAG_BASE_URL"), auth_mode=auth,
        api_key=secrets.require("RAG_API_KEY") if auth == "bearer" else None,
        virtual_user=env.optional("RAG_VIRTUAL_USER"),
        collection_prefix=env.require("RAG_COLLECTION_PREFIX"), domain_prefix=env.require("RAG_DOMAIN_PREFIX"),
        max_attempts=env.require_int("RAG_MAX_ATTEMPTS"),
        http=httpx.Client(timeout=env.require_int("RAG_TIMEOUT_SECONDS"), trust_env=True),
    )

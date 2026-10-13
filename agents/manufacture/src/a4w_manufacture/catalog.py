"""타겟 카탈로그 읽기 (Phase 2 설계 3.1·7.3절): 대상 선정 규칙, seed, 검증 시나리오, 색인 평가 질의.

카탈로그는 타겟 어댑터 패키지의 데이터 파일이다(Git 관리). 어댑터는 위치만 알려 주고 해석은 여기서 한다.
"""

from __future__ import annotations

import json
from functools import cached_property
from typing import Any

import yaml

READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class FileCatalog:
    def __init__(self, root):  # Path 또는 importlib.resources Traversable
        self._root = root

    def rules(self) -> dict[str, Any]:
        return yaml.safe_load((self._root / "rules.yaml").read_text(encoding="utf-8"))

    @cached_property
    def _seeds(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for t in self._json_files("seed", "tools"):
            op_id = (t.get("source") or {}).get("operationId")
            if op_id in out:
                raise ValueError(f"seed에 같은 동작이 둘이다: {op_id}")
            out[op_id] = t
        return out

    def seeds(self) -> list[dict[str, Any]]:
        return sorted(self._seeds.values(), key=lambda t: t["name"])

    def seed_for(self, operation_id: str | None) -> dict[str, Any] | None:
        return self._seeds.get(operation_id or "")

    def seed_match(self, operation_id: str | None, fragment_sha: str, structure_sha: str) -> str:
        """none | same | doc_only | changed (Phase 2 설계 7.3절 흐름)."""
        seed = self.seed_for(operation_id)
        if seed is None:
            return "none"
        src = seed.get("source") or {}
        if src.get("fragmentSha256") == fragment_sha:
            return "same"
        if src.get("structureSha256") == structure_sha:
            return "doc_only"
        return "changed"

    def composites(self) -> list[dict[str, Any]]:
        return sorted(self._json_files("seed", "composites"), key=lambda t: t["name"])

    @cached_property
    def _scenarios(self) -> dict[str, dict[str, Any]]:
        out = {}
        d = self._root / "scenarios"
        if d.is_dir():
            for f in sorted(d.iterdir(), key=lambda p: p.name):
                if f.name.endswith(".yaml"):
                    out[f.name[: -len(".yaml")]] = yaml.safe_load(f.read_text(encoding="utf-8"))
        return out

    def scenario(self, tool_name: str) -> dict[str, Any] | None:
        return self._scenarios.get(tool_name)

    def queries(self) -> list[dict[str, Any]]:
        f = self._root / "queries.yaml"
        if not f.is_file():
            return []
        return list((yaml.safe_load(f.read_text(encoding="utf-8")) or {}).get("queries", []))

    def examples(self, *, group: str | None, method: str, count: int, exclude: str | None = None) -> list[dict[str, Any]]:
        """LLM 본보기 seed (FR-239): (1) 같은 묶음, (2) 같은 메서드 계열(읽기·쓰기), (3) 이름 순. 결정적이다."""
        write = method not in READ_METHODS

        def key(t: dict[str, Any]) -> tuple[int, int, str]:
            t_write = (t.get("http") or {}).get("method", "GET") not in READ_METHODS
            return (0 if t.get("group") == group else 1, 0 if t_write == write else 1, t["name"])

        pool = [t for t in self.seeds() if t["name"] != exclude]
        return sorted(pool, key=key)[:count]

    def _json_files(self, *parts: str) -> list[dict[str, Any]]:
        d = self._root
        for p in parts:
            d = d / p
        if not d.is_dir():
            return []
        return [json.loads(f.read_text(encoding="utf-8")) for f in sorted(d.iterdir(), key=lambda p: p.name)
                if f.name.endswith(".json")]

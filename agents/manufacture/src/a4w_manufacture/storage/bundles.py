"""tool 묶음 파일 (Phase 2 설계 13.1절). 한 번 쓴 묶음은 바꾸지 않고, 현재 포인터만 원자적으로 바꾼다.

staging/<applyId>/ 에 모두 쓰고 fsync → versions/<toolVersion>/ 로 rename → current.json은 tmp에 쓰고 os.replace.
tool 정의 조회 API(Phase 3)도 같은 순서(current.json → manifest)로만 읽는다. staging은 보지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from a4w_core.openapi.canonical import canonical_json
from a4w_tools.reader import Toolset, ToolsetNotFound, ToolsetReader
from a4w_tools.validate import validate_toolset

_VERSION = re.compile(r"^tv-(\d{6})-[0-9a-f]{8}$")


class FileToolStore:
    def __init__(self, root: Path | str):
        self._root = Path(root)
        self._reader = ToolsetReader(self._root)

    def _target(self, target_id: str) -> Path:
        return self._root / "targets" / target_id

    def current(self, target_id: str) -> str | None:
        try:
            return self._reader.current_version(target_id)
        except ToolsetNotFound:
            return None

    def pointer(self, target_id: str) -> dict[str, str] | None:
        """현재 포인터 {"toolVersion", "updatedAt"}. 없으면 None."""
        version = self.current(target_id)
        if version is None:
            return None
        raw = json.loads((self._target(target_id) / "current.json").read_text(encoding="utf-8"))
        return {"toolVersion": version, "updatedAt": raw.get("updatedAt", "")}

    def has_version(self, target_id: str, tool_version: str) -> bool:
        return bool(_VERSION.match(tool_version)) and (self._target(target_id) / "versions" / tool_version).is_dir()

    def read(self, target_id: str, tool_version: str) -> Toolset:
        return self._reader.version(target_id, tool_version)

    def versions(self, target_id: str) -> list[str]:
        vdir = self._target(target_id) / "versions"
        if not vdir.is_dir():
            return []
        return sorted(p.name for p in vdir.iterdir() if _VERSION.match(p.name))

    def write(
        self,
        target_id: str,
        *,
        apply_id: str,
        tools: Mapping[str, Mapping[str, Any]],
        parent: str | None,
        meta: Mapping[str, Any],
    ) -> str:
        """새 묶음을 쓰고 tool 버전을 돌려준다. 포인터는 바꾸지 않는다."""
        names = sorted(tools)
        digest = hashlib.sha256(canonical_json([[n, tools[n]["revision"]] for n in names]).encode()).hexdigest()[:8]
        existing = self.versions(target_id)
        seq = (int(_VERSION.match(existing[-1]).group(1)) if existing else 0) + 1
        version = f"tv-{seq:06d}-{digest}"
        staging = self._target(target_id) / "staging" / apply_id
        if staging.exists():
            shutil.rmtree(staging)
        (staging / "tools").mkdir(parents=True)
        entries = []
        verified = meta.get("verified", {})
        for name in names:
            t = tools[name]
            _write_json(staging / "tools" / f"{name}.json", t)
            entries.append({
                "name": name, "kind": t["kind"], "file": f"tools/{name}.json", "revision": t["revision"],
                "risk": t["risk"], "scopes": list(t["scopes"]), "operationId": (t.get("source") or {}).get("operationId"),
                "verified": verified.get(name, "passed"),
            })
        manifest = {
            "schema": "a4w.toolset/v1", "targetId": target_id, "toolVersion": version, "parent": parent,
            "specVersion": meta["specVersion"], "changeSetId": meta.get("changeSetId"), "createdAt": meta["createdAt"],
            "tools": entries,
        }
        problems = validate_toolset(manifest)
        if problems:
            raise ValueError(f"manifest가 형식에 맞지 않는다: {problems[0]}")
        _write_json(staging / "manifest.json", manifest)
        _fsync_dir(staging / "tools")
        _fsync_dir(staging)
        self._finalize(target_id, apply_id, version)
        return version

    def _finalize(self, target_id: str, apply_id: str, version: str) -> None:
        dest = self._target(target_id) / "versions" / version
        if dest.exists():
            raise FileExistsError(f"이미 있는 tool 버전: {version}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.rename(self._target(target_id) / "staging" / apply_id, dest)
        _fsync_dir(dest.parent)

    def set_current(self, target_id: str, tool_version: str, *, updated_at: str = "") -> None:
        if not (self._target(target_id) / "versions" / tool_version / "manifest.json").is_file():
            raise FileNotFoundError(f"없는 tool 버전: {tool_version}")
        pointer = self._target(target_id) / "current.json"
        tmp = pointer.with_name("current.json.tmp")
        _write_json(tmp, {"toolVersion": tool_version, "updatedAt": updated_at})
        os.replace(tmp, pointer)
        _fsync_dir(pointer.parent)

    def clean_staging(self, target_id: str) -> int:
        staging = self._target(target_id) / "staging"
        if not staging.is_dir():
            return 0
        count = 0
        for p in staging.iterdir():
            shutil.rmtree(p, ignore_errors=True)
            count += 1
        return count

    def check(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        probe = self._root / ".probe"
        probe.write_text("ok")
        probe.unlink()


def _write_json(path: Path, value: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)  # 입력 칸 순서를 지킨다
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)

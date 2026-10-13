"""ToolsetReader: `current.json` → `versions/<v>/manifest.json` → tool 파일 (읽기 전용, 설계 13.1절)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .validate import validate_tool, validate_toolset


class ToolsetNotFound(LookupError):
    pass


@dataclass(frozen=True)
class Toolset:
    target_id: str
    tool_version: str
    manifest: dict[str, Any]
    tools: dict[str, dict[str, Any]]


class ToolsetReader:
    def __init__(self, root: Path | str):
        self._root = Path(root)

    def current_version(self, target_id: str) -> str:
        pointer = self._root / "targets" / target_id / "current.json"
        try:
            return json.loads(pointer.read_text(encoding="utf-8"))["toolVersion"]
        except (OSError, ValueError, KeyError):
            raise ToolsetNotFound(f"현재 tool 버전이 없다: {target_id}") from None

    def current(self, target_id: str) -> Toolset:
        return self.version(target_id, self.current_version(target_id))

    def version(self, target_id: str, tool_version: str) -> Toolset:
        vdir = self._root / "targets" / target_id / "versions" / tool_version
        try:
            manifest = json.loads((vdir / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ToolsetNotFound(f"tool 버전이 없다: {target_id}/{tool_version}") from None
        problems = validate_toolset(manifest)
        if problems or manifest["toolVersion"] != tool_version or manifest["targetId"] != target_id:
            raise ToolsetNotFound(f"manifest가 맞지 않는다: {target_id}/{tool_version}")
        tools: dict[str, dict[str, Any]] = {}
        for entry in manifest["tools"]:
            try:
                tool = json.loads((vdir / entry["file"]).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise ToolsetNotFound(f"tool 파일이 없다: {entry['file']}") from None
            if validate_tool(tool) or tool["name"] != entry["name"] or tool["revision"] != entry["revision"]:
                raise ToolsetNotFound(f"tool 파일이 manifest와 다르다: {entry['file']}")
            tools[entry["name"]] = tool
        return Toolset(target_id, tool_version, manifest, tools)

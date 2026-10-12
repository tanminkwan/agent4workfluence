"""사람이 확인하는 줄 단위 diff."""

from __future__ import annotations

import difflib


class TextDiffer:
    def __init__(self, context_lines: int):
        self._n = context_lines

    def diff(self, old: str | None, new: str, old_label: str, new_label: str) -> str:
        lines = difflib.unified_diff(
            (old or "").splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=old_label,
            tofile=new_label,
            n=self._n,
        )
        return "".join(line if line.endswith("\n") else line + "\n" for line in lines)

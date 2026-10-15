"""시스템 프롬프트 (Phase 4 설계 4.3, FR-I26). 문구는 설정에서 오고 코드에는 없다. 자리표시자는 ${이름}이다."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from string import Template

from a4w_core.errors import ConfigError

PLACEHOLDERS = ("data_open", "data_close", "now", "agent_id", "targets")


class SystemPrompt:
    def __init__(self, template: Template):
        self._template = template

    @classmethod
    def parse(cls, text: str) -> SystemPrompt:
        template = Template(text)
        for m in template.pattern.finditer(text):
            name = m.group("named") or m.group("braced")
            if m.group("invalid") is not None:
                raise ConfigError("시스템 프롬프트에 잘못된 '$'가 있다 (자리표시자는 ${이름}, 글자 $는 $$)")
            if name is not None and name not in PLACEHOLDERS:
                raise ConfigError(f"시스템 프롬프트에 알 수 없는 자리표시자: ${{{name}}} (허용: {', '.join(PLACEHOLDERS)})")
        return cls(template)

    def render(self, *, data_open: str, data_close: str, now: datetime, agent_id: str, targets: Sequence[str]) -> str:
        return self._template.substitute(data_open=data_open, data_close=data_close, now=now.isoformat(timespec="seconds"),
                                         agent_id=agent_id, targets=", ".join(targets))

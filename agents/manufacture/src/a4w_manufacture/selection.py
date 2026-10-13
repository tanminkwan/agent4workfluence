"""대상 선정 (Phase 2 설계 5절). 규칙은 타겟 카탈로그, 켜고 끄기는 타겟 .env. 결정적이고 LLM이 끼지 않는다."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any

RISK_ORDER = {"read": 0, "write": 1, "danger": 2}
READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@dataclass(frozen=True)
class Selection:
    kind: str                       # tool | excluded | codeOwned
    group: str | None = None
    reason: str | None = None       # 제외 이유 (ADMIN, GROUP_DISABLED, SCOPE_MISSING …)
    fallback: bool = False          # 규칙에 없어 fallback으로 정했는지
    risk: str | None = None
    scopes: tuple[str, ...] = ()


class Selector:
    def __init__(self, rules: Mapping[str, Any], *, enabled_groups: Iterable[str], token_scopes: Collection[str]):
        self._groups: dict[str, Any] = dict(rules["groups"])
        self._rules = list(rules.get("rules", []))
        self._fallback = rules.get("fallback", {})
        self._transform = list(rules.get("transform", []))
        self._context = dict(rules.get("context", {}))
        enabled = set(enabled_groups)
        unknown = enabled - set(self._groups)
        if unknown:
            raise ValueError(f"카탈로그에 없는 묶음: {', '.join(sorted(unknown))}")
        self._enabled = enabled
        implies = rules.get("scopeImplies", {})
        self._token = set(token_scopes) | {s for t in token_scopes for s in implies.get(t, [])}

    def select(self, fragment: Mapping[str, Any]) -> Selection:
        method = fragment["method"]
        scopes = self._required_scopes(fragment)
        rule = next((r for r in self._rules if _matches(r["match"], fragment, scopes)), None)
        fallback = rule is None
        if rule is not None and rule.get("codeOwned"):
            return Selection("codeOwned", scopes=scopes)
        if rule is not None and "exclude" in rule:
            return Selection("excluded", reason=rule["exclude"], scopes=scopes)
        group = rule["group"] if rule is not None else self._fallback_group(method)
        risk = self._risk(group, method)
        if group not in self._enabled:
            return Selection("excluded", group=group, reason="GROUP_DISABLED", fallback=fallback, risk=risk, scopes=scopes)
        if not set(scopes) <= self._token:
            return Selection("excluded", group=group, reason="SCOPE_MISSING", fallback=fallback, risk=risk, scopes=scopes)
        return Selection("tool", group=group, fallback=fallback, risk=risk, scopes=scopes)

    def fixed_values(self, fragment: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        """고정값 {위치: {이름: 값}}. 카탈로그 context의 칸은 값 대신 {"$context": 키} (설계 6.3.1).

        그 동작에 없는 칸은 기계 변환이 무시한다.
        """
        out: dict[str, dict[str, Any]] = {}
        for key, spec in sorted(self._context.items()):
            for bind in spec.get("bind", []):
                name = bind["name"] if bind["in"] != "body" else bind["pointer"].lstrip("/")
                out.setdefault(bind["in"], {})[name] = {"$context": key}
        scopes = self._required_scopes(fragment)
        for rule in self._transform:
            if _matches(rule["match"], fragment, scopes):
                for loc, values in rule.get("fixed", {}).items():
                    out.setdefault(loc, {}).update(values)
                break
        return out

    def _fallback_group(self, method: str) -> str:
        return self._fallback.get("byMethod", {}).get(method, self._fallback["default"])

    def _risk(self, group: str, method: str) -> str:
        by_method = "read" if method in READ_METHODS else "write"
        floor = self._groups[group]["risk"]
        if floor == "auto":
            return by_method
        return max(floor, by_method, key=RISK_ORDER.__getitem__)

    def _required_scopes(self, fragment: Mapping[str, Any]) -> tuple[str, ...]:
        """명세 security에서 필요 scope. 대안(OR)이 여럿이면 token으로 만족하는 첫 대안."""
        options = [sorted({s for values in req.values() for s in values}) for req in fragment.get("security") or []]
        if not options:
            return ()
        ok = next((o for o in options if set(o) <= self._token), options[0])
        return tuple(ok)


def _matches(match: Mapping[str, Any], fragment: Mapping[str, Any], scopes: tuple[str, ...]) -> bool:
    for key, want in match.items():
        if key == "operationId":
            patterns = [want] if isinstance(want, str) else want
            if not any(fnmatchcase(fragment.get("operationId") or "", p) for p in patterns):
                return False
        elif key == "method":
            if fragment["method"] != want:
                return False
        elif key == "pathPrefix":
            if not fragment["path"].startswith(want):
                return False
        elif key == "tag":
            if want not in (fragment.get("tags") or []):
                return False
        elif key == "scope":
            if want not in scopes:
                return False
        else:
            raise ValueError(f"모르는 규칙 조건: {key}")
    return True

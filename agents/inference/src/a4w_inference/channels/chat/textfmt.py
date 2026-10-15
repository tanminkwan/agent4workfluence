"""채팅에 보낼 글 다듬기: 평문화와 길이 규칙 (Phase 5 설계 5.2절, FR-C21·C22·C24). 순수 함수다.

평문화는 서식 문자만 걷어내고 내용은 지킨다. 길이 규칙은 줄 경계에서 자르고 안내 줄을 붙이며 서버 상한을 넘지 않는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_HTML_TAGS = ("a|b|i|u|s|em|strong|p|div|span|code|pre|ul|ol|li|h[1-6]|table|thead|tbody|tr|td|th|blockquote|hr|img|sub|sup|del|ins")
_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAG = re.compile(rf"</?(?:{_HTML_TAGS})(?:\s[^<>]*)?/?>", re.IGNORECASE)
_FENCE = re.compile(r"^\s*```")
_RULE = re.compile(r"^\s*([-*_])(?:\s*\1){2,}\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*$")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_QUOTE = re.compile(r"^\s{0,3}>\s?")
_BULLET = re.compile(r"^(\s*)[*+-]\s+(.*)$")
_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD_STAR = re.compile(r"\*\*(.+?)\*\*")
_BOLD_UNDER = re.compile(r"(?<!\w)__(.+?)__(?!\w)")
_STRIKE = re.compile(r"~~(.+?)~~")
_ITALIC_STAR = re.compile(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])")
_ITALIC_UNDER = re.compile(r"(?<!\w)_(?!\s)([^_\n]+?)(?<!\s)_(?!\w)")
_CODE = re.compile(r"`([^`\n]+)`")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_]+$")
_MANY_BLANKS = re.compile(r"\n{3,}")


def _bold_under(m: re.Match) -> str:
    inner = m.group(1)
    return m.group(0) if _IDENTIFIER.match(inner) else inner        # __init__ 같은 식별자는 건드리지 않는다


def _inline(line: str) -> str:
    line = _IMAGE.sub(lambda m: f"{m.group(1)} ({m.group(2)})" if m.group(1) else m.group(2), line)
    line = _LINK.sub(r"\1 (\2)", line)
    line = _TAG.sub("", line)
    line = _BOLD_STAR.sub(r"\1", line)
    line = _BOLD_UNDER.sub(_bold_under, line)
    line = _STRIKE.sub(r"\1", line)
    line = _ITALIC_STAR.sub(r"\1", line)
    line = _ITALIC_UNDER.sub(r"\1", line)
    return _CODE.sub(r"\1", line)


def to_plain_text(text: str) -> str:
    text = _BR.sub("\n", text.replace("\r\n", "\n").replace("\r", "\n"))
    out: list[str] = []
    in_code = False
    for line in text.split("\n"):
        if _FENCE.match(line):
            in_code = not in_code
            continue
        if in_code:
            out.append(line.rstrip())
            continue
        if _RULE.match(line) or ("|" in line and _TABLE_SEP.match(line)):
            continue
        stripped = line.strip()
        if len(stripped) > 1 and stripped.startswith("|") and stripped.endswith("|"):
            line = stripped[1:-1].strip()
        if (m := _HEADING.match(line)):
            line = m.group(1)
        line = _QUOTE.sub("", line, count=1)
        if (m := _BULLET.match(line)):
            line = f"{m.group(1)}- {m.group(2)}"
        out.append(_inline(line).rstrip())
    return _MANY_BLANKS.sub("\n\n", "\n".join(out)).strip()


@dataclass(frozen=True)
class FitResult:
    text: str
    truncated: bool


def fit_answer(text: str, *, max_chars: int, hard_max_chars: int, notice: str) -> FitResult:
    """답을 max_chars 안으로 줄인다. 넘으면 줄 경계(없으면 공백, 없으면 글자 수)에서 자르고 안내 줄을 붙인다.

    결과 전체는 hard_max_chars(서버 본문 상한)를 넘지 않는다. 설정이 이를 지킬 수 없으면 ValueError다(짧은 답에서도 바로).
    """
    if max_chars < 1 or not notice or max_chars + 1 + len(notice) > hard_max_chars:
        raise ValueError("CHAT_ANSWER_MAX_CHARS·안내 문구·CHAT_MAX_MESSAGE_CHARS 관계가 맞지 않는다")
    text = text.strip()
    if len(text) <= max_chars:
        return FitResult(text, False)
    window = text[:max_chars]
    body = ""
    if text[max_chars] == "\n":
        body = window.rstrip()
    elif "\n" in window:
        body = window[:window.rfind("\n")].rstrip()
    if not body.strip():
        if text[max_chars].isspace():
            body = window.rstrip()
        else:
            cut = max((i for i, c in enumerate(window) if c.isspace()), default=0)
            body = window[:cut].rstrip() if cut > 0 else window
    return FitResult(f"{body}\n{notice}", True)

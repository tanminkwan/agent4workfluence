"""채팅 프롬프트 덧붙임에 방 참여자를 채운다 (Phase 5 설계 18절).

문구는 덧붙임 파일에 있고, 코드는 자리표시자 두 개만 바꾼다. 다른 `$`는 건드리지 않는다.
표시 이름은 사용자가 정하는 글이라 줄바꿈·제어 문자를 공백으로 바꾸고 길이를 자른다. username은 서버 패턴에 맞을 때만 쓴다.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from .models import Member

MEMBERS_SLOT = "${room_members}"
COUNT_SLOT = "${room_member_count}"
USERNAME = re.compile(r"^[a-z0-9._-]{2,64}$")      # Workfluence 명세의 username 패턴
UNSAFE = re.compile(r"[\x00-\x1f\x7f  ]")


def render_channel_notes(template: str | None, members: Sequence[Member], *, my_user_id: str | None, max_members: int,
                         name_max_chars: int) -> str | None:
    if template is None:
        return None
    if MEMBERS_SLOT not in template and COUNT_SLOT not in template:
        return template
    others = [m for m in members if m.user_id != my_user_id]
    lines = "\n".join(_line(m, name_max_chars) for m in others[:max_members])
    return template.replace(MEMBERS_SLOT, lines).replace(COUNT_SLOT, str(len(others)))


def _line(member: Member, name_max_chars: int) -> str:
    username = member.username if member.username and USERNAME.match(member.username) else None
    name = " ".join(UNSAFE.sub(" ", member.display_name).split())[:name_max_chars].strip()
    name = name or username or member.user_id
    return f"- {name} (username={username})" if username else f"- {name}"

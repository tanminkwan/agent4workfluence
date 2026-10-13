"""Workfluence 시험 환경: 검증마다 팀 스페이스를 만들고, 끝나면 통째로 지운다 (Phase 2 설계 11.6절).

시험 스택에는 쓸 만한 스페이스도 고정 시험 자료도 없다. 스페이스에는 key가 없고 id로 식별한다.
스페이스 삭제는 휴지통으로 가고 보존 기간 뒤 서버 배치가 지운다(공개 API에 완전 삭제 없음).

검증 상대 계정(peer, 선택, 11.6.1절): 시험 스페이스 멤버로 넣고 @멘션 댓글로 검증 계정에게 알림을 만든다.
다른 멤버가 있으면 owner도 스페이스를 지울 수 없으므로(403) 정리할 때 peer를 먼저 뺀다.
"""

from __future__ import annotations

import logging
from pathlib import Path

from a4w_core.config import Settings, parse_env_text
from a4w_core.errors import SandboxUnavailable
from a4w_tools.call_policy import ConfigCallPolicy
from a4w_tools.sandbox import CallFailed, CleanupReport, RawCaller, SandboxContext, VerifySandbox
from a4w_tools.target_http import TargetHttpClient

log = logging.getLogger(__name__)
LIST_LIMIT = 1000  # spaces.list의 최대값 (명세)

# 바탕 자료의 글. 검증 계정이 쓰는 글에는 @멘션을 쓰지 않는다(알림 부수효과).
SPACE_DESCRIPTION = "aiagent4workfluence 실호출 검증용. 검증이 끝나면 지운다."
PAGE_TITLE = "검증 바탕 페이지"
PAGE_BODY = "실호출 검증에 쓰는 페이지입니다."
LABEL_NAME = "a4w-verify"
COMMENT_BODY = "검증용 댓글입니다."
ATTACHMENT_NAME = "a4w-verify.txt"
ATTACHMENT_TEXT = "실호출 검증에 쓰는 첨부입니다."
PEER_ROLE = "editor"


class WorkfluenceSpaceSandbox(VerifySandbox):
    def __init__(self, *, space_prefix: str, peer: RawCaller | None = None, peer_username: str | None = None,
                 agent_username: str | None = None):
        if not space_prefix:
            raise ValueError("시험 스페이스 이름 앞머리가 비었다")
        if peer is not None and not (peer_username and agent_username):
            raise ValueError("검증 상대 계정을 쓰려면 peer·agent username이 있어야 한다")
        self._prefix = space_prefix
        self._peer = peer
        self._peer_user = peer_username
        self._agent_user = agent_username

    @classmethod
    def from_config(cls, arg) -> "WorkfluenceSpaceSandbox":
        """arg는 타겟 설정, 또는 (타겟 설정, 타겟 비밀 디렉터리). 비밀 디렉터리가 있으면 peer 비밀을 찾는다."""
        cfg, secrets_dir = arg if isinstance(arg, tuple) else (arg, None)
        peer_file = cfg.optional("VERIFY_PEER_SECRETS")
        if not peer_file or secrets_dir is None or not (Path(secrets_dir) / peer_file).is_file():
            return cls(space_prefix=cfg.require("VERIFY_SPACE_PREFIX"))
        secrets = Settings(parse_env_text((Path(secrets_dir) / peer_file).read_text(encoding="utf-8")),
                           origin=f"secrets {peer_file}")
        peer = RawCaller(TargetHttpClient.from_settings(cfg, secrets), ConfigCallPolicy.from_settings(cfg))
        return cls(space_prefix=cfg.require("VERIFY_SPACE_PREFIX"), peer=peer,
                   peer_username=cfg.require("VERIFY_PEER_USERNAME"), agent_username=cfg.require("VERIFY_AGENT_USERNAME"))

    @property
    def has_peer(self) -> bool:
        return self._peer is not None

    def context_keys(self) -> list[str]:
        # homeSpace: agent의 고정 맥락 키 (카탈로그 rules.yaml의 context). 검증 때는 시험 스페이스다
        # notificationId: peer가 있을 때만 값이 생긴다 (11.6.1)
        return ["spaceId", "spaceName", "pageId", "pageTitle", "currentVersionNo", "labelName", "commentId",
                "attachmentId", "notificationId", "homeSpace"]

    def prepare(self, caller: RawCaller, run_id: str) -> SandboxContext:
        self._remove_leftovers(caller)
        name = self._prefix + run_id
        try:
            space = caller.expect("POST", "/spaces", ok=(200, 201), json={"name": name, "description": SPACE_DESCRIPTION})
        except CallFailed as e:
            raise SandboxUnavailable(f"시험 스페이스를 만들지 못했다: {e}") from None
        ctx = SandboxContext(values={"spaceId": space["id"], "spaceName": space["name"], "homeSpace": space["id"]},
                             internal={"spaceId": space["id"]})
        try:
            if self._peer is not None:
                caller.expect("POST", f"/spaces/{space['id']}/members", ok=(200, 201),
                              json={"username": self._peer_user, "role": PEER_ROLE})
            page = caller.expect(
                "POST", "/pages", ok=(200, 201),
                # 제목에 검증 id를 넣어 유일하게 한다 (제목으로 찾는 상위 tool이 다른 페이지를 고르지 않게)
                json={"space": space["id"], "title": f"{PAGE_TITLE} {run_id}", "body": PAGE_BODY, "format": "markdown"},
            )
            label = caller.expect("POST", f"/pages/{page['id']}/labels", ok=(200, 201), json={"name": LABEL_NAME})
            comment = caller.expect(
                "POST", f"/pages/{page['id']}/comments", ok=(200, 201), json={"body": COMMENT_BODY, "format": "markdown"}
            )
            attachment = caller.expect(
                "POST", f"/pages/{page['id']}/attachments", ok=(200, 201),
                json={"filename": ATTACHMENT_NAME, "content": ATTACHMENT_TEXT, "encoding": "utf8"},
            )
            if self._peer is not None:
                ctx.values.update(self._notification(caller, page["id"], run_id))
            # 라벨·댓글·첨부가 버전을 올릴 수 있으니 다 만든 뒤 다시 읽어 지금 버전을 넘긴다
            page = caller.expect("GET", f"/pages/{page['id']}", ok=(200,))
        except CallFailed as e:
            self.cleanup(caller, ctx)
            raise SandboxUnavailable(f"시험 바탕 자료를 만들지 못했다: {e}") from None
        ctx.values.update(
            pageId=page["id"], pageTitle=page["title"], currentVersionNo=page["currentVersionNo"],
            labelName=label["name"], commentId=comment["id"], attachmentId=attachment["id"],
        )
        return ctx

    def _notification(self, caller: RawCaller, page_id: str, run_id: str) -> dict:
        """peer가 검증 계정을 멘션하고, 검증 계정의 알림에서 그 댓글의 알림 id를 찾는다."""
        mention = self._peer.expect("POST", f"/pages/{page_id}/comments", ok=(200, 201),
                                    json={"body": f"@{self._agent_user} 검증 알림입니다 {run_id}", "format": "markdown"})
        listed = caller.expect("GET", "/notifications", ok=(200,))
        found = next((n for n in listed.get("items", []) if n.get("commentId") == mention["id"]), None)
        if found is None:
            log.warning("peer mention did not produce a notification: run=%s", run_id)
            return {}
        return {"notificationId": found["id"]}

    def cleanup(self, caller: RawCaller, context: SandboxContext) -> CleanupReport:
        space_id = context.internal.get("spaceId")
        if not space_id:
            return CleanupReport(ok=True)
        try:
            self._drop_peer(caller, space_id)
            caller.expect("DELETE", f"/spaces/{space_id}", ok=(200, 204))
        except CallFailed as e:
            return CleanupReport(ok=False, failures=(f"시험 스페이스 {space_id} 삭제 실패: {e}",))
        return CleanupReport(ok=True)

    def _drop_peer(self, caller: RawCaller, space_id: str) -> None:
        """다른 멤버가 있으면 owner도 스페이스를 지울 수 없다(403). peer를 먼저 뺀다. 없으면(404) 그대로 둔다."""
        if self._peer is None:
            return
        caller.expect("DELETE", f"/spaces/{space_id}/members/{self._peer_user}", ok=(200, 204, 404))

    def _remove_leftovers(self, caller: RawCaller) -> None:
        """지난 검증이 남긴 스페이스(앞머리가 같은 것)를 먼저 지운다. 다른 스페이스는 건드리지 않는다."""
        try:
            listed = caller.expect("GET", "/spaces", ok=(200,), query={"q": self._prefix, "limit": str(LIST_LIMIT)})
        except CallFailed as e:
            raise SandboxUnavailable(f"스페이스 목록을 읽지 못했다: {e}") from None
        for space in listed.get("items", []):
            if space.get("name", "").startswith(self._prefix):
                try:
                    self._drop_peer(caller, space["id"])
                    caller.expect("DELETE", f"/spaces/{space['id']}", ok=(200, 204))
                except CallFailed:
                    pass  # 다음 검증에서 다시 지운다

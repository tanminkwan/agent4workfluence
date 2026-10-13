"""Workfluence 시험 환경: 검증마다 팀 스페이스를 만들고, 끝나면 통째로 지운다 (Phase 2 설계 11.6절).

시험 스택에는 쓸 만한 스페이스도 고정 시험 자료도 없다. 스페이스에는 key가 없고 id로 식별한다.
스페이스 삭제는 휴지통으로 가고 보존 기간 뒤 서버 배치가 지운다(공개 API에 완전 삭제 없음).
"""

from __future__ import annotations

from a4w_core.errors import SandboxUnavailable
from a4w_tools.sandbox import CallFailed, CleanupReport, RawCaller, SandboxContext, VerifySandbox

LIST_LIMIT = 1000  # spaces.list의 최대값 (명세)

# 바탕 자료의 글. @멘션을 쓰지 않는다(알림 부수효과).
SPACE_DESCRIPTION = "aiagent4workfluence 실호출 검증용. 검증이 끝나면 지운다."
PAGE_TITLE = "검증 바탕 페이지"
PAGE_BODY = "실호출 검증에 쓰는 페이지입니다."
LABEL_NAME = "a4w-verify"
COMMENT_BODY = "검증용 댓글입니다."
ATTACHMENT_NAME = "a4w-verify.txt"
ATTACHMENT_TEXT = "실호출 검증에 쓰는 첨부입니다."


class WorkfluenceSpaceSandbox(VerifySandbox):
    def __init__(self, *, space_prefix: str):
        if not space_prefix:
            raise ValueError("시험 스페이스 이름 앞머리가 비었다")
        self._prefix = space_prefix

    @classmethod
    def from_config(cls, cfg) -> "WorkfluenceSpaceSandbox":
        return cls(space_prefix=cfg.require("VERIFY_SPACE_PREFIX"))

    def context_keys(self) -> list[str]:
        # homeSpace: agent의 고정 맥락 키 (카탈로그 rules.yaml의 context). 검증 때는 시험 스페이스다
        return ["spaceId", "spaceName", "pageId", "pageTitle", "currentVersionNo", "labelName", "commentId", "attachmentId", "homeSpace"]

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

    def cleanup(self, caller: RawCaller, context: SandboxContext) -> CleanupReport:
        space_id = context.internal.get("spaceId")
        if not space_id:
            return CleanupReport(ok=True)
        try:
            caller.expect("DELETE", f"/spaces/{space_id}", ok=(200, 204))
        except CallFailed as e:
            return CleanupReport(ok=False, failures=(f"시험 스페이스 {space_id} 삭제 실패: {e}",))
        return CleanupReport(ok=True)

    def _remove_leftovers(self, caller: RawCaller) -> None:
        """지난 검증이 남긴 스페이스(앞머리가 같은 것)를 먼저 지운다. 다른 스페이스는 건드리지 않는다."""
        try:
            listed = caller.expect("GET", "/spaces", ok=(200,), query={"q": self._prefix, "limit": str(LIST_LIMIT)})
        except CallFailed as e:
            raise SandboxUnavailable(f"스페이스 목록을 읽지 못했다: {e}") from None
        for space in listed.get("items", []):
            if space.get("name", "").startswith(self._prefix):
                try:
                    caller.expect("DELETE", f"/spaces/{space['id']}", ok=(200, 204))
                except CallFailed:
                    pass  # 다음 검증에서 다시 지운다

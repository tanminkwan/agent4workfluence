"""manufacture 오류 (Phase 2 설계 16.4절). HTTP 상태로 바꾸는 일은 API 계층이 맡는다."""

from __future__ import annotations

from typing import Any

from a4w_core.errors import A4WError


class _WithDetails(A4WError):
    def __init__(self, message: str, details: Any = None):
        super().__init__(message)
        self.details = details


class DetectionUnavailable(A4WError):
    code = "DETECTION_UNAVAILABLE"


class ChangeSetOutOfOrder(_WithDetails):
    code = "CHANGESET_OUT_OF_ORDER"


class ApplyInProgress(_WithDetails):
    code = "APPLY_IN_PROGRESS"


class ApplyNotFound(A4WError):
    code = "APPLY_NOT_FOUND"


class ReviewNotFound(A4WError):
    code = "REVIEW_NOT_FOUND"


class ReviewAlreadyDecided(A4WError):
    code = "REVIEW_ALREADY_DECIDED"


class ToolVersionNotFound(A4WError):
    code = "TOOL_VERSION_NOT_FOUND"


class ToolsetNotFound(A4WError):
    code = "TOOLSET_NOT_FOUND"


class LlmAuthFailed(A4WError):
    code = "LLM_AUTH"


class VerifyFailedAll(_WithDetails):
    code = "VERIFY_FAILED_ALL"


class VerifyTimeout(A4WError):
    code = "VERIFY_TIMEOUT"

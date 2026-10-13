"""오류 코드와 예외.

코드는 고정 문자열이다. HTTP 상태로 바꾸는 일은 각 서비스의 API 계층이 맡는다.
"""


class A4WError(Exception):
    code = "INTERNAL_ERROR"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class ConfigError(A4WError):
    code = "CONFIG_ERROR"


class TargetNotFound(A4WError):
    code = "TARGET_NOT_FOUND"


class ChangeSetNotFound(A4WError):
    code = "CHANGESET_NOT_FOUND"


class DetectInProgress(A4WError):
    code = "DETECT_IN_PROGRESS"


class UnsupportedSpecSource(A4WError):
    code = "UNSUPPORTED_SPEC_SOURCE"


class SpecFetchFailed(A4WError):
    code = "SPEC_FETCH_FAILED"


class SpecHashMismatch(A4WError):
    code = "SPEC_HASH_MISMATCH"


class SpecInvalid(A4WError):
    code = "SPEC_INVALID"


class StorageError(A4WError):
    code = "STORAGE_ERROR"


# ── Phase 2: 타겟 창구 (Detection 보강)


class ScopeUnknown(A4WError):
    code = "SCOPE_UNKNOWN"


class SandboxUnavailable(A4WError):
    code = "SANDBOX_UNAVAILABLE"


class UnsupportedSandbox(A4WError):
    code = "UNSUPPORTED_SANDBOX"


class VerifyInProgress(A4WError):
    code = "VERIFY_IN_PROGRESS"


class VerificationNotFound(A4WError):
    code = "VERIFICATION_NOT_FOUND"


class VerifyRequestInvalid(A4WError):
    code = "VERIFY_REQUEST_INVALID"

    def __init__(self, message: str, details=None):
        super().__init__(message)
        self.details = details

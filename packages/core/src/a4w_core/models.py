"""서비스 사이에서 주고받는 값. 모두 바꿀 수 없는 값 객체다."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Change = Literal["added", "removed", "modified"]


@dataclass(frozen=True)
class OperationChange:
    method: str
    path: str
    operation_id: str | None
    change: Change
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    changed_fields: tuple[str, ...] | None = None
    doc_only: bool | None = None


@dataclass(frozen=True)
class DocumentChange:
    section: str
    change: Change


@dataclass(frozen=True)
class ReviewResult:
    needs_review: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ChangeSummary:
    added: int
    removed: int
    modified: int
    doc_only: int
    document_changes: int

    @classmethod
    def of(cls, operations, document_changes) -> "ChangeSummary":
        count = lambda kind: sum(1 for o in operations if o.change == kind)  # noqa: E731
        return cls(
            added=count("added"),
            removed=count("removed"),
            modified=count("modified"),
            doc_only=sum(1 for o in operations if o.doc_only),
            document_changes=len(document_changes),
        )


@dataclass(frozen=True)
class SpecVersion:
    target_id: str
    version: str
    git_commit: str
    size_bytes: int
    operation_count: int
    first_seen_at: str


@dataclass(frozen=True)
class ChangeSet:
    change_set_id: str
    target_id: str
    from_version: str | None
    to_version: str
    created_at: str
    operations: tuple[OperationChange, ...]
    document_changes: tuple[DocumentChange, ...]
    review: ReviewResult
    text_diff: str

    @property
    def summary(self) -> ChangeSummary:
        return ChangeSummary.of(self.operations, self.document_changes)

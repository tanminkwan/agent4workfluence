"""변경 식별 흐름과 변경분 조회 (설계 4절). interface에만 의존한다."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass

from a4w_core.errors import ChangeSetNotFound, SpecHashMismatch, TargetNotFound
from a4w_core.interfaces import ReviewPolicy, SpecNormalizer, SpecParser, SpecSource
from a4w_core.models import ChangeSet, ChangeSummary, SpecVersion
from a4w_core.openapi.operations import collect_operations

from .diff import OpenApiDiffer
from .ids import change_set_id, spec_version
from .ports import Clock, DetectionStore, SpecArchive, TargetLock
from .textdiff import TextDiffer


@dataclass(frozen=True)
class TargetRuntime:
    """타겟 하나의 구현 묶음 (조립 지점이 설정을 보고 만든다)."""

    source: SpecSource
    parser: SpecParser
    normalizer: SpecNormalizer
    policy: ReviewPolicy


@dataclass(frozen=True)
class DetectResult:
    status: str  # changed | unchanged
    target_id: str
    current_version: str | None = None
    change_set_id: str | None = None
    from_version: str | None = None
    to_version: str | None = None
    summary: ChangeSummary | None = None
    needs_review: bool | None = None
    review_reasons: tuple[str, ...] = ()

    @classmethod
    def changed(cls, cs: ChangeSet) -> "DetectResult":
        return cls(
            status="changed", target_id=cs.target_id, change_set_id=cs.change_set_id,
            from_version=cs.from_version, to_version=cs.to_version, summary=cs.summary,
            needs_review=cs.review.needs_review, review_reasons=cs.review.reasons,
        )

    @classmethod
    def unchanged(cls, target_id: str, version: str) -> "DetectResult":
        return cls(status="unchanged", target_id=target_id, current_version=version)


class DetectService:
    def __init__(
        self,
        *,
        targets: Mapping[str, TargetRuntime],
        archive: SpecArchive,
        store: DetectionStore,
        lock: TargetLock,
        differ: OpenApiDiffer,
        text_differ: TextDiffer,
        clock: Clock,
    ):
        self._targets = dict(targets)
        self._archive = archive
        self._store = store
        self._lock = lock
        self._differ = differ
        self._text = text_differ
        self._clock = clock

    def detect(self, target_id: str) -> DetectResult:
        rt = self._targets.get(target_id)
        if rt is None:
            raise TargetNotFound(f"등록되지 않은 타겟: {target_id}")
        with self._lock.hold(target_id):
            return self._detect(target_id, rt)

    def _detect(self, target_id: str, rt: TargetRuntime) -> DetectResult:
        current = self._store.current(target_id)
        remote_hash = rt.source.fetch_hash()
        if current and remote_hash == current.version:
            return DetectResult.unchanged(target_id, current.version)

        raw = rt.source.fetch_spec()
        version = spec_version(raw)
        if remote_hash is not None and version != remote_hash:
            raise SpecHashMismatch("받은 명세의 sha256이 해시 URL 값과 다르다")
        if current and version == current.version:
            return DetectResult.unchanged(target_id, current.version)

        new_doc = rt.parser.parse(raw)
        from_version = current.version if current else None
        existing = self._store.find_changeset(target_id, from_version, version)
        if existing:
            return DetectResult.changed(existing)

        old_raw = self._archive.read(target_id, current.git_commit) if current else None
        old_doc = rt.parser.parse(old_raw) if old_raw is not None else None
        operations, documents = self._differ.diff(old_doc, new_doc)
        review = rt.policy.evaluate(operations, documents)
        text_diff = self._text.diff(
            rt.normalizer.to_text(old_raw) if old_raw is not None else None,
            rt.normalizer.to_text(raw),
            old_label=f"{target_id}@{from_version or 'empty'}",
            new_label=f"{target_id}@{version}",
        )
        now = self._clock.now_iso()
        cs = ChangeSet(
            change_set_id=change_set_id(target_id, from_version, version),
            target_id=target_id, from_version=from_version, to_version=version, created_at=now,
            operations=operations, document_changes=documents, review=review, text_diff=text_diff,
        )
        commit = self._archive.commit(target_id, raw, version)
        record = SpecVersion(
            target_id=target_id, version=version, git_commit=commit, size_bytes=len(raw),
            operation_count=len(collect_operations(new_doc)), first_seen_at=now,
        )
        self._store.record(record, commit, cs, updated_at=now)
        return DetectResult.changed(cs)


class ChangeSetQuery:
    def __init__(self, *, targets: Collection[str], store: DetectionStore):
        self._targets = set(targets)
        self._store = store

    def get(self, target_id: str, change_set_id: str) -> ChangeSet:
        if target_id not in self._targets:
            raise TargetNotFound(f"등록되지 않은 타겟: {target_id}")
        cs = self._store.get_changeset(target_id, change_set_id)
        if cs is None:
            raise ChangeSetNotFound(f"변경분이 없다: {change_set_id}")
        return cs

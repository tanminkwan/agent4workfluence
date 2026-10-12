"""시험용 가짜 구현."""

from __future__ import annotations

import hashlib
from contextlib import contextmanager

from a4w_core.errors import DetectInProgress, StorageError
from a4w_core.interfaces import ReviewPolicy, SpecSource
from a4w_core.models import ReviewResult
from a4w_detection.ports import Clock, DetectionStore, SpecArchive, TargetLock


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class FakeSource(SpecSource):
    def __init__(self, raw: bytes, hash_value: str | None = "auto"):
        self.raw = raw
        self.hash_value = hash_value
        self.spec_calls = 0

    def fetch_hash(self):
        return sha(self.raw) if self.hash_value == "auto" else self.hash_value

    def fetch_spec(self):
        self.spec_calls += 1
        return self.raw


class FakeArchive(SpecArchive):
    def __init__(self):
        self.commits: dict[str, bytes] = {}
        self.head: dict[str, str] = {}

    def commit(self, target_id, raw, version):
        head = self.head.get(target_id)
        if head and self.commits[head] == raw:
            return head
        cid = f"c{len(self.commits) + 1}"
        self.commits[cid] = raw
        self.head[target_id] = cid
        return cid

    def read(self, target_id, commit):
        return self.commits[commit]


class MemoryStore(DetectionStore):
    def __init__(self):
        self.versions = {}
        self.state = {}
        self.changesets = {}
        self.fail_next_record = False

    def current(self, target_id):
        st = self.state.get(target_id)
        if not st:
            return None
        v = self.versions[(target_id, st[0])]
        return type(v)(v.target_id, v.version, st[1], v.size_bytes, v.operation_count, v.first_seen_at)

    def find_changeset(self, target_id, from_version, to_version):
        return next((c for c in self.changesets.values()
                     if (c.target_id, c.from_version, c.to_version) == (target_id, from_version, to_version)), None)

    def get_changeset(self, target_id, change_set_id):
        c = self.changesets.get(change_set_id)
        return c if c and c.target_id == target_id else None

    def record(self, version, commit, changeset, updated_at):
        if self.fail_next_record:
            self.fail_next_record = False
            raise StorageError("boom")
        self.versions.setdefault((version.target_id, version.version), version)
        self.changesets[changeset.change_set_id] = changeset
        self.state[version.target_id] = (version.version, commit)

    def check(self):
        pass


class FakeLock(TargetLock):
    def __init__(self):
        self.busy = set()

    @contextmanager
    def hold(self, target_id):
        if target_id in self.busy:
            raise DetectInProgress(target_id)
        self.busy.add(target_id)
        try:
            yield
        finally:
            self.busy.discard(target_id)


class FixedClock(Clock):
    def __init__(self, value="2026-10-12T00:00:00Z"):
        self.value = value

    def now_iso(self):
        return self.value


class NoReview(ReviewPolicy):
    def evaluate(self, operations, document_changes):
        reasons = ("OPERATION_REMOVED",) if any(o.change == "removed" for o in operations) else ()
        return ReviewResult(bool(reasons), reasons)

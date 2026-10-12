import pytest

from a4w_core.errors import ChangeSetNotFound, SpecHashMismatch, SpecInvalid, StorageError, TargetNotFound, DetectInProgress
from a4w_core.normalizers import IdentityNormalizer
from a4w_core.spec_parsers import YamlSpecParser
from a4w_detection.diff import OpenApiDiffer
from a4w_detection.ids import change_set_id
from a4w_detection.service import ChangeSetQuery, DetectService, TargetRuntime
from a4w_detection.textdiff import TextDiffer

from ..fakes import FakeArchive, FakeLock, FakeSource, FixedClock, MemoryStore, NoReview, sha

V1 = b"openapi: 3.1.0\ninfo: {title: t, version: '1'}\npaths:\n  /a:\n    get: {operationId: a.get, responses: {}}\n"
V2 = V1 + b"  /b:\n    post: {operationId: b.post, responses: {}}\n"
V3 = b"openapi: 3.1.0\ninfo: {title: t, version: '1'}\npaths:\n  /b:\n    post: {operationId: b.post, responses: {}}\n"


class Env:
    def __init__(self, raw=V1):
        self.source = FakeSource(raw)
        self.archive = FakeArchive()
        self.store = MemoryStore()
        self.lock = FakeLock()
        rt = TargetRuntime(source=self.source, parser=YamlSpecParser(), normalizer=IdentityNormalizer(), policy=NoReview())
        self.service = DetectService(
            targets={"wf": rt}, archive=self.archive, store=self.store, lock=self.lock,
            differ=OpenApiDiffer(), text_differ=TextDiffer(context_lines=3), clock=FixedClock(),
        )
        self.query = ChangeSetQuery(targets={"wf"}, store=self.store)


def test_unknown_target():
    with pytest.raises(TargetNotFound):
        Env().service.detect("nope")


def test_first_run_creates_full_changeset():
    env = Env()
    r = env.service.detect("wf")
    assert r.status == "changed"
    assert r.from_version is None and r.to_version == sha(V1)
    assert r.change_set_id == change_set_id("wf", None, sha(V1))
    cs = env.query.get("wf", r.change_set_id)
    assert [(o.method, o.path, o.change) for o in cs.operations] == [("GET", "/a", "added")]
    assert cs.summary.added == 1
    assert "+openapi: 3.1.0" in cs.text_diff
    assert env.store.current("wf").operation_count == 1


def test_second_call_with_same_spec_is_unchanged_without_fetching_spec():
    env = Env()
    env.service.detect("wf")
    calls = env.source.spec_calls
    r = env.service.detect("wf")
    assert r.status == "unchanged" and r.current_version == sha(V1)
    assert env.source.spec_calls == calls
    assert len(env.store.changesets) == 1


def test_change_produces_diff_against_previous():
    env = Env()
    first = env.service.detect("wf")
    env.source.raw = V2
    r = env.service.detect("wf")
    assert r.status == "changed" and r.from_version == first.to_version and r.to_version == sha(V2)
    assert r.summary.added == 1 and r.summary.removed == 0
    assert r.needs_review is False


def test_removal_is_flagged_by_policy():
    env = Env(V2)
    env.service.detect("wf")
    env.source.raw = V3
    r = env.service.detect("wf")
    assert r.needs_review is True and r.review_reasons == ("OPERATION_REMOVED",)


def test_hash_mismatch_stores_nothing():
    env = Env()
    env.source.hash_value = "0" * 64
    with pytest.raises(SpecHashMismatch):
        env.service.detect("wf")
    assert env.store.current("wf") is None and env.archive.commits == {}


def test_spec_equal_to_current_is_unchanged_even_if_hash_url_differs():
    env = Env()
    env.service.detect("wf")
    env.source.hash_value = None  # 해시를 주지 않는 경우와 같은 흐름
    r = env.service.detect("wf")
    assert r.status == "unchanged"


def test_invalid_spec_stores_nothing():
    env = Env(b"not: openapi\n")
    with pytest.raises(SpecInvalid):
        env.service.detect("wf")
    assert env.archive.commits == {}


def test_concurrent_detect_is_rejected():
    env = Env()
    env.lock.busy.add("wf")
    with pytest.raises(DetectInProgress):
        env.service.detect("wf")


def test_crash_between_commit_and_record_recovers_on_retry():
    env = Env()
    env.store.fail_next_record = True
    with pytest.raises(StorageError):
        env.service.detect("wf")
    assert env.store.current("wf") is None and len(env.archive.commits) == 1
    r = env.service.detect("wf")
    assert r.status == "changed" and r.change_set_id == change_set_id("wf", None, sha(V1))
    assert len(env.archive.commits) == 1  # 같은 내용이라 커밋이 늘지 않는다


def test_revert_creates_new_changeset_back():
    env = Env(V1)
    env.service.detect("wf")
    env.source.raw = V2
    env.service.detect("wf")
    env.source.raw = V1
    r = env.service.detect("wf")
    assert (r.from_version, r.to_version) == (sha(V2), sha(V1))
    assert r.summary.removed == 1
    assert env.store.current("wf").version == sha(V1)


def test_query_unknown_target_and_changeset():
    env = Env()
    with pytest.raises(TargetNotFound):
        env.query.get("nope", "cs-x")
    with pytest.raises(ChangeSetNotFound):
        env.query.get("wf", "cs-x")

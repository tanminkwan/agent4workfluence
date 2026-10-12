"""Detection–Manufacture 계약 (설계 9.2절). 바꾸려면 이 시험을 먼저 바꾼다."""

from fastapi.testclient import TestClient

from a4w_core.normalizers import IdentityNormalizer
from a4w_core.spec_parsers import YamlSpecParser
from a4w_detection.app import AppContext, create_app
from a4w_detection.diff import OpenApiDiffer
from a4w_detection.service import ChangeSetQuery, DetectService, TargetRuntime
from a4w_detection.textdiff import TextDiffer

from ..fakes import FakeArchive, FakeLock, FakeSource, FixedClock, MemoryStore, NoReview

OLD = b"""openapi: 3.1.0
info: {title: t, version: '1'}
components:
  schemas:
    Err: {type: object, properties: {code: {type: string}}}
paths:
  /keep:
    get: {operationId: keep, responses: {'400': {description: e, content: {application/json: {schema: {$ref: '#/components/schemas/Err'}}}}}}
  /gone:
    delete: {operationId: gone, responses: {}}
"""
NEW = b"""openapi: 3.1.0
info: {title: t, version: '1'}
components:
  schemas:
    Err: {type: object, properties: {code: {type: integer}}}
paths:
  /keep:
    get: {operationId: keep, responses: {'400': {description: e, content: {application/json: {schema: {$ref: '#/components/schemas/Err'}}}}}}
  /new:
    post: {operationId: new, security: [{bearerAuth: [write]}], responses: {}}
"""

HEADER = {"changeSetId", "targetId", "fromVersion", "toVersion", "createdAt", "needsReview",
          "reviewReasons", "summary", "operations", "documentChanges"}
OP_COMMON = {"method", "path", "operationId", "change"}


def fetch_changeset():
    src = FakeSource(OLD)
    store = MemoryStore()
    rt = TargetRuntime(source=src, parser=YamlSpecParser(), normalizer=IdentityNormalizer(), policy=NoReview())
    svc = DetectService(targets={"wf": rt}, archive=FakeArchive(), store=store, lock=FakeLock(),
                        differ=OpenApiDiffer(), text_differ=TextDiffer(3), clock=FixedClock())
    c = TestClient(create_app(AppContext(detect=svc, query=ChangeSetQuery(targets={"wf"}, store=store),
                                         store=store, auth_mode="none", api_token=None)))
    c.post("/v1/detect", json={"targetId": "wf"})
    src.raw = NEW
    cs_id = c.post("/v1/detect", json={"targetId": "wf"}).json()["changeSetId"]
    return c.get(f"/v1/changesets/{cs_id}", params={"targetId": "wf"}).json()


def test_header_fields():
    body = fetch_changeset()
    assert set(body) == HEADER
    assert set(body["summary"]) == {"added", "removed", "modified", "docOnly", "documentChanges"}


def test_operation_items_by_kind():
    ops = {o["path"]: o for o in fetch_changeset()["operations"]}
    assert set(ops["/new"]) == OP_COMMON | {"after"}
    assert set(ops["/gone"]) == OP_COMMON | {"before"}
    assert set(ops["/keep"]) == OP_COMMON | {"before", "after", "changedFields", "docOnly"}
    assert ops["/new"]["method"] == "POST" and ops["/new"]["change"] == "added"


def test_fragments_are_self_contained():
    ops = {o["path"]: o for o in fetch_changeset()["operations"]}
    schema = ops["/keep"]["after"]["responses"]["400"]["content"]["application/json"]["schema"]
    assert schema == {"type": "object", "properties": {"code": {"type": "integer"}}}
    assert "$ref" not in str(ops["/keep"]["after"])
    assert ops["/new"]["after"]["security"] == [{"bearerAuth": ["write"]}]


def test_fragment_keys():
    after = {o["path"]: o for o in fetch_changeset()["operations"]}["/new"]["after"]
    assert set(after) <= {"operationId", "method", "path", "summary", "description", "tags",
                          "parameters", "requestBody", "responses", "security"}

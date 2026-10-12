import pytest

from a4w_core.models import DocumentChange, OperationChange
from a4w_target_workfluence.review_policy import WorkfluenceV1AdditivePolicy


def oc(change, doc_only=None):
    return OperationChange(method="GET", path="/x", operation_id="x", change=change, doc_only=doc_only)


def evaluate(ops=(), docs=()):
    return WorkfluenceV1AdditivePolicy().evaluate(ops, docs)


def test_added_only_needs_no_review():
    r = evaluate([oc("added"), oc("added")])
    assert r.needs_review is False and r.reasons == ()


def test_removed_needs_review():
    assert evaluate([oc("removed")]).reasons == ("OPERATION_REMOVED",)


def test_modified_needs_review_unless_doc_only():
    assert evaluate([oc("modified", doc_only=False)]).reasons == ("OPERATION_CHANGED",)
    assert evaluate([oc("modified", doc_only=True)]).needs_review is False


@pytest.mark.parametrize("section", ["security", "components.securitySchemes"])
def test_security_change_needs_review(section):
    assert evaluate(docs=[DocumentChange(section, "modified")]).reasons == ("SECURITY_CHANGED",)


def test_openapi_version_change_needs_review():
    assert evaluate(docs=[DocumentChange("openapi", "modified")]).reasons == ("SPEC_VERSION_CHANGED",)


def test_first_run_document_additions_need_no_review():
    docs = [DocumentChange(s, "added") for s in ("openapi", "info", "servers", "tags", "components.securitySchemes")]
    assert evaluate([oc("added")], docs).needs_review is False


@pytest.mark.parametrize("section", ["info", "servers", "tags"])
def test_informational_sections_need_no_review(section):
    assert evaluate(docs=[DocumentChange(section, "modified")]).needs_review is False


def test_reasons_are_unique_and_sorted():
    r = evaluate([oc("removed"), oc("modified", False), oc("removed")], [DocumentChange("security", "modified")])
    assert r.reasons == ("OPERATION_CHANGED", "OPERATION_REMOVED", "SECURITY_CHANGED")
    assert r.needs_review is True


def test_register_adds_policy():
    from a4w_core.registry import Registry
    from a4w_target_workfluence import register
    reg = Registry()
    register(reg)
    assert reg.names("review_policy") == ["workfluence_v1_additive"]

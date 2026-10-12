import hashlib

from a4w_detection.ids import change_set_id, spec_version


def test_spec_version_is_sha256_hex():
    assert spec_version(b"x") == hashlib.sha256(b"x").hexdigest()


def test_change_set_id_is_deterministic_and_prefixed():
    a = change_set_id("wf", None, "v1")
    assert a == change_set_id("wf", None, "v1")
    assert a.startswith("cs-") and len(a) == 3 + 24
    assert a == "cs-" + hashlib.sha256(b"wf\nempty\nv1").hexdigest()[:24]


def test_change_set_id_differs_by_inputs():
    ids = {change_set_id("wf", None, "v1"), change_set_id("wf", "v0", "v1"), change_set_id("other", None, "v1")}
    assert len(ids) == 3

"""정의 받기·교체·알림 (Phase 3 설계 6.4·6.5절, AC-T5·T7)."""

from __future__ import annotations

import anyio
import httpx
import pytest

from a4w_mcp.catalog import ServingCatalog
from a4w_mcp.refresher import Refresher
from a4w_mcp.snapshot import Exposure
from a4w_mcp.source import ManufactureToolsetSource

from ..fakes import TARGET, FakeManufacture, add_comment, bundle, search_pages

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Notes:
    def __init__(self):
        self.count = 0

    async def tools_changed(self):
        self.count += 1


EXPOSE = Exposure(scopes=None, max_risk="danger", groups=None, separator="__", name_max=64, context={"homeSpace": "h"})


def make(fake):
    catalog = ServingCatalog(targets=[TARGET], clock=lambda: "2026-10-14T00:00:00Z")
    notes = Notes()
    src = ManufactureToolsetSource(base_url="http://m:1", token=None, timeout=5, transport=httpx.MockTransport(fake))
    r = Refresher(source=src, exposures={TARGET: EXPOSE}, catalog=catalog, notifier=notes,
                  refresh_seconds=60, retry_seconds=10)
    return r, catalog, notes


async def test_starts_empty_then_serves_after_first_success():
    fake = FakeManufacture()
    r, catalog, notes = make(fake)
    assert catalog.snapshot().served == {}
    assert await r.refresh_once() is False
    assert catalog.status()["targets"][TARGET]["lastError"] == "TOOLSET_MISSING"
    fake.publish(bundle())
    assert await r.refresh_once() is True
    assert len(catalog.snapshot().served) == 4 and notes.count == 1
    st = catalog.status()["targets"][TARGET]
    assert st["toolVersion"] == "tv-000001-aaaaaaaa" and st["lastError"] is None and st["served"] == 4


async def test_same_version_is_not_fetched_again():
    fake = FakeManufacture(bundle())
    r, catalog, notes = make(fake)
    await r.refresh_once()
    before = len(fake.requests)
    assert await r.refresh_once() is True
    assert len(fake.requests) == before + 1  # current(304)만
    assert notes.count == 1


async def test_new_version_replaces_and_notifies():
    fake = FakeManufacture(bundle())
    r, catalog, notes = make(fake)
    await r.refresh_once()
    old = catalog.snapshot()
    fake.publish(bundle([search_pages(), add_comment("r-00000000000f")], version="tv-000002-bbbbbbbb"))
    await r.refresh_once()
    new = catalog.snapshot()
    assert new is not old and set(new.served) == {"wf__search_pages", "wf__add_comment"}
    assert new.served["wf__add_comment"].definition["revision"] == "r-00000000000f"
    assert old.served["wf__add_comment"].definition["revision"] == "r-000000000002"  # 옛 Snapshot은 그대로
    assert notes.count == 2


async def test_rejected_bundle_keeps_previous():
    fake = FakeManufacture(bundle())
    r, catalog, notes = make(fake)
    await r.refresh_once()
    broken = bundle(version="tv-000002-bbbbbbbb")
    broken["tools"]["add_comment"]["revision"] = "r-999999999999"
    fake.publish(broken)
    assert await r.refresh_once() is False
    assert catalog.snapshot().tool_versions == {TARGET: "tv-000001-aaaaaaaa"}
    assert catalog.status()["targets"][TARGET]["lastError"] == "TOOLSET_MISMATCH"
    assert notes.count == 1


async def test_unavailable_keeps_previous():
    fake = FakeManufacture(bundle())
    r, catalog, notes = make(fake)
    await r.refresh_once()
    fake.fail = 503
    assert await r.refresh_once() is False
    assert len(catalog.snapshot().served) == 4
    assert catalog.status()["targets"][TARGET]["lastError"] == "TOOLSET_UNAVAILABLE"
    fake.fail = None
    assert await r.refresh_once() is True
    assert catalog.status()["targets"][TARGET]["lastError"] is None


async def test_run_forever_uses_retry_interval_on_failure():
    fake = FakeManufacture()
    r, catalog, notes = make(fake)
    waits = []

    async def sleep(seconds):
        waits.append(seconds)
        if len(waits) == 1:
            fake.publish(bundle())
        if len(waits) == 2:
            raise anyio.get_cancelled_exc_class()()

    with pytest.raises(anyio.get_cancelled_exc_class()):
        await r.run_forever(sleep=sleep)
    assert waits == [10, 60] and notes.count == 1


async def test_refresh_now_runs_once_more():
    fake = FakeManufacture(bundle())
    r, catalog, notes = make(fake)
    await r.refresh_once()
    fake.publish(bundle([search_pages()], version="tv-000002-bbbbbbbb"))
    await r.refresh_now()
    assert set(catalog.snapshot().served) == {"wf__search_pages"}

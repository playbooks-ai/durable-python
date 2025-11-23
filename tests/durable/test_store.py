from pathlib import Path

import pytest

from durable import DiskStateStore, InMemoryStateStore


@pytest.mark.asyncio
async def test_in_memory_state_store_round_trip():
    store = InMemoryStateStore()
    await store.save("one", {"value": 123})

    loaded = await store.load("one")

    assert loaded == {"value": 123}


@pytest.mark.asyncio
async def test_disk_state_store_round_trip(tmp_path: Path):
    store = DiskStateStore(str(tmp_path))
    await store.save("two", {"status": "ok"})

    loaded = await store.load("two")

    assert loaded == {"status": "ok"}
    assert (tmp_path / "two.pkl").exists()


@pytest.mark.asyncio
async def test_disk_state_store_overwrite(tmp_path: Path):
    store = DiskStateStore(str(tmp_path))
    await store.save("item", {"first": True})
    await store.save("item", {"first": False, "second": True})

    loaded = await store.load("item")

    assert loaded == {"first": False, "second": True}

import asyncio

import pytest

from durable import DurableBackend, InMemoryStateStore


@pytest.mark.asyncio
async def test_durable_backend_load_save_delegates():
    store = InMemoryStateStore()
    backend = DurableBackend(state_store=store)

    await backend.save_state("abc", {"k": "v"})
    loaded = await backend.load_state("abc")

    assert loaded == {"k": "v"}


@pytest.mark.asyncio
async def test_wait_for_event_unblocks_on_publish():
    backend = DurableBackend(InMemoryStateStore())
    instance_id = "i-1"
    event_name = "ready"

    waiter = asyncio.create_task(backend.wait_for_event(instance_id, event_name))
    await asyncio.sleep(0)
    backend.publish_event(instance_id, event_name)

    await asyncio.wait_for(waiter, timeout=1)
    assert waiter.done()

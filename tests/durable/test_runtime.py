import pytest

from durable import (
    DurableBackend,
    InMemoryStateStore,
    PauseForEventException,
    make_durable,
)


async def add_numbers(a: int, b: int):
    return a + b


async def pause_flow():
    raise PauseForEventException("go")
    return "done"


@pytest.mark.asyncio
async def test_runtime_executes_program_and_persists_state():
    durable_add = make_durable(add_numbers)
    backend = DurableBackend(InMemoryStateStore())
    instance_id = "add-1"

    result = await durable_add(
        1, 2, orchestration_backend=backend, instance_id=instance_id
    )

    assert result == 3
    saved = await backend.load_state(instance_id)
    assert "call_stack" in saved


@pytest.mark.asyncio
async def test_runtime_pause_and_resume():
    durable_flow = make_durable(pause_flow)
    backend = DurableBackend(InMemoryStateStore())
    instance_id = "flow-1"

    with pytest.raises(PauseForEventException):
        await durable_flow(orchestration_backend=backend, instance_id=instance_id)

    saved = await backend.load_state(instance_id)
    assert saved is not None

    backend.publish_event(instance_id, "go")
    result = await durable_flow(orchestration_backend=backend, instance_id=instance_id)

    assert result == "done"

import asyncio

from durable import (
    DurableBackend,
    InMemoryStateStore,
    PauseForEventException,
    make_durable,
)


async def workflow(name: str):
    print(f"starting workflow for {name}")
    raise PauseForEventException("go")
    return f"done for {name}"


async def main():
    backend = DurableBackend(InMemoryStateStore())
    durable_workflow = make_durable(workflow)

    try:
        await durable_workflow(
            "Terry", orchestration_backend=backend, instance_id="demo-1"
        )
    except PauseForEventException as e:
        print(f"paused waiting for {e.event}")

    backend.publish_event("demo-1", "go")
    result = await durable_workflow(
        "Terry", orchestration_backend=backend, instance_id="demo-1"
    )
    print(f"result: {result}")


if __name__ == "__main__":
    asyncio.run(main())

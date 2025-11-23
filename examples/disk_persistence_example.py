import asyncio
from pathlib import Path

from durable import DurableBackend, DiskStateStore, PauseForEventException, make_durable


async def order_workflow(order_id: str):
    print(f"processing order {order_id}")
    raise PauseForEventException("payment-confirmed")
    return f"order {order_id} complete"


async def main():
    store_path = Path("./workflows")
    store = DiskStateStore(str(store_path))
    backend = DurableBackend(store)
    durable_order = make_durable(order_workflow)
    instance = "order-1"

    # First run pauses and persists to disk
    try:
        await durable_order("123", orchestration_backend=backend, instance_id=instance)
    except PauseForEventException:
        print("waiting for payment-confirmed")

    # Later (or after restart) deliver event and resume
    backend.publish_event(instance, "payment-confirmed")
    result = await durable_order(
        "123", orchestration_backend=backend, instance_id=instance
    )
    print(f"result: {result}")


if __name__ == "__main__":
    asyncio.run(main())

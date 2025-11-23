import pytest

from durable import (
    CallStack,
    CheckpointException,
    FunctionCall,
    PauseForEventException,
    _convert_args_to_kwargs,
)


async def add_numbers(a: int, b: int):
    return a + b


async def noop():
    return None


@pytest.mark.asyncio
async def test_call_stack_executes_function_call():
    call = FunctionCall(add_numbers, kwargs={"a": 1, "b": 2})
    stack = CallStack()
    stack.push(call)

    namespace = {
        "call_stack": stack,
        "FunctionCall": FunctionCall,
        "CheckpointException": CheckpointException,
        "PauseForEventException": PauseForEventException,
        "_convert_args_to_kwargs": _convert_args_to_kwargs,
    }

    await stack.resume(namespace=namespace)

    assert stack.is_empty()
    assert stack.last_result == 3


def test_call_stack_serialization_round_trip():
    call = FunctionCall(noop)
    stack = CallStack([call])

    state = stack.to_state()
    restored = CallStack.from_state(state)

    assert len(restored.stack) == 1
    assert restored.pending_return_values is None
    assert restored.last_result is None

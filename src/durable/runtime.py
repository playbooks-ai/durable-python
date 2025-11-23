"""DurableRuntime coordinates execution of durable programs."""

from __future__ import annotations

import uuid
from typing import Any, Callable

from .backend import DurableBackend, OrchestrationBackend
from .exceptions import CheckpointException, PauseForEventException
from .stack import CallStack, FunctionCall
from .utils import _convert_args_to_kwargs


class DurableProgram:
    """Wrapper around a user async function that can build FunctionCalls."""

    def __init__(self, fn: Callable[..., Any]):
        self.fn = fn
        self.namespace = dict(getattr(fn, "__globals__", {}))

    def create_call(self, *args, **kwargs) -> FunctionCall:
        kwargs_map = _convert_args_to_kwargs(self.fn, list(args), kwargs)
        return FunctionCall(fn=self.fn, kwargs=kwargs_map)

    async def __call__(
        self,
        *args,
        orchestration_backend: OrchestrationBackend | None = None,
        instance_id: str | None = None,
        **kwargs,
    ):
        runtime = DurableRuntime(
            program=self,
            backend=orchestration_backend or DurableBackend(),
            instance_id=instance_id,
        )
        return await runtime.run(*args, **kwargs)


class DurableRuntime:
    """Drives execution using a CallStack and OrchestrationBackend."""

    def __init__(
        self,
        program: DurableProgram,
        backend: OrchestrationBackend,
        instance_id: str | None = None,
    ):
        self.program = program
        self.backend = backend
        self.instance_id = instance_id or str(uuid.uuid4())
        self.call_stack = CallStack()
        self.namespace = self._build_namespace()

    def _build_namespace(self) -> dict:
        base = dict(self.program.namespace)
        base.update(
            {
                "call_stack": self.call_stack,
                "FunctionCall": FunctionCall,
                "CheckpointException": CheckpointException,
                "PauseForEventException": PauseForEventException,
                "_convert_args_to_kwargs": _convert_args_to_kwargs,
            }
        )
        return base

    def _serialize_state(self) -> dict:
        return {
            "instance_id": self.instance_id,
            "call_stack": self.call_stack.to_state(),
        }

    def _restore_state(self, state: dict) -> None:
        if "call_stack" in state:
            self.call_stack = CallStack.from_state(state["call_stack"])
            self.namespace = self._build_namespace()

    async def run(self, *args, **kwargs):
        existing_state = await self.backend.load_state(self.instance_id)
        if existing_state:
            self._restore_state(existing_state)
        elif self.call_stack.is_empty():
            initial_call = self.program.create_call(*args, **kwargs)
            self.call_stack.push(initial_call)
            self.namespace = self._build_namespace()

        while True:
            try:
                await self.call_stack.resume(namespace=self.namespace)
                break
            except PauseForEventException as exc:
                await self.backend.save_state(
                    self.instance_id,
                    self._serialize_state(),
                )
                raise exc

        await self.backend.save_state(self.instance_id, self._serialize_state())
        return self.call_stack.last_result

    def publish_event(self, event_name: str) -> None:
        """Helper to surface backend event publication."""
        publish = getattr(self.backend, "publish_event", None)
        if callable(publish):
            publish(self.instance_id, event_name)

    async def wait_for_event(self, event_name: str):
        """Delegate to backend wait and then resume."""
        await self.backend.wait_for_event(self.instance_id, event_name)
        return await self.run()

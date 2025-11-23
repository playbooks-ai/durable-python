"""Orchestration backends."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Any

from .store import InMemoryStateStore, StateStore


class OrchestrationBackend(ABC):
    """Interface for pluggable orchestration layers."""

    @abstractmethod
    async def load_state(self, instance_id: str) -> dict | None: ...

    @abstractmethod
    async def save_state(self, instance_id: str, state: dict) -> None: ...

    @abstractmethod
    async def wait_for_event(self, instance_id: str, event_name: str) -> None: ...


class DurableBackend(OrchestrationBackend):
    """Default backend backed by a StateStore and in-memory event bus."""

    def __init__(self, state_store: StateStore | None = None):
        self.state_store = state_store or InMemoryStateStore()
        self._events: dict[str, dict[str, asyncio.Future[Any]]] = defaultdict(dict)

    async def load_state(self, instance_id: str) -> dict | None:
        return await self.state_store.load(instance_id)

    async def save_state(self, instance_id: str, state: dict) -> None:
        await self.state_store.save(instance_id, state)

    async def wait_for_event(self, instance_id: str, event_name: str) -> None:
        instance_events = self._events[instance_id]
        future = instance_events.get(event_name)
        if future is None or future.done():
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            instance_events[event_name] = future
        await future

    def publish_event(self, instance_id: str, event_name: str) -> None:
        """Notify any waiters for an event."""
        instance_events = self._events[instance_id]
        future = instance_events.get(event_name)
        loop = asyncio.get_running_loop()
        if future is None or future.done():
            future = loop.create_future()
            instance_events[event_name] = future
        if not future.done():
            future.set_result(True)

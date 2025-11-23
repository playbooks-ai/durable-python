"""Persistence store abstractions."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from pathlib import Path

import cloudpickle


class StateStore(ABC):
    """Persist runtime state for a durable instance."""

    @abstractmethod
    async def load(self, instance_id: str) -> dict | None: ...

    @abstractmethod
    async def save(self, instance_id: str, state: dict) -> None: ...


class InMemoryStateStore(StateStore):
    """Ephemeral in-memory store for testing/dev."""

    def __init__(self):
        self._state: dict[str, dict] = {}

    async def load(self, instance_id: str) -> dict | None:
        return self._state.get(instance_id)

    async def save(self, instance_id: str, state: dict) -> None:
        self._state[instance_id] = state


class DiskStateStore(StateStore):
    """File-based persistence using cloudpickle."""

    def __init__(self, path: str):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)

    async def load(self, instance_id: str) -> dict | None:
        file_path = self.path / f"{instance_id}.pkl"
        if not file_path.exists():
            return None

        def _load() -> dict:
            with open(file_path, "rb") as fh:
                return cloudpickle.load(fh)

        return await asyncio.to_thread(_load)

    async def save(self, instance_id: str, state: dict) -> None:
        file_path = self.path / f"{instance_id}.pkl"

        def _save() -> None:
            with open(file_path, "wb") as fh:
                cloudpickle.dump(state, fh)

        await asyncio.to_thread(_save)

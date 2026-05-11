"""Model load/unload manager. Decoupled from Qwen3-TTS for testability."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


class ModelManager[T]:
    """Caches loaded models keyed by short name; unloads after idle."""

    def __init__(
        self,
        loader: Callable[[str], T],
        unload_after: float,
        on_unload: Callable[[], None] | None = None,
    ) -> None:
        self._loader = loader
        self._unload_after = unload_after
        self._on_unload = on_unload or (lambda: None)
        self._registry: dict[str, str] = {}
        self._loaded: dict[str, T] = {}
        self._lock = threading.Lock()
        self._last_used = time.monotonic()

    def register(self, key: str, model_id: str) -> None:
        self._registry[key] = model_id

    def get(self, key: str) -> T:
        if key not in self._registry:
            raise KeyError(f"unknown model key: {key}")
        with self._lock:
            self._last_used = time.monotonic()
            if key not in self._loaded:
                self._loaded[key] = self._loader(self._registry[key])
            return self._loaded[key]

    def unload_all(self) -> None:
        with self._lock:
            if not self._loaded:
                return
            names = list(self._loaded)
            self._loaded.clear()
        self._on_unload()
        logger.info("unloaded models: %s", ", ".join(names))

    def loaded_keys(self) -> list[str]:
        with self._lock:
            return list(self._loaded)

    def last_used(self) -> float:
        return self._last_used

    async def run_unload_watcher(self, poll_interval: float = 5.0) -> None:
        # unload_after <= 0 means "keep loaded forever" — skip the watcher entirely.
        if self._unload_after <= 0:
            logger.info("idle unload disabled (unload_after=%s)", self._unload_after)
            return
        while True:
            await asyncio.sleep(poll_interval)
            with self._lock:
                if not self._loaded:
                    continue
                idle = time.monotonic() - self._last_used
            if idle >= self._unload_after:
                self.unload_all()

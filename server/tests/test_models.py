import asyncio
import time

import pytest

from mimic_server.models import ModelManager


class FakeModel:
    def __init__(self, name: str) -> None:
        self.name = name


def test_loads_on_demand_and_caches():
    calls: list[str] = []

    def loader(model_id: str) -> FakeModel:
        calls.append(model_id)
        return FakeModel(model_id)

    mm = ModelManager(loader=loader, unload_after=60)
    mm.register("clone", "Qwen/clone-id")

    a = mm.get("clone")
    b = mm.get("clone")

    assert a is b
    assert calls == ["Qwen/clone-id"]


def test_get_unknown_key_raises():
    mm = ModelManager(loader=lambda mid: FakeModel(mid), unload_after=60)
    with pytest.raises(KeyError):
        mm.get("custom")


def test_unload_all_clears_cache():
    mm = ModelManager(loader=lambda mid: FakeModel(mid), unload_after=60)
    mm.register("clone", "Qwen/c")
    mm.get("clone")
    mm.unload_all()
    assert mm.loaded_keys() == []


def test_status_reports_loaded_keys():
    mm = ModelManager(loader=lambda mid: FakeModel(mid), unload_after=60)
    mm.register("clone", "Qwen/c")
    mm.register("custom", "Qwen/cv")
    mm.get("clone")
    assert mm.loaded_keys() == ["clone"]


@pytest.mark.asyncio
async def test_idle_watcher_unloads_after_timeout():
    mm = ModelManager(loader=lambda mid: FakeModel(mid), unload_after=0.05)
    mm.register("clone", "Qwen/c")
    mm.get("clone")

    task = asyncio.create_task(mm.run_unload_watcher(poll_interval=0.01))
    try:
        await asyncio.sleep(0.2)
        assert mm.loaded_keys() == []
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


def test_get_resets_idle_timer(monkeypatch):
    mm = ModelManager(loader=lambda mid: FakeModel(mid), unload_after=60)
    mm.register("clone", "Qwen/c")
    mm.get("clone")
    t0 = mm.last_used()
    time.sleep(0.01)
    mm.get("clone")
    assert mm.last_used() > t0

"""Offline startup, service and automatic-recording test with HA API fakes.

The fakes exercise task ownership and integration logic but do not replace a
live Home Assistant 2026.11 acceptance test.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import importlib.util
import math
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from smart_scale_bmi import database
from test_storage_and_bmi import profile


class FakeVoluptuous(types.ModuleType):
    class Invalid(ValueError):
        pass
    def Schema(self, data):
        return data
    def Required(self, key, **_):
        return key
    def Optional(self, key, **_):
        return key
    def Coerce(self, f):
        return f
    def Range(self, **_):
        return lambda val: val
    def All(self, *args):
        return lambda val: val


class FakeEntry:
    def __init__(self):
        self.entry_id = "entry1"
        self.title = "Test"
        self.data = profile()
        self.options = {}
        self.callbacks = []
        self.tasks = []

    def async_create_background_task(self, hass, coroutine, name, eager_start=False):
        task = asyncio.create_task(coroutine, name=name)
        self.tasks.append(task)
        return task

    def async_on_unload(self, callback):
        self.callbacks.append(callback)

    def add_update_listener(self, listener):
        return lambda: None


class FakeHass:
    def __init__(self, tmp_path):
        self.data = {}
        self.state = "running"
        self.config = SimpleNamespace(path=lambda *parts: str(Path(tmp_path, *parts)), time_zone="Asia/Bangkok")
        self.states = {}
        self.actions = {}
        self.background = []
        self.listeners = {}
        self.dispatches = []
        self.static_paths = []
        self.http = SimpleNamespace(async_register_static_paths=self.register_static)
        self.services = SimpleNamespace(async_register=self.register_action)
        self.config_entries = SimpleNamespace(
            async_forward_entry_setups=self.forward,
            async_unload_platforms=self.unload_platforms,
        )
        self.bus = SimpleNamespace(async_listen_once=lambda *args: lambda: None)
        self.resource_manager = SimpleNamespace(
            items=[],
            async_get_info=self.resource_info,
            async_items=lambda: self.resource_manager.items,
            async_create_item=self.resource_create,
            async_update_item=self.resource_update,
        )
        self.data["lovelace"] = SimpleNamespace(mode="storage", resources=self.resource_manager)
        self.gate = None
        self.platform_setup_count = 0

    async def register_static(self, configs):
        self.static_paths += configs

    def register_action(self, domain, action, handler, schema=None):
        self.actions[action] = handler

    async def forward(self, entry, platforms):
        self.platform_setup_count += 1

    async def unload_platforms(self, entry, platforms):
        return True

    async def async_add_executor_job(self, func, *args):
        if self.gate is not None and func is database.init_db:
            await self.gate.wait()
        return await asyncio.to_thread(func, *args)

    def async_create_background_task(self, coroutine, name, eager_start=False):
        task = asyncio.create_task(coroutine, name=name)
        self.background.append(task)
        return task

    async def resource_info(self):
        return {}

    async def resource_create(self, item):
        self.resource_manager.items.append({"url": item["url"], "type": item["res_type"], "id": len(self.resource_manager.items) + 1})

    async def resource_update(self, item_id, item):
        self.resource_manager.items[item_id-1].update(item)


def make_runtime(monkeypatch, hass):
    fake_vol = FakeVoluptuous("voluptuous")
    monkeypatch.setitem(sys.modules, "probatio", fake_vol)
    monkeypatch.setitem(sys.modules, "voluptuous", fake_vol)
    def fake_module(name, **values):
        module = types.ModuleType(name)
        for key, value in values.items():
            setattr(module, key, value)
        monkeypatch.setitem(sys.modules, name, module)
        return module

    fake_module("homeassistant.components")
    fake_module("homeassistant.components.frontend", add_extra_js_url=lambda h, url: None)
    fake_module("homeassistant.components.http", StaticPathConfig=lambda *args: args)
    fake_module("homeassistant.exceptions", ServiceValidationError=type("ServiceValidationError", (Exception,), {}))
    fake_module("homeassistant.core", HomeAssistant=FakeHass, ServiceCall=SimpleNamespace,
                CoreState=SimpleNamespace(running="running"), callback=lambda f: f)
    fake_module("homeassistant.helpers")
    fake_module("homeassistant.helpers.config_validation", string=str)
    fake_module("homeassistant.helpers.dispatcher", async_dispatcher_send=lambda h, sig: h.dispatches.append(sig))
    def listen_state(h, ids, handler):
        for entity_id in ids:
            h.listeners[entity_id] = handler
        return lambda: None
    def call_later(h, delay, cb):
        timer = asyncio.get_running_loop().call_later(0.01, cb, datetime.now(timezone.utc))
        return timer.cancel
    fake_module("homeassistant.helpers.event", async_track_state_change_event=listen_state,
                async_call_later=call_later)
    async def integration(*_):
        return SimpleNamespace(version="2026.11.0")
    fake_module("homeassistant.loader", async_get_integration=integration)
    fake_module("homeassistant.util")
    fake_module("homeassistant.util.dt", utcnow=lambda: datetime.now(timezone.utc),
                now=lambda: datetime.now(timezone.utc), get_time_zone=lambda name: ZoneInfo(name))
    ha_const = sys.modules["homeassistant.const"]
    monkeypatch.setattr(ha_const, "EVENT_HOMEASSISTANT_STARTED", "homeassistant_started", raising=False)
    spec = importlib.util.spec_from_file_location(
        "smart_scale_bmi.runtime", Path(__file__).resolve().parents[1] / "custom_components/smart_scale_bmi/__init__.py"
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "smart_scale_bmi.runtime", module)
    spec.loader.exec_module(module)
    return module


def test_backend_validation_and_timezone(monkeypatch, tmp_path):
    h = FakeHass(tmp_path)
    runtime = make_runtime(monkeypatch, h)
    assert runtime._valid_weight("62.5") == 62.5
    for invalid in ["NaN", "inf", "-inf", "abc", 0, -1, 999]:
        with pytest.raises(ValueError):
            runtime._valid_weight(invalid)
    assert runtime._parse_profile_id("1.0") == 1
    assert runtime._parse_profile_id("1.5") is None
    assert runtime._parse_profile_id("NaN") is None
    assert runtime._weight_from_state(SimpleNamespace(entity_id="sensor.weight", state="154.324", attributes={"unit_of_measurement": "lb"})) == pytest.approx(70, abs=0.002)
    assert runtime._weight_from_state(SimpleNamespace(entity_id="sensor.weight", state="70000", attributes={"unit_of_measurement": "g"})) == 70
    assert runtime._weight_from_state(SimpleNamespace(entity_id="sensor.weight", state="nan", attributes={"unit_of_measurement": "kg"})) is None
    assert runtime._normalise_time(h, "2026-11-07T14:00:00+07:00").startswith("2026-11-07T07:00:00")
    assert runtime._normalise_time(h, "2026-11-07T14:00:00").startswith("2026-11-07T07:00:00")
    assert runtime._calculate_measurement_payload(profile(), 68, "2020-06-01T00:00:00+00:00")["age_months"] == (2020 - 1988) * 12 + 1
    # At 00:30 Bangkok, the UTC date is still the previous calendar day.
    local_age = runtime._calculate_measurement_payload(
        {**profile(), "birth_month": 11, "birth_year": 2015},
        68, "2026-11-06T17:30:00+00:00", ZoneInfo("Asia/Bangkok"),
    )
    assert local_age["age_months"] == 132


def test_background_startup_and_recording(monkeypatch, tmp_path):
    async def scenario():
        h = FakeHass(tmp_path)
        runtime = make_runtime(monkeypatch, h)
        assert await runtime.async_setup(h, {})
        await asyncio.gather(*h.background)
        assert len(h.static_paths) == 1
        assert len(h.resource_manager.items) == 1
        original_resource = dict(h.resource_manager.items[0])
        assert await runtime.async_setup(h, {})
        assert len(h.static_paths) == 2  # async_setup is called once by HA, test creates two deliberate calls
        await asyncio.gather(*h.background)
        assert len(h.resource_manager.items) == 1
        assert h.resource_manager.items[0] == original_resource
        assert len(h.actions) == 3

        e = FakeEntry()
        h.gate = asyncio.Event()
        assert await asyncio.wait_for(runtime.async_setup_entry(h, e), timeout=1)
        assert h.platform_setup_count == 1
        assert not h.data["smart_scale_bmi"][e.entry_id]["storage_ready"]
        h.gate.set()
        await asyncio.wait_for(h.data["smart_scale_bmi"][e.entry_id]["storage_task"], timeout=3)
        assert h.data["smart_scale_bmi"][e.entry_id]["storage_ready"]

        now = datetime.now(timezone.utc)
        old_weight = SimpleNamespace(entity_id="sensor.weight", state="69", last_changed=now,
                                    attributes={"unit_of_measurement": "kg"})
        new_weight = SimpleNamespace(entity_id="sensor.weight", state="70", last_changed=now,
                                    attributes={"unit_of_measurement": "kg"})
        new_profile = SimpleNamespace(entity_id="sensor.profile", state="1", last_changed=now,
                                     attributes={})
        h.states = {"sensor.weight": new_weight, "sensor.profile": new_profile}
        h.listeners["sensor.weight"](SimpleNamespace(data={"entity_id": "sensor.weight", "old_state": old_weight, "new_state": new_weight}))
        await asyncio.sleep(0.12)
        await asyncio.gather(*e.tasks)
        db_path = h.data["smart_scale_bmi"][e.entry_id]["db_path"]
        assert database.count_measurements(db_path, e.entry_id) == 1
        # Repeated event from the same pair must not add a second measurement.
        h.listeners["sensor.weight"](SimpleNamespace(data={"entity_id": "sensor.weight", "old_state": old_weight, "new_state": new_weight}))
        await asyncio.sleep(0.12)
        await asyncio.gather(*e.tasks)
        assert database.count_measurements(db_path, e.entry_id) == 1

        await h.actions["add_measurement"](SimpleNamespace(data={"entry_id": e.entry_id, "weight_kg": 65, "measured_at": "2020-06-01T00:00:00+00:00"}))
        assert database.count_measurements(db_path, e.entry_id) == 2
        first = database.get_latest_measurement(db_path, e.entry_id)
        assert first["weight_kg"] == 70  # last reading is by date, not insertion order
        older = database.get_recent_measurements(db_path, e.entry_id, 2)[1]
        assert older["weight_kg"] == 65
        stored = database.get_measurement(db_path, e.entry_id, older["id"])
        assert stored["age_months"] == (2020-1988)*12 + 1
        await h.actions["update_measurement"](SimpleNamespace(data={"entry_id": e.entry_id, "measurement_id": stored["id"], "weight_kg": 68}))
        updated = database.get_measurement(db_path, e.entry_id, stored["id"])
        assert updated["age_months"] == stored["age_months"]
        await h.actions["delete_measurement"](SimpleNamespace(data={"entry_id": e.entry_id, "measurement_id": stored["id"]}))
        assert database.count_measurements(db_path, e.entry_id) == 1
        assert await runtime.async_unload_entry(h, e)
        assert e.entry_id not in h.data["smart_scale_bmi"]
    asyncio.run(scenario())

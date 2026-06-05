"""Smart Scale BMI integration."""
from __future__ import annotations

import logging
import os
from functools import partial
from datetime import datetime
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant, ServiceCall, callback
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.lovelace.resources import ResourceStorageCollection
from homeassistant.loader import async_get_integration
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
import homeassistant.util.dt as dt_util

from .const import (
    CONF_HEIGHT_M,
    CONF_INITIAL_WEIGHT_KG,
    CONF_PERSON_NAME,
    CONF_PROFILE_ID,
    CONF_PROFILE_SENSOR,
    CONF_WEIGHT_SENSOR,
    DB_DIR,
    DB_FILE,
    DEFAULT_DEBOUNCE_SECONDS,
    DOMAIN,
    PLATFORMS,
    SIGNAL_UPDATE_SENSORS,
)
from .database import (
    count_measurements,
    get_config,
    get_measurement,
    init_db,
    insert_measurement,
    upsert_person,
    delete_measurement,
    update_measurement,
)
from .who import calculate_bmi, classify_bmi

_LOGGER = logging.getLogger(__name__)

UI_URL_BASE = "/smart_scale_bmi_ui"
UI_DIR_PATH = "frontend"
CARD_FILE = "smart-scale-bmi-card.js"

SERVICE_ADD_MEASUREMENT = "add_measurement"
SERVICE_DELETE_MEASUREMENT = "delete_measurement"
SERVICE_UPDATE_MEASUREMENT = "update_measurement"

SERVICE_ADD_MEASUREMENT_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): cv.string,
        vol.Required("weight_kg"): vol.Coerce(float),
        vol.Optional("measured_at"): cv.string,
    }
)

SERVICE_DELETE_MEASUREMENT_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): cv.string,
        vol.Required("measurement_id"): vol.Coerce(int),
    }
)

SERVICE_UPDATE_MEASUREMENT_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): cv.string,
        vol.Required("measurement_id"): vol.Coerce(int),
        vol.Required("weight_kg"): vol.Coerce(float),
    }
)


async def init_resource(hass: HomeAssistant, url: str, ver: str) -> None:
    """Register the Lovelace custom card resource."""
    url_with_version = f"{url}?hacstag={ver}"
    add_extra_js_url(hass, url_with_version)

    async def _register_resource(*args: Any) -> None:
        lovelace = hass.data.get("lovelace")
        if not lovelace:
            return

        resources = getattr(lovelace, "resources", None) or lovelace.get("resources")
        if not isinstance(resources, ResourceStorageCollection):
            return

        if not resources.loaded:
            await resources.async_load()

        for item in resources.async_items():
            item_url = item.get("url", "")
            base_url = item_url.split("?")[0]
            if base_url == url:
                if item_url != url_with_version:
                    await resources.async_update_item(
                        item["id"],
                        {"res_type": "module", "url": url_with_version},
                    )
                return

        await resources.async_create_item(
            {"res_type": "module", "url": url_with_version}
        )

    if hass.state == CoreState.running:
        await _register_resource()
    else:
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _register_resource)


def _parse_profile_id(value: Any) -> int | None:
    """Parse a profile id value that may arrive as '1' or '1.0'."""
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _format_state_time(state_obj: Any) -> str | None:
    """Return last_changed as local ISO text."""
    if state_obj is None or state_obj.last_changed is None:
        return None
    return dt_util.as_local(state_obj.last_changed).isoformat(timespec="seconds")


def _calculate_measurement_payload(config: dict[str, Any], weight_kg: float) -> dict[str, Any]:
    """Calculate BMI and warning payload for a configured person."""
    bmi = calculate_bmi(weight_kg, float(config[CONF_HEIGHT_M]))
    classification = classify_bmi(
        bmi,
        str(config["gender"]),
        int(config["birth_year"]),
        int(config["birth_month"]),
        dt_util.now().date(),
    )
    return {
        "bmi": bmi,
        "warning": classification["warning"],
        "standard": classification["standard"],
        "age_months": classification.get("age_months"),
        "age_text": classification.get("age_text"),
    }


async def _ensure_storage_ready(runtime: dict[str, Any]) -> bool:
    """Wait for the lightweight background storage preparation when needed."""
    storage_task = runtime.get("storage_task")
    if storage_task is not None and not storage_task.done():
        await storage_task
    return bool(runtime.get("storage_ready"))


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up integration-level services and frontend assets."""
    hass.data.setdefault(DOMAIN, {})

    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                UI_URL_BASE,
                hass.config.path("custom_components", DOMAIN, UI_DIR_PATH),
                False,
            )
        ]
    )

    async def handle_add_measurement(call: ServiceCall) -> None:
        entry_id = call.data["entry_id"]
        runtime = hass.data.get(DOMAIN, {}).get(entry_id)
        if not runtime:
            _LOGGER.warning("Cannot add measurement: entry_id %s is not loaded", entry_id)
            return

        if not await _ensure_storage_ready(runtime):
            _LOGGER.warning("Cannot add measurement: storage is not ready for entry_id %s", entry_id)
            return

        entry: ConfigEntry = runtime["entry"]
        db_path: str = runtime["db_path"]
        config_data = get_config(entry)
        weight_kg = float(call.data["weight_kg"])
        measured_at = call.data.get("measured_at") or dt_util.now().isoformat(timespec="seconds")
        payload = _calculate_measurement_payload(config_data, weight_kg)

        await hass.async_add_executor_job(
            partial(
                insert_measurement,
                db_path,
                entry_id=entry_id,
                config=config_data,
                measured_at=measured_at,
                weight_kg=weight_kg,
                **payload,
            )
        )
        async_dispatcher_send(hass, f"{SIGNAL_UPDATE_SENSORS}_{entry_id}")

    async def handle_delete_measurement(call: ServiceCall) -> None:
        entry_id = call.data["entry_id"]
        runtime = hass.data.get(DOMAIN, {}).get(entry_id)
        if not runtime:
            _LOGGER.warning("Cannot delete measurement: entry_id %s is not loaded", entry_id)
            return

        if not await _ensure_storage_ready(runtime):
            _LOGGER.warning("Cannot delete measurement: storage is not ready for entry_id %s", entry_id)
            return

        deleted = await hass.async_add_executor_job(
            delete_measurement,
            runtime["db_path"],
            entry_id,
            int(call.data["measurement_id"]),
        )
        if not deleted:
            _LOGGER.warning(
                "No measurement %s found for entry_id %s",
                call.data["measurement_id"],
                entry_id,
            )
        async_dispatcher_send(hass, f"{SIGNAL_UPDATE_SENSORS}_{entry_id}")

    async def handle_update_measurement(call: ServiceCall) -> None:
        entry_id = call.data["entry_id"]
        runtime = hass.data.get(DOMAIN, {}).get(entry_id)
        if not runtime:
            _LOGGER.warning("Cannot update measurement: entry_id %s is not loaded", entry_id)
            return

        if not await _ensure_storage_ready(runtime):
            _LOGGER.warning("Cannot update measurement: storage is not ready for entry_id %s", entry_id)
            return

        measurement_id = int(call.data["measurement_id"])
        current = await hass.async_add_executor_job(
            get_measurement,
            runtime["db_path"],
            entry_id,
            measurement_id,
        )
        if current is None:
            _LOGGER.warning(
                "No measurement %s found for entry_id %s",
                measurement_id,
                entry_id,
            )
            return

        entry: ConfigEntry = runtime["entry"]
        config_data = get_config(entry)
        weight_kg = float(call.data["weight_kg"])
        if weight_kg <= 0:
            _LOGGER.warning("Cannot update measurement %s: weight must be greater than 0", measurement_id)
            return

        measured_at = current["measured_at"]
        payload = _calculate_measurement_payload(config_data, weight_kg)

        updated = await hass.async_add_executor_job(
            partial(
                update_measurement,
                runtime["db_path"],
                entry_id=entry_id,
                measurement_id=measurement_id,
                config=config_data,
                measured_at=measured_at,
                weight_kg=weight_kg,
                **payload,
            )
        )
        if updated:
            async_dispatcher_send(hass, f"{SIGNAL_UPDATE_SENSORS}_{entry_id}")

    hass.services.async_register(
        DOMAIN,
        SERVICE_ADD_MEASUREMENT,
        handle_add_measurement,
        schema=SERVICE_ADD_MEASUREMENT_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DELETE_MEASUREMENT,
        handle_delete_measurement,
        schema=SERVICE_DELETE_MEASUREMENT_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_MEASUREMENT,
        handle_update_measurement,
        schema=SERVICE_UPDATE_MEASUREMENT_SCHEMA,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a Smart Scale BMI config entry without blocking Home Assistant startup."""
    integration = await async_get_integration(hass, DOMAIN)
    fallback_version = integration.version if integration and integration.version else "1.0"

    def get_file_version(file_name: str, fallback: str) -> str:
        try:
            file_path = hass.config.path(
                "custom_components", DOMAIN, UI_DIR_PATH, file_name
            )
            return str(int(os.path.getmtime(file_path)))
        except Exception:
            return fallback

    async def _async_register_frontend_resource() -> None:
        try:
            ver_card = await hass.async_add_executor_job(
                get_file_version, CARD_FILE, fallback_version
            )
            await init_resource(hass, f"{UI_URL_BASE}/{CARD_FILE}", ver_card)
        except Exception as err:  # pragma: no cover - defensive runtime logging
            _LOGGER.exception("Could not register Smart Scale BMI card resource: %s", err)

    if hass.state == CoreState.running:
        resource_task = hass.async_create_task(_async_register_frontend_resource())
        entry.async_on_unload(resource_task.cancel)
    else:
        @callback
        def _schedule_resource_registration(*_: Any) -> None:
            hass.async_create_task(_async_register_frontend_resource())

        entry.async_on_unload(
            hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STARTED,
                _schedule_resource_registration,
            )
        )

    config_data = get_config(entry)
    storage_dir = hass.config.path(DB_DIR)
    db_path = os.path.join(storage_dir, DB_FILE)

    runtime: dict[str, Any] = {
        "entry": entry,
        "db_path": db_path,
        "pending_unsub": None,
        "recording_enable_unsub": None,
        "last_record_key": None,
        "recording_enabled": hass.state == CoreState.running,
        "storage_ready": False,
        "storage_task": None,
    }
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime

    async def _insert_initial_measurement() -> None:
        initial_weight = float(config_data.get(CONF_INITIAL_WEIGHT_KG, 0) or 0)
        if initial_weight <= 0:
            return
        existing_count = await hass.async_add_executor_job(count_measurements, db_path, entry.entry_id)
        if existing_count > 0:
            return
        payload = _calculate_measurement_payload(config_data, initial_weight)
        await hass.async_add_executor_job(
            partial(
                insert_measurement,
                db_path,
                entry_id=entry.entry_id,
                config=config_data,
                measured_at=dt_util.now().isoformat(timespec="seconds"),
                weight_kg=initial_weight,
                **payload,
            )
        )

    async def _prepare_storage() -> None:
        try:
            await hass.async_add_executor_job(init_db, db_path)
            await hass.async_add_executor_job(upsert_person, db_path, entry.entry_id, config_data)
            await _insert_initial_measurement()
            runtime["storage_ready"] = True
            async_dispatcher_send(hass, f"{SIGNAL_UPDATE_SENSORS}_{entry.entry_id}")
        except Exception as err:  # pragma: no cover - defensive runtime logging
            runtime["storage_ready"] = False
            _LOGGER.exception("Could not prepare Smart Scale BMI storage: %s", err)

    storage_task = hass.async_create_task(_prepare_storage())
    runtime["storage_task"] = storage_task
    entry.async_on_unload(storage_task.cancel)

    if not runtime["recording_enabled"]:

        @callback
        def _enable_state_recording(*_: Any) -> None:
            runtime["recording_enabled"] = True
            runtime["recording_enable_unsub"] = None

        runtime["recording_enable_unsub"] = hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STARTED,
            _enable_state_recording,
        )
        entry.async_on_unload(runtime["recording_enable_unsub"])

    async def _record_weight_from_states() -> None:
        if not await _ensure_storage_ready(runtime):
            return

        current_config = get_config(entry)
        weight_state = hass.states.get(str(current_config[CONF_WEIGHT_SENSOR]))
        profile_state = hass.states.get(str(current_config[CONF_PROFILE_SENSOR]))

        if weight_state is None or profile_state is None:
            return

        profile_id = _parse_profile_id(profile_state.state)
        configured_profile_id = int(current_config[CONF_PROFILE_ID])
        if profile_id != configured_profile_id:
            return

        try:
            weight_kg = float(weight_state.state)
        except (TypeError, ValueError):
            return

        if weight_kg <= 0:
            return

        weight_last_changed = _format_state_time(weight_state)
        profile_last_changed = _format_state_time(profile_state)
        record_key = (
            configured_profile_id,
            round(weight_kg, 2),
            weight_last_changed,
            profile_last_changed,
        )
        if runtime.get("last_record_key") == record_key:
            return
        runtime["last_record_key"] = record_key

        measured_at = dt_util.now().isoformat(timespec="seconds")
        payload = _calculate_measurement_payload(current_config, weight_kg)

        await hass.async_add_executor_job(
            partial(
                insert_measurement,
                db_path,
                entry_id=entry.entry_id,
                config=current_config,
                measured_at=measured_at,
                weight_kg=weight_kg,
                source_weight_last_changed=weight_last_changed,
                source_profile_last_changed=profile_last_changed,
                **payload,
            )
        )
        async_dispatcher_send(hass, f"{SIGNAL_UPDATE_SENSORS}_{entry.entry_id}")

    @callback
    def _schedule_record(event: Any) -> None:
        if not runtime.get("recording_enabled"):
            return

        event_data = getattr(event, "data", {}) or {}
        old_state = event_data.get("old_state")
        new_state = event_data.get("new_state")
        entity_id = event_data.get("entity_id")
        if old_state is None or new_state is None:
            return

        old_value = str(old_state.state).strip()
        new_value = str(new_state.state).strip()
        if old_value == new_value:
            return
        if not new_value or new_value.lower() in {"unknown", "unavailable", "none"}:
            return
        if not old_value or old_value.lower() in {"unknown", "unavailable", "none"}:
            return

        current_config = get_config(entry)
        watched_entities = {
            str(current_config[CONF_WEIGHT_SENSOR]),
            str(current_config[CONF_PROFILE_SENSOR]),
        }
        if entity_id not in watched_entities:
            return

        pending_unsub = runtime.get("pending_unsub")
        if pending_unsub is not None:
            pending_unsub()

        @callback
        def _fire_record(_now: datetime) -> None:
            runtime["pending_unsub"] = None
            hass.async_create_task(_record_weight_from_states())

        runtime["pending_unsub"] = async_call_later(
            hass,
            DEFAULT_DEBOUNCE_SECONDS,
            _fire_record,
        )

    entry.async_on_unload(
        async_track_state_change_event(
            hass,
            [str(config_data[CONF_WEIGHT_SENSOR]), str(config_data[CONF_PROFILE_SENSOR])],
            _schedule_record,
        )
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(update_listener))
    return True


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry after options are changed."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if runtime and runtime.get("pending_unsub") is not None:
        runtime["pending_unsub"]()
    if runtime and runtime.get("recording_enable_unsub") is not None:
        runtime["recording_enable_unsub"]()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok

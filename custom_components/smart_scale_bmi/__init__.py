"""Smart Scale BMI integration for Home Assistant."""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any

import probatio as vol

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.loader import async_get_integration
import homeassistant.util.dt as dt_util

from .const import (
    CONF_HEIGHT_M,
    CONF_INITIAL_WEIGHT_KG,
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
    delete_measurement,
    get_config,
    get_measurement,
    init_db,
    insert_measurement,
    update_measurement,
    upsert_person,
)
from .who import calculate_bmi, classify_bmi

_LOGGER = logging.getLogger(__name__)
UI_URL_BASE = "/smart_scale_bmi_ui"
CARD_FILE = "smart-scale-bmi-card.js"
SOURCE_PAIR_WINDOW_SECONDS = 120


def _valid_weight(value: Any) -> float:
    """Reject invalid, nonfinite and physiologically implausible measurements."""
    try:
        weight = float(value)
    except (TypeError, ValueError) as err:
        raise vol.Invalid("Weight must be a number") from err
    if not math.isfinite(weight) or not 1 <= weight <= 300:
        raise vol.Invalid("Weight must be between 1 and 300 kg")
    return weight


SERVICE_ADD_MEASUREMENT_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): cv.string,
        vol.Required("weight_kg"): _valid_weight,
        vol.Optional("measured_at"): cv.string,
    }
)
SERVICE_DELETE_MEASUREMENT_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): cv.string,
        vol.Required("measurement_id"): vol.All(vol.Coerce(int), vol.Range(min=1)),
    }
)
SERVICE_UPDATE_MEASUREMENT_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): cv.string,
        vol.Required("measurement_id"): vol.All(vol.Coerce(int), vol.Range(min=1)),
        vol.Required("weight_kg"): _valid_weight,
    }
)


def _normalise_time(hass: HomeAssistant, value: str | None) -> str:
    """Store UTC ISO timestamps so dates sort consistently across time zones."""
    if not value:
        return dt_util.utcnow().isoformat(timespec="microseconds")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt_util.get_time_zone(hass.config.time_zone))
        return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")
    except (ValueError, TypeError) as err:
        raise ServiceValidationError(
            "measured_at must be an ISO 8601 datetime (for example 2026-11-07T12:00:00+07:00)"
        ) from err


def _parse_profile_id(value: Any) -> int | None:
    """Accept numeric ID states, but not nonintegral values or nonfinite floats."""
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or not number.is_integer():
        return None
    return int(number)


def _weight_from_state(state: Any) -> float | None:
    """Convert known device weight units to kilograms."""
    try:
        weight = float(state.state)
    except (ValueError, TypeError):
        return None
    unit = str(state.attributes.get("unit_of_measurement") or "kg").strip().lower()
    if unit in ("kg", "kilogram", "kilograms"):
        pass
    elif unit in ("g", "gram", "grams"):
        weight /= 1000
    elif unit in ("lb", "lbs", "pound", "pounds"):
        weight *= 0.45359237
    elif unit in ("oz", "ounce", "ounces"):
        weight *= 0.028349523125
    else:
        _LOGGER.warning("Weight sensor %s has unsupported unit %r", state.entity_id, unit)
        return None
    if not math.isfinite(weight) or not 1 <= weight <= 300:
        _LOGGER.debug("Ignoring out-of-range weight state for %s: %s %s", state.entity_id, state.state, unit)
        return None
    return round(weight, 3)


def _state_timestamp(state: Any) -> datetime | None:
    timestamp = getattr(state, "last_changed", None)
    if timestamp is None:
        return None
    return timestamp if timestamp.tzinfo is not None else timestamp.replace(tzinfo=timezone.utc)


def _calculate_measurement_payload(
    config: dict[str, Any], weight_kg: float, measured_at: str, local_timezone: Any = None
) -> dict[str, Any]:
    """Compute BMI and age as of the measurement date, not the current date."""
    bmi = calculate_bmi(weight_kg, float(config[CONF_HEIGHT_M]))
    measurement_datetime = datetime.fromisoformat(measured_at)
    if local_timezone is not None and measurement_datetime.tzinfo is not None:
        measurement_datetime = measurement_datetime.astimezone(local_timezone)
    classification = classify_bmi(
        bmi,
        str(config["gender"]),
        int(config["birth_year"]),
        int(config["birth_month"]),
        measurement_datetime.date(),
    )
    return {
        "bmi": bmi,
        "warning": classification["warning"],
        "standard": classification["standard"],
        "age_months": classification.get("age_months"),
        "age_text": classification.get("age_text"),
    }


async def init_resource(hass: HomeAssistant, url: str, version: str) -> None:
    """Register a single dashboard module, compatible with storage and YAML resources."""
    resource_url = f"{url}?v={version}"
    lovelace = hass.data.get("lovelace")
    resources = getattr(lovelace, "resources", None)
    if resources is None and isinstance(lovelace, dict):
        resources = lovelace.get("resources")
    if resources is None or not hasattr(resources, "async_items"):
        add_extra_js_url(hass, resource_url)
        _LOGGER.debug("Loaded card using frontend extra JS URL (Lovelace resources unavailable)")
        return

    if hasattr(resources, "async_get_info"):
        await resources.async_get_info()
    elif hasattr(resources, "async_load") and not getattr(resources, "loaded", False):
        await resources.async_load()

    for item in resources.async_items():
        if item.get("url", "").split("?", 1)[0] != url:
            continue
        if item["url"] != resource_url or item.get("type", item.get("res_type")) != "module":
            if hasattr(resources, "async_update_item"):
                await resources.async_update_item(
                    item["id"], {"res_type": "module", "url": resource_url}
                )
        _LOGGER.debug("Smart Scale BMI dashboard resource available: %s", resource_url)
        return

    if getattr(lovelace, "mode", "storage") == "yaml" or not hasattr(resources, "async_create_item"):
        # YAML users control resources. An extra URL makes the bundled card work
        # without attempting to write a read-only resource collection.
        add_extra_js_url(hass, resource_url)
        _LOGGER.info("Registered card via extra JS URL (YAML-managed Lovelace resources)")
        return

    await resources.async_create_item({"res_type": "module", "url": resource_url})
    _LOGGER.info("Created Lovelace resource %s", resource_url)


def _runtime_for_service(hass: HomeAssistant, entry_id: str) -> dict[str, Any]:
    runtime = hass.data.get(DOMAIN, {}).get(entry_id)
    if runtime is None or runtime.get("unloading"):
        raise ServiceValidationError(f"Smart Scale BMI entry {entry_id} is not loaded")
    return runtime


async def _ensure_storage_ready(runtime: dict[str, Any]) -> None:
    task = runtime.get("storage_task")
    if task is not None and not task.done():
        await task
    if not runtime.get("storage_ready"):
        raise ServiceValidationError(
            "Smart Scale BMI database is not ready; check integration logs or reload the entry"
        )


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Register HTTP resources and actions once (not once per person)."""
    hass.data.setdefault(DOMAIN, {})
    frontend_path = Path(__file__).parent / "frontend"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(UI_URL_BASE, str(frontend_path), False)]
    )

    async def _register_card() -> None:
        try:
            integration = await async_get_integration(hass, DOMAIN)
            await init_resource(
                hass,
                f"{UI_URL_BASE}/{CARD_FILE}",
                str(integration.version or "2026.11.0"),
            )
        except Exception:
            _LOGGER.exception("Cannot register Smart Scale BMI dashboard card resource")

    hass.async_create_background_task(
        _register_card(), f"{DOMAIN} register dashboard card", eager_start=False
    )

    async def handle_add_measurement(call: ServiceCall) -> None:
        entry_id = call.data["entry_id"]
        runtime = _runtime_for_service(hass, entry_id)
        await _ensure_storage_ready(runtime)
        weight = call.data["weight_kg"]
        data = get_config(runtime["entry"])
        measured_at = _normalise_time(hass, call.data.get("measured_at"))
        payload = _calculate_measurement_payload(
            data, weight, measured_at, dt_util.get_time_zone(hass.config.time_zone)
        )
        async with runtime["record_lock"]:
            try:
                record_id = await hass.async_add_executor_job(
                    partial(
                        insert_measurement,
                        runtime["db_path"],
                        entry_id=entry_id,
                        config=data,
                        measured_at=measured_at,
                        weight_kg=weight,
                        **payload,
                    )
                )
            except Exception as err:
                _LOGGER.exception("Manual measurement insert failed for entry %s", entry_id)
                raise ServiceValidationError("Unable to save measurement; check Home Assistant logs") from err
        _LOGGER.info("Added manual measurement id=%s entry=%s", record_id, entry_id)
        async_dispatcher_send(hass, f"{SIGNAL_UPDATE_SENSORS}_{entry_id}")

    async def handle_delete_measurement(call: ServiceCall) -> None:
        entry_id = call.data["entry_id"]
        runtime = _runtime_for_service(hass, entry_id)
        await _ensure_storage_ready(runtime)
        measurement_id = call.data["measurement_id"]
        async with runtime["record_lock"]:
            try:
                deleted = await hass.async_add_executor_job(
                    delete_measurement, runtime["db_path"], entry_id, measurement_id
                )
            except Exception as err:
                _LOGGER.exception("Delete failed entry=%s measurement=%s", entry_id, measurement_id)
                raise ServiceValidationError("Unable to delete measurement; check logs") from err
        if not deleted:
            raise ServiceValidationError(f"Measurement {measurement_id} not found in entry {entry_id}")
        _LOGGER.info("Deleted measurement id=%s entry=%s", measurement_id, entry_id)
        async_dispatcher_send(hass, f"{SIGNAL_UPDATE_SENSORS}_{entry_id}")

    async def handle_update_measurement(call: ServiceCall) -> None:
        entry_id = call.data["entry_id"]
        runtime = _runtime_for_service(hass, entry_id)
        await _ensure_storage_ready(runtime)
        measurement_id = call.data["measurement_id"]
        weight = call.data["weight_kg"]
        config_data = get_config(runtime["entry"])
        async with runtime["record_lock"]:
            try:
                current = await hass.async_add_executor_job(
                    get_measurement, runtime["db_path"], entry_id, measurement_id
                )
                if current is None:
                    raise ServiceValidationError(
                        f"Measurement {measurement_id} not found in entry {entry_id}"
                    )
                payload = _calculate_measurement_payload(
                    config_data, weight, current["measured_at"],
                    dt_util.get_time_zone(hass.config.time_zone),
                )
                updated = await hass.async_add_executor_job(
                    partial(
                        update_measurement,
                        runtime["db_path"],
                        entry_id=entry_id,
                        measurement_id=measurement_id,
                        config=config_data,
                        measured_at=current["measured_at"],
                        weight_kg=weight,
                        **payload,
                    )
                )
            except ServiceValidationError:
                raise
            except Exception as err:
                _LOGGER.exception("Update failed entry=%s measurement=%s", entry_id, measurement_id)
                raise ServiceValidationError("Unable to update measurement; check logs") from err
        if not updated:
            raise ServiceValidationError(f"Measurement {measurement_id} was not updated")
        _LOGGER.info("Updated measurement id=%s entry=%s", measurement_id, entry_id)
        async_dispatcher_send(hass, f"{SIGNAL_UPDATE_SENSORS}_{entry_id}")

    hass.services.async_register(DOMAIN, "add_measurement", handle_add_measurement, schema=SERVICE_ADD_MEASUREMENT_SCHEMA)
    hass.services.async_register(DOMAIN, "delete_measurement", handle_delete_measurement, schema=SERVICE_DELETE_MEASUREMENT_SCHEMA)
    hass.services.async_register(DOMAIN, "update_measurement", handle_update_measurement, schema=SERVICE_UPDATE_MEASUREMENT_SCHEMA)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a profile without waiting for SQLite initialization at startup."""
    db_path = str(Path(hass.config.path(DB_DIR)) / DB_FILE)
    runtime: dict[str, Any] = {
        "entry": entry,
        "db_path": db_path,
        "pending_unsub": None,
        "recording_enabled": hass.state == CoreState.running,
        "storage_ready": False,
        "storage_task": None,
        "record_lock": asyncio.Lock(),
        "last_record_key": None,
        "unloading": False,
        "last_error": None,
    }
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime

    async def _prepare_storage() -> None:
        """Run all synchronous SQLite work on executor threads, never HA's event loop."""
        for attempt in range(1, 4):
            if runtime["unloading"]:
                return
            try:
                config_data = get_config(entry)
                await hass.async_add_executor_job(init_db, db_path)
                await hass.async_add_executor_job(upsert_person, db_path, entry.entry_id, config_data)
                initial = float(config_data.get(CONF_INITIAL_WEIGHT_KG, 0) or 0)
                if 1 <= initial <= 300:
                    async with runtime["record_lock"]:
                        count = await hass.async_add_executor_job(
                            count_measurements, db_path, entry.entry_id
                        )
                        if count == 0:
                            initial_time = _normalise_time(hass, None)
                            payload = _calculate_measurement_payload(
                                config_data, initial, initial_time,
                                dt_util.get_time_zone(hass.config.time_zone),
                            )
                            await hass.async_add_executor_job(
                                partial(
                                    insert_measurement,
                                    db_path,
                                    entry_id=entry.entry_id,
                                    config=config_data,
                                    measured_at=initial_time,
                                    weight_kg=initial,
                                    **payload,
                                )
                            )
                            _LOGGER.info("Initial weight saved entry=%s (user-configured value)", entry.entry_id)
                runtime["storage_ready"] = True
                runtime["last_error"] = None
                _LOGGER.debug("SQLite ready for entry %s", entry.entry_id)
                async_dispatcher_send(hass, f"{SIGNAL_UPDATE_SENSORS}_{entry.entry_id}")
                return
            except asyncio.CancelledError:
                raise
            except Exception as err:
                runtime["last_error"] = type(err).__name__
                _LOGGER.exception(
                    "SQLite initialization failed entry=%s attempt=%s/3", entry.entry_id, attempt
                )
                if attempt < 3:
                    await asyncio.sleep(5)
        _LOGGER.error("SQLite unavailable for entry %s; reload integration after fixing the error", entry.entry_id)

    runtime["storage_task"] = entry.async_create_background_task(
        hass, _prepare_storage(), f"{DOMAIN} database {entry.entry_id}", eager_start=False
    )

    if not runtime["recording_enabled"]:
        @callback
        def _enable_recording(_event: Any) -> None:
            runtime["recording_enabled"] = True
            _LOGGER.debug("Scale monitoring enabled after Home Assistant started: %s", entry.entry_id)

        entry.async_on_unload(
            hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _enable_recording)
        )

    async def _record_weight_from_states() -> None:
        if runtime["unloading"] or not runtime["recording_enabled"]:
            return
        try:
            await _ensure_storage_ready(runtime)
        except ServiceValidationError:
            return
        async with runtime["record_lock"]:
            if runtime["unloading"]:
                return
            config_data = get_config(entry)
            weight_state = hass.states.get(str(config_data[CONF_WEIGHT_SENSOR]))
            profile_state = hass.states.get(str(config_data[CONF_PROFILE_SENSOR]))
            if weight_state is None or profile_state is None:
                _LOGGER.debug("Skipped measurement entry=%s: weight/profile state missing", entry.entry_id)
                return
            profile_id = _parse_profile_id(profile_state.state)
            if profile_id != int(config_data[CONF_PROFILE_ID]):
                _LOGGER.debug("Skipped measurement entry=%s: profile mismatch (%s)", entry.entry_id, profile_id)
                return
            weight = _weight_from_state(weight_state)
            if weight is None:
                return
            now = dt_util.utcnow()
            weight_time = _state_timestamp(weight_state)
            profile_time = _state_timestamp(profile_state)
            if weight_time is None or profile_time is None or (
                abs((weight_time - profile_time).total_seconds()) > SOURCE_PAIR_WINDOW_SECONDS
                or (now - weight_time).total_seconds() > SOURCE_PAIR_WINDOW_SECONDS
                or (now - profile_time).total_seconds() > SOURCE_PAIR_WINDOW_SECONDS
            ):
                _LOGGER.debug(
                    "Skipped stale scale pair entry=%s weight_at=%s profile_at=%s window=%ss",
                    entry.entry_id, weight_time, profile_time, SOURCE_PAIR_WINDOW_SECONDS,
                )
                return
            weight_stamp = weight_time.isoformat(timespec="microseconds")
            profile_stamp = profile_time.isoformat(timespec="microseconds")
            record_key = (profile_id, weight_stamp, profile_stamp)
            if record_key == runtime["last_record_key"]:
                return
            try:
                measurement_time = _normalise_time(hass, None)
                payload = _calculate_measurement_payload(
                    config_data, weight, measurement_time,
                    dt_util.get_time_zone(hass.config.time_zone),
                )
                record_id = await hass.async_add_executor_job(
                    partial(
                        insert_measurement,
                        db_path,
                        entry_id=entry.entry_id,
                        config=config_data,
                        measured_at=measurement_time,
                        weight_kg=weight,
                        source_weight_last_changed=weight_stamp,
                        source_profile_last_changed=profile_stamp,
                        **payload,
                    )
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.exception("Automatic measurement failed entry=%s", entry.entry_id)
                return
            runtime["last_record_key"] = record_key
        if record_id is not None:
            _LOGGER.info(
                "Scale measurement stored id=%s entry=%s profile=%s",
                record_id, entry.entry_id, profile_id,
            )
            _LOGGER.debug("Scale measurement weight_kg=%.3f entry=%s", weight, entry.entry_id)
            async_dispatcher_send(hass, f"{SIGNAL_UPDATE_SENSORS}_{entry.entry_id}")
        else:
            _LOGGER.debug("Duplicate scale event ignored entry=%s", entry.entry_id)

    @callback
    def _schedule_record(event: Any) -> None:
        if not runtime["recording_enabled"] or runtime["unloading"]:
            return
        data = getattr(event, "data", {}) or {}
        old_state, new_state = data.get("old_state"), data.get("new_state")
        if new_state is None:
            return
        entity_id = data.get("entity_id")
        current = get_config(entry)
        if entity_id not in (current[CONF_WEIGHT_SENSOR], current[CONF_PROFILE_SENSOR]):
            return
        old_value = getattr(old_state, "state", None)
        new_value = getattr(new_state, "state", None)
        if old_value == new_value or str(new_value).lower() in {"unknown", "unavailable", "none", ""}:
            return
        # Do not infer a measurement on initial startup states; require a real
        # transition following an existing state, and both sensors to be fresh.
        if old_value is None or str(old_value).lower() in {"unknown", "unavailable", "none", ""}:
            return
        if runtime["pending_unsub"] is not None:
            runtime["pending_unsub"]()

        @callback
        def _fire_record(_now: datetime) -> None:
            runtime["pending_unsub"] = None
            if not runtime["unloading"]:
                entry.async_create_background_task(
                    hass, _record_weight_from_states(),
                    f"{DOMAIN} record {entry.entry_id}", eager_start=False
                )

        runtime["pending_unsub"] = async_call_later(hass, DEFAULT_DEBOUNCE_SECONDS, _fire_record)

    config = get_config(entry)
    entry.async_on_unload(
        async_track_state_change_event(
            hass, [str(config[CONF_WEIGHT_SENSOR]), str(config[CONF_PROFILE_SENSOR])], _schedule_record
        )
    )
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        runtime["unloading"] = True
        hass.data[DOMAIN].pop(entry.entry_id, None)
        raise
    entry.async_on_unload(entry.add_update_listener(update_listener))
    _LOGGER.debug("Smart Scale BMI entry %s ready without waiting for database I/O", entry.entry_id)
    return True


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry after its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Stop active recording and unload sensor entities."""
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if runtime:
        runtime["unloading"] = True
        if runtime["pending_unsub"] is not None:
            runtime["pending_unsub"]()
            runtime["pending_unsub"] = None
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        _LOGGER.debug("Unloaded Smart Scale BMI entry %s", entry.entry_id)
    elif runtime:
        runtime["unloading"] = False
    return unload_ok

"""Sensor platform for Smart Scale BMI."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_BIRTH_MONTH,
    CONF_BIRTH_YEAR,
    CONF_GENDER,
    CONF_HEIGHT_M,
    CONF_PERSON_NAME,
    CONF_PROFILE_ID,
    CONF_PROFILE_SENSOR,
    CONF_WEIGHT_SENSOR,
    DOMAIN,
    GENDER_LABELS,
    SIGNAL_UPDATE_SENSORS,
)
from .database import get_config, get_latest_measurement, get_recent_measurements

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    async_add_entities([SmartScaleBMISensor(hass, entry)], False)


class SmartScaleBMISensor(SensorEntity):
    """Latest BMI sensor for one configured person."""

    _attr_has_entity_name = False
    _attr_icon = "mdi:scale-bathroom"
    _attr_native_unit_of_measurement = "kg/m²"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialise the sensor."""
        self.hass = hass
        self.entry = entry
        self._entry_id = entry.entry_id
        self._db_path = hass.data[DOMAIN][entry.entry_id]["db_path"]
        config = get_config(entry)
        self._attr_name = f"{config[CONF_PERSON_NAME]} BMI"
        self._attr_unique_id = f"{entry.entry_id}_bmi"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": f"Smart Scale BMI - {config[CONF_PERSON_NAME]}",
            "manufacturer": "Custom Integration",
            "model": "Smart Scale BMI Profile",
        }
        self._attr_native_value: float | None = None
        self._attr_extra_state_attributes: dict[str, Any] = {}

    async def async_added_to_hass(self) -> None:
        """Register update signal."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_UPDATE_SENSORS}_{self._entry_id}",
                self._handle_update_signal,
            )
        )
        runtime = self.hass.data.get(DOMAIN, {}).get(self._entry_id, {})
        if runtime.get("storage_ready"):
            self.async_schedule_update_ha_state(True)

    @callback
    def _handle_update_signal(self, *_: Any) -> None:
        """Handle dispatcher update."""
        self.async_schedule_update_ha_state(True)

    async def async_update(self) -> None:
        """Update the sensor state from SQLite."""
        runtime = self.hass.data.get(DOMAIN, {}).get(self._entry_id, {})
        if not runtime.get("storage_ready", True):
            config = get_config(self.entry)
            gender_label = GENDER_LABELS.get(str(config[CONF_GENDER]), str(config[CONF_GENDER]))
            self._attr_native_value = None
            self._attr_extra_state_attributes = {
                "entry_id": self._entry_id,
                "profile_id": int(config[CONF_PROFILE_ID]),
                "name": config[CONF_PERSON_NAME],
                "gender": gender_label,
                "birth_month": int(config[CONF_BIRTH_MONTH]),
                "birth_year": int(config[CONF_BIRTH_YEAR]),
                "height_m": float(config[CONF_HEIGHT_M]),
                "weight_sensor": config[CONF_WEIGHT_SENSOR],
                "profile_sensor": config[CONF_PROFILE_SENSOR],
                "warning": "đang tải dữ liệu",
                "recent_measurements": [],
            }
            return

        try:
            latest = await self.hass.async_add_executor_job(
                get_latest_measurement,
                self._db_path,
                self._entry_id,
            )
            recent = await self.hass.async_add_executor_job(
                get_recent_measurements,
                self._db_path,
                self._entry_id,
                100,
            )
        except Exception as err:  # pragma: no cover - defensive for runtime HA logging
            _LOGGER.exception("Could not read Smart Scale BMI database: %s", err)
            return

        config = get_config(self.entry)
        gender_label = GENDER_LABELS.get(str(config[CONF_GENDER]), str(config[CONF_GENDER]))
        self._attr_name = f"{config[CONF_PERSON_NAME]} BMI"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, self.entry.entry_id)},
            "name": f"Smart Scale BMI - {config[CONF_PERSON_NAME]}",
            "manufacturer": "Custom Integration",
            "model": "Smart Scale BMI Profile",
        }

        if latest is None:
            self._attr_native_value = None
            self._attr_extra_state_attributes = {
                "entry_id": self._entry_id,
                "profile_id": int(config[CONF_PROFILE_ID]),
                "name": config[CONF_PERSON_NAME],
                "gender": gender_label,
                "birth_month": int(config[CONF_BIRTH_MONTH]),
                "birth_year": int(config[CONF_BIRTH_YEAR]),
                "height_m": float(config[CONF_HEIGHT_M]),
                "weight_sensor": config[CONF_WEIGHT_SENSOR],
                "profile_sensor": config[CONF_PROFILE_SENSOR],
                "warning": "chưa có dữ liệu",
                "recent_measurements": [],
            }
            return

        self._attr_native_value = float(latest["bmi"])
        self._attr_extra_state_attributes = {
            "entry_id": self._entry_id,
            "measurement_id": latest["id"],
            "profile_id": latest["profile_id"],
            "name": latest["name"],
            "gender": gender_label,
            "birth_month": latest["birth_month"],
            "birth_year": latest["birth_year"],
            "age": latest["age_text"],
            "age_months": latest["age_months"],
            "height_m": latest["height_m"],
            "weight_kg": latest["weight_kg"],
            "bmi": latest["bmi"],
            "warning": latest["warning"],
            "standard": latest["standard"],
            "measured_at": latest["measured_at"],
            "weight_sensor": latest["source_weight_sensor"],
            "profile_sensor": latest["source_profile_sensor"],
            "source_weight_last_changed": latest["source_weight_last_changed"],
            "source_profile_last_changed": latest["source_profile_last_changed"],
            "recent_measurements": recent,
        }

"""Privacy-conscious diagnostics for Smart Scale BMI."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .database import count_measurements


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict:
    """Provide operational diagnostics without names, weights or birth dates."""
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    result: dict = {
        "storage_ready": bool(runtime and runtime.get("storage_ready")),
        "unloading": bool(runtime and runtime.get("unloading")),
        "last_error_type": runtime.get("last_error") if runtime else "not_loaded",
        "measurement_count": None,
        "weight_source_configured": bool(entry.options.get("weight_sensor", entry.data.get("weight_sensor"))),
        "profile_source_configured": bool(entry.options.get("profile_sensor", entry.data.get("profile_sensor"))),
    }
    if runtime and runtime.get("storage_ready"):
        try:
            result["measurement_count"] = await hass.async_add_executor_job(
                count_measurements, runtime["db_path"], entry.entry_id
            )
        except Exception as err:
            result["measurement_count_error"] = type(err).__name__
    return result

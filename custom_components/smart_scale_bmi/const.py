"""Constants for the Smart Scale BMI integration."""
from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "smart_scale_bmi"
PLATFORMS: list[Platform] = [Platform.SENSOR]

CONF_WEIGHT_SENSOR = "weight_sensor"
CONF_PROFILE_SENSOR = "profile_sensor"
CONF_PROFILE_ID = "profile_id"
CONF_PERSON_NAME = "person_name"
CONF_BIRTH_MONTH = "birth_month"
CONF_BIRTH_YEAR = "birth_year"
CONF_GENDER = "gender"
CONF_HEIGHT_M = "height_m"
CONF_INITIAL_WEIGHT_KG = "initial_weight_kg"

GENDER_MALE = "male"
GENDER_FEMALE = "female"
GENDER_LABELS = {
    GENDER_MALE: "Nam",
    GENDER_FEMALE: "Nữ",
}

DB_DIR = "smart_scale_bmi"
DB_FILE = "smart_scale_bmi.db"

DEFAULT_DEBOUNCE_SECONDS = 2
SIGNAL_UPDATE_SENSORS = "smart_scale_bmi_update_signal"

ATTR_LAST_MEASUREMENT = "last_measurement"
ATTR_RECENT_MEASUREMENTS = "recent_measurements"

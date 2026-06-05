"""Config flow for Smart Scale BMI."""
from __future__ import annotations

from datetime import date
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_BIRTH_MONTH,
    CONF_BIRTH_YEAR,
    CONF_GENDER,
    CONF_HEIGHT_M,
    CONF_INITIAL_WEIGHT_KG,
    CONF_PERSON_NAME,
    CONF_PROFILE_ID,
    CONF_PROFILE_SENSOR,
    CONF_WEIGHT_SENSOR,
    DOMAIN,
    GENDER_FEMALE,
    GENDER_MALE,
)

MONTH_OPTIONS = [
    selector.SelectOptionDict(value=str(month), label=f"Tháng {month}")
    for month in range(1, 13)
]

GENDER_OPTIONS = [
    selector.SelectOptionDict(value=GENDER_MALE, label="Nam"),
    selector.SelectOptionDict(value=GENDER_FEMALE, label="Nữ"),
]


def _normalise_user_input(user_input: dict[str, Any]) -> dict[str, Any]:
    """Convert selector values to the correct persisted types."""
    data = dict(user_input)
    data[CONF_PROFILE_ID] = int(float(data[CONF_PROFILE_ID]))
    data[CONF_BIRTH_MONTH] = int(data[CONF_BIRTH_MONTH])
    data[CONF_BIRTH_YEAR] = int(float(data[CONF_BIRTH_YEAR]))
    data[CONF_HEIGHT_M] = round(float(data[CONF_HEIGHT_M]), 3)
    data[CONF_INITIAL_WEIGHT_KG] = round(float(data[CONF_INITIAL_WEIGHT_KG]), 2)
    data[CONF_PERSON_NAME] = str(data[CONF_PERSON_NAME]).strip()
    return data


def _schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Return the config/options schema."""
    defaults = defaults or {}
    current_year = date.today().year

    return vol.Schema(
        {
            vol.Required(
                CONF_WEIGHT_SENSOR,
                default=defaults.get(CONF_WEIGHT_SENSOR),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(
                CONF_PROFILE_SENSOR,
                default=defaults.get(CONF_PROFILE_SENSOR),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(
                CONF_PROFILE_ID,
                default=defaults.get(CONF_PROFILE_ID, 1),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1,
                    max=999,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_PERSON_NAME,
                default=defaults.get(CONF_PERSON_NAME, ""),
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
            ),
            vol.Required(
                CONF_BIRTH_MONTH,
                default=str(defaults.get(CONF_BIRTH_MONTH, 1)),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=MONTH_OPTIONS,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                CONF_BIRTH_YEAR,
                default=defaults.get(CONF_BIRTH_YEAR, current_year - 30),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1900,
                    max=current_year,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_GENDER,
                default=defaults.get(CONF_GENDER, GENDER_MALE),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=GENDER_OPTIONS,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                CONF_HEIGHT_M,
                default=defaults.get(CONF_HEIGHT_M, 1.62),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.3,
                    max=2.5,
                    step=0.01,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="m",
                )
            ),
            vol.Required(
                CONF_INITIAL_WEIGHT_KG,
                default=defaults.get(CONF_INITIAL_WEIGHT_KG, 65.52),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1,
                    max=300,
                    step=0.01,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="kg",
                )
            ),
        }
    )


class SmartScaleBMIConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Smart Scale BMI."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        """Return the options flow."""
        return SmartScaleBMIOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            data = _normalise_user_input(user_input)
            if not data[CONF_PERSON_NAME]:
                errors[CONF_PERSON_NAME] = "name_required"
            else:
                unique_id = (
                    f"{data[CONF_WEIGHT_SENSOR]}|{data[CONF_PROFILE_SENSOR]}|"
                    f"{data[CONF_PROFILE_ID]}"
                )
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"{data[CONF_PERSON_NAME]} - ID {data[CONF_PROFILE_ID]}",
                    data=data,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_schema(user_input or {}),
            errors=errors,
        )


class SmartScaleBMIOptionsFlow(config_entries.OptionsFlow):
    """Handle editable options for a Smart Scale BMI entry."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Manage the options."""
        errors: dict[str, str] = {}
        current = {**self.config_entry.data, **self.config_entry.options}

        if user_input is not None:
            data = _normalise_user_input(user_input)
            if not data[CONF_PERSON_NAME]:
                errors[CONF_PERSON_NAME] = "name_required"
            else:
                return self.async_create_entry(title="", data=data)

        return self.async_show_form(
            step_id="init",
            data_schema=_schema(user_input or current),
            errors=errors,
        )

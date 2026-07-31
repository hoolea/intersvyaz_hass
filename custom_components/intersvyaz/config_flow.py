"""Config flow for Intersvyaz Domofon integration."""
from __future__ import annotations

import logging
import uuid
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import IntersvyazApiClient, IntersvyazApiError, IntersvyazAuthError
from .const import (
    AUTH_METHOD_LOGIN,
    AUTH_METHOD_PHONE,
    CONF_ADDRESS,
    CONF_AUTH_ID,
    CONF_AUTH_METHOD,
    CONF_DEVICE_ID,
    CONF_PASSWORD,
    CONF_PHONE,
    CONF_SCAN_INTERVAL,
    CONF_SMS_CODE,
    CONF_TOKEN,
    CONF_USERNAME,
    CONF_USER_ID,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


class DomofonConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Intersvyaz Domofon."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize flow."""
        self.auth_method: str | None = None
        self.phone_data: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> "DomofonOptionsFlow":
        return DomofonOptionsFlow(config_entry)

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Выбор метода авторизации."""
        if user_input is not None:
            self.auth_method = user_input[CONF_AUTH_METHOD]
            if self.auth_method == AUTH_METHOD_LOGIN:
                return await self.async_step_login()
            return await self.async_step_phone_number()

        auth_methods = {
            AUTH_METHOD_LOGIN: "Логин",
            AUTH_METHOD_PHONE: "Номер телефона",
        }

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_AUTH_METHOD): vol.In(auth_methods)}),
        )

    async def async_step_login(self, user_input: dict[str, Any] | None = None):
        """Авторизация по логину и паролю."""
        errors: dict[str, str] = {}
        if user_input is not None:
            client = IntersvyazApiClient(async_get_clientsession(self.hass))
            try:
                token = await client.login(
                    user_input[CONF_USERNAME], user_input[CONF_PASSWORD]
                )
            except IntersvyazAuthError:
                errors["base"] = "auth_error"
            except IntersvyazApiError as err:
                _LOGGER.error("Ошибка сети при авторизации: %s", err)
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(f"login_{user_input[CONF_USERNAME]}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_USERNAME],
                    data={
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_TOKEN: token,
                    },
                )

        return self.async_show_form(
            step_id="login",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    async def async_step_phone_number(self, user_input: dict[str, Any] | None = None):
        """Ввод номера телефона."""
        errors: dict[str, str] = {}
        if user_input is not None:
            client = IntersvyazApiClient(async_get_clientsession(self.hass))
            self.phone_data = {CONF_PHONE: user_input[CONF_PHONE]}

            try:
                device_id = await client.send_sms(user_input[CONF_PHONE])
            except IntersvyazApiError as err:
                # При ошибке отправки СМС (например, превышен лимит) всё
                # равно даём пользователю ввести код из предыдущего СМС.
                _LOGGER.debug("Ошибка отправки СМС: %s", err)
                self.phone_data[CONF_DEVICE_ID] = uuid.uuid4().hex
                description = f"\n\n{err}" if "limit" in str(err).lower() else (
                    "\n\nВы можете ввести код из предыдущего СМС"
                )
                return await self.async_step_sms_code(error_message=description)

            self.phone_data[CONF_DEVICE_ID] = device_id
            return await self.async_step_sms_code()

        return self.async_show_form(
            step_id="phone_number",
            data_schema=vol.Schema({vol.Required(CONF_PHONE): str}),
            description_placeholders={"format": "+79991234567"},
            errors=errors,
        )

    async def async_step_sms_code(
        self,
        user_input: dict[str, Any] | None = None,
        error_message: str | None = None,
    ):
        """Ввод SMS кода."""
        errors: dict[str, str] = {}
        description_placeholders = {"error_message": error_message or ""}

        if user_input is not None:
            client = IntersvyazApiClient(async_get_clientsession(self.hass))
            try:
                result = await client.confirm_sms(
                    self.phone_data[CONF_PHONE],
                    user_input[CONF_SMS_CODE],
                    self.phone_data[CONF_DEVICE_ID],
                )
            except IntersvyazApiError:
                errors["base"] = "invalid_code"
            else:
                self.phone_data[CONF_AUTH_ID] = result["authId"]
                self.phone_data["addresses"] = result["addresses"]
                return await self.async_step_address_select()

        return self.async_show_form(
            step_id="sms_code",
            data_schema=vol.Schema({vol.Required(CONF_SMS_CODE): str}),
            description_placeholders=description_placeholders,
            errors=errors,
        )

    async def async_step_address_select(self, user_input: dict[str, Any] | None = None):
        """Выбор адреса."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                selected_address = next(
                    addr
                    for addr in self.phone_data["addresses"]
                    if addr["ADDRESS"] == user_input[CONF_ADDRESS]
                )
                client = IntersvyazApiClient(async_get_clientsession(self.hass))
                token = await client.token_by_address(
                    self.phone_data[CONF_AUTH_ID], selected_address["USER_ID"]
                )
            except IntersvyazApiError as err:
                _LOGGER.error("Ошибка получения токена: %s", err)
                errors["base"] = "token_error"
            else:
                await self.async_set_unique_id(f"phone_{selected_address['USER_ID']}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_ADDRESS],
                    data={
                        CONF_PHONE: self.phone_data[CONF_PHONE],
                        CONF_TOKEN: token,
                        CONF_ADDRESS: user_input[CONF_ADDRESS],
                        CONF_USER_ID: selected_address["USER_ID"],
                    },
                )

        return self.async_show_form(
            step_id="address_select",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(
                        [addr["ADDRESS"] for addr in self.phone_data.get("addresses", [])]
                    )
                }
            ),
            errors=errors,
        )


class DomofonOptionsFlow(config_entries.OptionsFlow):
    """Настройки интеграции: интервал обновления данных о камерах."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCAN_INTERVAL, default=current): vol.All(
                        vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL)
                    )
                }
            ),
        )

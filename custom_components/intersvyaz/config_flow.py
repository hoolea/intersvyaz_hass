"""Config flow for Intersvyaz Domofon integration."""
from __future__ import annotations

import logging
import uuid
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
import homeassistant.helpers.config_validation as cv
import homeassistant.helpers.device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import IntersvyazApiClient, IntersvyazApiError, IntersvyazAuthError
from .const import (
    AUTH_METHOD_LOGIN,
    AUTH_METHOD_PHONE,
    CONF_ADDRESS,
    CONF_ADDRESSES,
    CONF_AUTH_ID,
    CONF_AUTH_METHOD,
    CONF_DEVICE_ID,
    CONF_PASSWORD,
    CONF_PHONE,
    CONF_PRIMARY_USER_ID,
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


def _addresses_schema(addresses: list[dict]) -> vol.Schema:
    """Схема с чекбоксами (multi-select) для адресов, полученных от API.

    Формат ответа API использует ключ "ADDRESS" (см. api.py: confirm_sms).
    """
    options = {addr["ADDRESS"]: addr["ADDRESS"] for addr in addresses}
    return vol.Schema({vol.Required(CONF_ADDRESS): cv.multi_select(options)})


def _stored_addresses_schema(addresses: list[dict]) -> vol.Schema:
    """Схема с чекбоксами (multi-select) для уже сохранённых адресов записи.

    В отличие от _addresses_schema, здесь адреса хранятся в формате
    entry.data[CONF_ADDRESSES] с ключом CONF_ADDRESS ("address").
    """
    options = {addr[CONF_ADDRESS]: addr[CONF_ADDRESS] for addr in addresses}
    return vol.Schema({vol.Required(CONF_ADDRESS): cv.multi_select(options)})


class DomofonConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Intersvyaz Domofon."""

    VERSION = 2

    def __init__(self) -> None:
        """Initialize flow."""
        self.auth_method: str | None = None
        self.phone_data: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> "DomofonOptionsFlow":
        return DomofonOptionsFlow()

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
                        CONF_AUTH_METHOD: AUTH_METHOD_LOGIN,
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
        """Выбор одного или нескольких адресов (чекбоксы)."""
        errors: dict[str, str] = {}
        available = self.phone_data.get("addresses", [])
        address_map = {addr["ADDRESS"]: addr for addr in available}

        if user_input is not None:
            selected = user_input[CONF_ADDRESS]
            if not selected:
                errors["base"] = "no_address_selected"
            else:
                client = IntersvyazApiClient(async_get_clientsession(self.hass))
                addresses_data: list[dict] = []
                try:
                    for address_name in selected:
                        addr = address_map[address_name]
                        token = await client.token_by_address(
                            self.phone_data[CONF_AUTH_ID], addr["USER_ID"]
                        )
                        addresses_data.append(
                            {
                                CONF_ADDRESS: address_name,
                                CONF_USER_ID: addr["USER_ID"],
                                CONF_TOKEN: token,
                            }
                        )
                except IntersvyazApiError as err:
                    _LOGGER.error("Ошибка получения токена: %s", err)
                    errors["base"] = "token_error"
                else:
                    await self.async_set_unique_id(f"phone_{self.phone_data[CONF_PHONE]}")
                    self._abort_if_unique_id_configured()

                    title = addresses_data[0][CONF_ADDRESS]
                    if len(addresses_data) > 1:
                        title = f"{title} +{len(addresses_data) - 1}"

                    return self.async_create_entry(
                        title=title,
                        data={
                            CONF_AUTH_METHOD: AUTH_METHOD_PHONE,
                            CONF_PHONE: self.phone_data[CONF_PHONE],
                            CONF_ADDRESSES: addresses_data,
                            CONF_PRIMARY_USER_ID: addresses_data[0][CONF_USER_ID],
                        },
                    )

        return self.async_show_form(
            step_id="address_select",
            data_schema=_addresses_schema(available),
            errors=errors,
        )


class DomofonOptionsFlow(config_entries.OptionsFlow):
    """Настройки интеграции: интервал обновления, добавление/удаление адресов."""

    def __init__(self) -> None:
        # self.config_entry предоставляется базовым классом OptionsFlow
        # автоматически (начиная с HA 2024.x); ручное присваивание в
        # __init__ устарело и будет удалено в HA 2025.12.
        self.phone_data: dict[str, Any] = {}

    @property
    def _supports_addresses(self) -> bool:
        """Управление адресами доступно только для авторизации по телефону."""
        return bool(self.config_entry.data.get(CONF_ADDRESSES))

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if not self._supports_addresses:
            return await self.async_step_settings()

        return self.async_show_menu(
            step_id="init",
            menu_options=["settings", "add_address", "remove_address"],
        )

    async def async_step_settings(self, user_input: dict[str, Any] | None = None):
        """Интервал обновления данных о камерах."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCAN_INTERVAL, default=current): vol.All(
                        vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL)
                    )
                }
            ),
        )

    # ------------------------------------------------------------------ #
    # Добавление адреса
    # ------------------------------------------------------------------ #
    async def async_step_add_address(self, user_input: dict[str, Any] | None = None):
        """Отправляет СМС на телефон, привязанный к записи, и просит код."""
        phone = self.config_entry.data.get(CONF_PHONE)
        if not phone:
            return self.async_abort(reason="no_phone")

        if not self.phone_data:
            client = IntersvyazApiClient(async_get_clientsession(self.hass))
            self.phone_data = {CONF_PHONE: phone}
            try:
                device_id = await client.send_sms(phone)
            except IntersvyazApiError as err:
                _LOGGER.debug("Ошибка отправки СМС: %s", err)
                device_id = uuid.uuid4().hex
            self.phone_data[CONF_DEVICE_ID] = device_id

        return await self.async_step_add_address_sms_code()

    async def async_step_add_address_sms_code(self, user_input: dict[str, Any] | None = None):
        """Ввод SMS-кода для подтверждения перед добавлением адреса."""
        errors: dict[str, str] = {}
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
                existing = {
                    addr[CONF_ADDRESS]
                    for addr in self.config_entry.data.get(CONF_ADDRESSES, [])
                }
                new_addresses = [
                    addr for addr in result["addresses"] if addr["ADDRESS"] not in existing
                ]
                if not new_addresses:
                    return self.async_abort(reason="no_new_addresses")
                self.phone_data["addresses"] = new_addresses
                return await self.async_step_add_address_select()

        return self.async_show_form(
            step_id="add_address_sms_code",
            data_schema=vol.Schema({vol.Required(CONF_SMS_CODE): str}),
            errors=errors,
        )

    async def async_step_add_address_select(self, user_input: dict[str, Any] | None = None):
        """Выбор новых адресов (чекбоксы) для добавления к записи."""
        errors: dict[str, str] = {}
        available = self.phone_data.get("addresses", [])
        address_map = {addr["ADDRESS"]: addr for addr in available}

        if user_input is not None:
            selected = user_input[CONF_ADDRESS]
            if not selected:
                errors["base"] = "no_address_selected"
            else:
                client = IntersvyazApiClient(async_get_clientsession(self.hass))
                addresses = list(self.config_entry.data.get(CONF_ADDRESSES, []))
                try:
                    for address_name in selected:
                        addr = address_map[address_name]
                        token = await client.token_by_address(
                            self.phone_data[CONF_AUTH_ID], addr["USER_ID"]
                        )
                        addresses.append(
                            {
                                CONF_ADDRESS: address_name,
                                CONF_USER_ID: addr["USER_ID"],
                                CONF_TOKEN: token,
                            }
                        )
                except IntersvyazApiError as err:
                    _LOGGER.error("Ошибка получения токена: %s", err)
                    errors["base"] = "token_error"
                else:
                    data = dict(self.config_entry.data)
                    data[CONF_ADDRESSES] = addresses
                    self.hass.config_entries.async_update_entry(self.config_entry, data=data)
                    return self.async_abort(reason="address_added")

        return self.async_show_form(
            step_id="add_address_select",
            data_schema=_addresses_schema(available),
            errors=errors,
        )

    # ------------------------------------------------------------------ #
    # Удаление адреса
    # ------------------------------------------------------------------ #
    async def async_step_remove_address(self, user_input: dict[str, Any] | None = None):
        """Выбор адресов (чекбоксы) для удаления из записи."""
        errors: dict[str, str] = {}
        current = self.config_entry.data.get(CONF_ADDRESSES, [])

        if len(current) <= 1:
            return self.async_abort(reason="cannot_remove_last_address")

        if user_input is not None:
            to_remove = set(user_input[CONF_ADDRESS])
            if not to_remove:
                errors["base"] = "no_address_selected"
            elif len(to_remove) >= len(current):
                errors["base"] = "cannot_remove_all"
            else:
                self._remove_devices(current, to_remove)
                remaining = [addr for addr in current if addr[CONF_ADDRESS] not in to_remove]
                data = dict(self.config_entry.data)
                data[CONF_ADDRESSES] = remaining
                self.hass.config_entries.async_update_entry(self.config_entry, data=data)
                return self.async_abort(reason="address_removed")

        return self.async_show_form(
            step_id="remove_address",
            data_schema=_stored_addresses_schema(current),
            errors=errors,
        )

    def _remove_devices(self, addresses: list[dict], to_remove: set[str]) -> None:
        """Удаляет устройства (и их сущности) для удаляемых адресов."""
        device_registry = dr.async_get(self.hass)
        entry_id = self.config_entry.entry_id
        primary_user_id = self.config_entry.data.get(CONF_PRIMARY_USER_ID)

        for addr in addresses:
            if addr[CONF_ADDRESS] not in to_remove:
                continue
            user_id = addr.get(CONF_USER_ID)
            is_primary = user_id is not None and user_id == primary_user_id
            identifier = (
                (DOMAIN, entry_id) if is_primary else (DOMAIN, f"{entry_id}_{user_id}")
            )
            device = device_registry.async_get_device(identifiers={identifier})
            if device:
                device_registry.async_remove_device(device.id)

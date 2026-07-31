"""Button platform for Intersvyaz Domofon integration."""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo

from .api import IntersvyazApiClient, IntersvyazApiError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Настройка кнопки в Home Assistant."""
    client: IntersvyazApiClient = hass.data[DOMAIN][entry.entry_id]["client"]
    async_add_entities([DomofonButton(client, entry.title, entry.entry_id)])


class DomofonButton(ButtonEntity):
    """Кнопка открытия домофона."""

    _attr_has_entity_name = True

    def __init__(self, client: IntersvyazApiClient, address: str, entry_id: str) -> None:
        self._client = client
        self._relay_id: str | None = None
        self._attr_name = "Открыть"
        self._attr_unique_id = f"domofon_button_{entry_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=address,
            manufacturer="Интерсвязь",
            model="Домофон IS74",
        )

    async def async_press(self) -> None:
        try:
            if not self._relay_id:
                self._relay_id = await self._client.get_relay_id()
            try:
                await self._client.open_door(self._relay_id)
            except IntersvyazApiError:
                self._relay_id = await self._client.get_relay_id()
                await self._client.open_door(self._relay_id)
        except IntersvyazApiError as err:
            _LOGGER.error("Не удалось открыть домофон: %s", err)
            raise HomeAssistantError(f"Не удалось открыть домофон: {err}") from err
        else:
            _LOGGER.info("Дверь успешно открыта")

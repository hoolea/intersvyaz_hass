import asyncio
import logging
import aiohttp
from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity

from . import DOMAIN, open_door, get_token, get_relay_id

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Настройка кнопки в Home Assistant."""
    session = aiohttp.ClientSession()
    token = await get_token(session, entry.data["username"], entry.data["password"])
    
    if not token:
        _LOGGER.error("Не удалось получить токен")
        return
    
    relay_id = await get_relay_id(session, token)
    
    if not relay_id:
        _LOGGER.error("Не удалось получить ID реле")
        return

    async_add_entities([DomofonButton(hass, token, relay_id)])

class DomofonButton(ButtonEntity):
    """Кнопка открытия домофона."""

    def __init__(self, hass: HomeAssistant, token: str, relay_id: str):
        """Инициализация кнопки."""
        self._hass = hass
        self._token = token
        self._relay_id = relay_id
        self._attr_name = "Открыть домофон"
        self._attr_unique_id = f"domofon_{relay_id}"

    async def async_press(self):
        """Обработчик нажатия кнопки."""
        session = aiohttp.ClientSession()
        await open_door(session, self._token, self._relay_id)
        await session.close()

import logging
import aiohttp
from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import DOMAIN, open_door, get_token, get_relay_id

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Настройка кнопки в Home Assistant."""
    session = async_get_clientsession(hass)
    
    # Получаем токен из сохраненных данных конфигурации
    token = entry.data.get("token")
    if not token:
        _LOGGER.error("Токен не найден в конфигурации")
        return
    
    # Создаем кнопку с сохраненными данными
    async_add_entities([DomofonButton(hass, token)])

class DomofonButton(ButtonEntity):
    """Кнопка открытия домофона."""

    def __init__(self, hass: HomeAssistant, token: str):
        """Инициализация кнопки."""
        self._hass = hass
        self._token = token
        self._attr_name = "Открыть домофон"
        self._attr_unique_id = "domofon_button"
        
        # Добавляем информацию об устройстве
        self._attr_device_info = {
            "identifiers": {("intersvyaz_domofon", "main")},
            "name": "Домофон Интерсвязь",
            "manufacturer": "Интерсвязь",
            "model": "Домофон IS74",
            "sw_version": "1.0",
        }

    async def async_press(self):
        """Обработчик нажатия кнопки."""
        session = async_get_clientsession(self._hass)
        
        # Получаем ID реле при каждом нажатии
        relay_id = await get_relay_id(session, self._token)
        if not relay_id:
            _LOGGER.error("Не удалось получить ID реле")
            return
            
        await open_door(session, self._token, relay_id)

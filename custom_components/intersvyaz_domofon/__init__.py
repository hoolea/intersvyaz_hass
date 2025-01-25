import logging
import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

DOMAIN = "domofon"
BASE_URL = "https://api.is74.ru"
_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Настройка интеграции."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = entry.data

    await hass.config_entries.async_forward_entry_setup(entry, "button")

    return True

async def get_token(session, username, password):
    """Получение токена авторизации."""
    url = f"{BASE_URL}/auth/mobile"
    payload = {"username": username, "password": password}
    headers = {"Content-Type": "application/json"}
    async with session.post(url, json=payload, headers=headers) as resp:
        if resp.status == 200:
            data = await resp.json()
            return data.get("TOKEN")
    return None

async def get_relay_id(session, token):
    """Получение ID реле."""
    url = f"{BASE_URL}/domofon/relays"
    headers = {"Authorization": f"Bearer {token}"}

    async with session.get(url, headers=headers) as resp:
        data = await resp.json()
        _LOGGER.info(f"Ответ API на relays: {data}")  # Логируем ответ

        if isinstance(data, list) and len(data) > 0:
            return data[0]["RELAY_ID"]  # Берем первый реле
        else:
            _LOGGER.error("API не вернул список реле!")
    
    return None

async def open_door(session, token, relay_id):
    """Открытие домофона."""
    url = f"{BASE_URL}/domofon/relays/{relay_id}/open?from=app"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    async with session.post(url, headers=headers) as resp:
        if resp.status == 200:
            _LOGGER.info("Дверь успешно открыта")
        else:
            _LOGGER.error("Ошибка открытия двери")

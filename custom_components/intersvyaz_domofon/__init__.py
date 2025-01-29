import logging
import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

# Определение домена интеграции и базовых URL
DOMAIN = "domofon"
BASE_URL = "https://api.is74.ru"
BASE_URL_CAM = "https://cams.is74.ru"

# Логгер для отладки
_LOGGER = logging.getLogger(__name__)

async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Настройка интеграции."""
    hass.data.setdefault(DOMAIN, {})
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Настройка интеграции для камеры и кнопки."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = entry.data

    # Настройка камеры и кнопки
    await hass.config_entries.async_forward_entry_setups(entry, ["camera", "button"])
    
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Разгрузка конфигурационной записи."""
    unload_ok = await hass.config_entries.async_forward_entry_unload(entry, "camera")
    unload_ok_button = await hass.config_entries.async_forward_entry_unload(entry, "button")
    
    if unload_ok and unload_ok_button:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok and unload_ok_button

async def get_token(session: aiohttp.ClientSession, username: str, password: str) -> str | None:
    """Получение токена авторизации."""
    url = f"{BASE_URL}/auth/mobile"
    payload = {"username": username, "password": password}
    headers = {"Content-Type": "application/json"}
    
    async with session.post(url, json=payload, headers=headers) as resp:
        if resp.status == 200:
            data = await resp.json()
            return data.get("TOKEN")
    _LOGGER.error("Ошибка получения токена")
    return None

async def get_relay_id(session: aiohttp.ClientSession, token: str) -> str | None:
    """Получение ID реле."""
    url = f"{BASE_URL}/domofon/relays"
    headers = {"Authorization": f"Bearer {token}"}
    
    async with session.get(url, headers=headers) as resp:
        data = await resp.json()
        _LOGGER.info(f"Ответ API на relays: {data}")
        
        if isinstance(data, list) and data:
            return data[0].get("RELAY_ID")
        _LOGGER.error("API не вернул список реле!")
    return None

async def get_group_id(session: aiohttp.ClientSession, token: str) -> str | None:
    """Получение ID группы с названием, начинающимся на 'Умный двор'."""
    url = f"{BASE_URL_CAM}/api/get-group/"
    headers = {"Authorization": f"Bearer {token}"}
    
    async with session.get(url, headers=headers) as resp:
        data = await resp.json()
        _LOGGER.info(f"Ответ API на group: {data}")
        
        for group in data:
            if group.get("NAME", "").startswith("Умный двор"):
                return group.get("ID")
        _LOGGER.error("Не найдена группа 'Умный двор'!")
    return None

async def get_uuid_cam(session: aiohttp.ClientSession, token: str, group_id: str) -> list[str]:
    """Получение списка UUID камер."""
    url = f"{BASE_URL_CAM}/api/get-group/{group_id}"
    headers = {"Authorization": f"Bearer {token}"}
    
    async with session.get(url, headers=headers) as resp:
        data = await resp.json()
        _LOGGER.info(f"Ответ API на камеры: {data}")
        
        if isinstance(data, list) and data:
            return [camera["UUID"] for camera in data if "UUID" in camera]
        _LOGGER.error("API не вернул список UUID камер!")
    return []

async def open_door(session: aiohttp.ClientSession, token: str, relay_id: str) -> None:
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

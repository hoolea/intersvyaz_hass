import logging
import aiohttp
import asyncio
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

DOMAIN = "domofon"
BASE_URL = "https://api.is74.ru"
BASE_URL_CAM = "https://cams.is74.ru"
_LOGGER = logging.getLogger(__name__)

async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Intercom Camera component."""
    hass.data.setdefault(DOMAIN, {})
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Настройка интеграции для камеры и кнопки."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = entry.data

    # Настройка камеры
    await hass.config_entries.async_forward_entry_setup(entry, "camera")
    # Настройка кнопки
    await hass.config_entries.async_forward_entry_setup(entry, "button")
    
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Разгрузка камеры
    unload_ok = await hass.config_entries.async_forward_entry_unload(entry, "camera")
    # Разгрузка кнопки
    unload_ok_button = await hass.config_entries.async_forward_entry_unload(entry, "button")
    
    if unload_ok and unload_ok_button:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok and unload_ok_button

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

async def get_group_id(session, token):
    """Получение ID группы с названием, начинающимся на 'Умный двор'."""
    url = f"{BASE_URL_CAM}/api/get-group/"
    headers = {"Authorization": f"Bearer {token}"}

    async with session.get(url, headers=headers) as resp:
        data = await resp.json()
        _LOGGER.info(f"Ответ API на group (тип {type(data)}): {data}")  # Логируем ответ

        # Ищем группу, название которой начинается на 'Умный двор'
        for group in data:
            if group.get("NAME", "").startswith("Умный двор"):
                group_id = group.get("ID")
                print(f"Полученный group_id: {group_id}")  # Выводим group_id
                return group_id

        # Если не нашли нужную группу
        _LOGGER.error("Не найдена группа, название которой начинается на 'Умный двор'!")
    
    return None

async def get_uuid_cam(session, token, group_id):
    """Получение UUID всех камер."""
    url = f"{BASE_URL_CAM}/api/get-group/{group_id}"
    headers = {"Authorization": f"Bearer {token}"}

    async with session.get(url, headers=headers) as resp:
        data = await resp.json()
        _LOGGER.info(f"Ответ API на relays: {data}")  # Логируем ответ

        if isinstance(data, list) and len(data) > 0:
            uuids = [camera["UUID"] for camera in data if "UUID" in camera]
            return uuids  # Возвращаем все UUID
        else:
            _LOGGER.error("API не вернул список UUID!")
    return []

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

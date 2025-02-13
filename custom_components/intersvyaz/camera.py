from __future__ import annotations

from typing import Any
import logging
import aiohttp
import voluptuous as vol
from aiohttp import web
from haffmpeg.camera import CameraMjpeg
from haffmpeg.tools import IMAGE_JPEG
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from homeassistant.components.camera import (
    PLATFORM_SCHEMA as CAMERA_PLATFORM_SCHEMA,
    Camera,
    CameraEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_aiohttp_proxy_stream
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .const import CONF_UUID, CONF_TOKEN
from . import get_token, get_uuid_cam, get_group_id

# Логгер для вывода сообщений об ошибках
_LOGGER = logging.getLogger(__name__)

# Значение по умолчанию для имени камеры
DEFAULT_NAME = "IS74 Camera"

# Определение схемы конфигурации платформы
PLATFORM_SCHEMA = CAMERA_PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_UUID): cv.string,
        vol.Required(CONF_TOKEN): cv.string,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    }
)

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Настройка платформы камеры из записи конфигурации."""
    session = async_get_clientsession(hass)
    
    # Получаем токен из сохраненных данных конфигурации
    token = entry.data.get("token")
    if not token:
        _LOGGER.error("Токен не найден в конфигурации")
        return

    # Получаем group_id
    group_id = await get_group_id(session, token)
    if not group_id:
        _LOGGER.error("Не удалось получить group_id")
        return

    # Получаем информацию о камерах
    cameras_info = await get_cameras_info(session, token, group_id)
    if not cameras_info:
        _LOGGER.error("Не удалось получить информацию о камерах")
        return

    # Создаём камеры
    cameras = [IS74Camera(entry.data, token, camera_info) for camera_info in cameras_info]
    
    # Добавляем камеры
    async_add_entities(cameras)

async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Настройка камеры IS74 через YAML-конфигурацию."""
    async_add_entities([IS74Camera(config)])

async def get_cameras_info(session: aiohttp.ClientSession, token: str, group_id: str) -> list[dict] | None:
    """Получает информацию о камерах."""
    try:
        headers = {"Authorization": f"Bearer {token}"}
        async with session.get(
            f"https://cams.is74.ru/api/get-group/{group_id}",
            headers=headers,
        ) as response:
            if response.status == 200:
                return await response.json()
            return None
    except aiohttp.ClientError as err:
        _LOGGER.error("Ошибка при получении информации о камерах: %s", err)
        return None

class IS74Camera(Camera):
    """Реализация камеры IS74."""
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, config: dict[str, Any], token: str, camera_info: dict) -> None:
        """Инициализация камеры IS74."""
        super().__init__()
        self._uuid: str = camera_info["UUID"]
        self._name: str = camera_info["NAME"]
        self._token: str = token
        self._attr_unique_id = f"is74_camera_{self._uuid}"
        self._input: str = (
            f"https://cdn.cams.is74.ru/hls/playlists/multivariant.m3u8?uuid={self._uuid}&realtime=1&token=bearer-{self._token}"
        )
        
        # Добавляем информацию об устройстве
        self._attr_device_info = {
            "identifiers": {("intersvyaz_domofon", config.get("device_id", "main"))},
            "name": "Домофон Интерсвязь",
            "manufacturer": "Интерсвязь",
            "model": "Домофон IS74",
            "sw_version": "1.0",
        }

    async def stream_source(self) -> str:
        """Возвращает источник потока."""
        return self._input

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Возвращает статичное изображение с камеры (не реализовано)."""
        return None

    @property
    def name(self) -> str:
        """Возвращает имя камеры."""
        return self._name

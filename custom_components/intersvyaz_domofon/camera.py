from __future__ import annotations

from typing import Any

from aiohttp import web
from haffmpeg.camera import CameraMjpeg
from haffmpeg.tools import IMAGE_JPEG
import voluptuous as vol
import aiohttp

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

DEFAULT_NAME = "IS74 Camera"

PLATFORM_SCHEMA = CAMERA_PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_UUID): cv.string,
        vol.Required(CONF_TOKEN): cv.string,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    }
)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up the camera platform from a config entry."""
    
    # Получение токена
    session = aiohttp.ClientSession()
    token = await get_token(session, entry.data["username"], entry.data["password"])
    
    if not token:
        _LOGGER.error("Не удалось получить токен")
        return
    
    # Получение group_id
    group_id = await get_group_id(session, token)
    
    if not group_id:
        _LOGGER.error("Не удалось получить group_id")
        return

    # Получение UUID камеры
    uuids = await get_uuid_cam(session, token, group_id)
    
    if not uuids:
        _LOGGER.error("Не удалось получить UUID камеры")
        return

    # Создание списка камер для каждого UUID
    cameras = []
    for uuid in uuids:
        cameras.append(IS74Camera(entry.data, token, uuid))

    # Добавление всех камер
    async_add_entities(cameras)

    # Закрытие сессии
    await session.close()

async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up a IS74 camera from YAML configuration."""
    async_add_entities([IS74Camera(config)])

class IS74Camera(Camera):
    """An implementation of an IS74 Camera."""

    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, config: dict[str, Any], token: str, uuid: str) -> None:
        """Initialize an IS74 Camera."""
        super().__init__()
        self._name: str = config.get(CONF_NAME, DEFAULT_NAME)
        self._uuid: str = uuid
        self._token: str = token
        self._input: str = f"https://cdn.cams.is74.ru/hls/playlists/multivariant.m3u8?uuid={self._uuid}&realtime=1&token=bearer-{self._token}"

    async def stream_source(self) -> str:
        """Return the stream source."""
        return self._input

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a still image response from the camera."""
        return None  # Опционально, можно реализовать

    @property
    def name(self) -> str:
        """Return the name of this camera."""
        return self._name

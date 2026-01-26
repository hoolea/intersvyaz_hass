"""Camera platform for Intersvyaz Domofon integration."""
import logging
import aiohttp
from typing import Optional
from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import DeviceInfo

from . import DOMAIN, BASE_URL_CAM, get_group_id, get_uuid_cam
from .const import CONF_TOKEN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
):
    """Настройка камер в Home Assistant."""
    session = async_get_clientsession(hass)
    
    # Получаем токен из сохраненных данных конфигурации
    token = entry.data.get(CONF_TOKEN)
    if not token:
        _LOGGER.error("Токен не найден в конфигурации")
        return
    
    # Получаем ID группы камер
    group_id = await get_group_id(session, token)
    if not group_id:
        _LOGGER.error("Не удалось получить ID группы камер")
        return
    
    # Получаем список камер с их данными
    cameras_data = await _get_cameras_data(session, token, group_id)
    if not cameras_data:
        _LOGGER.warning("Не найдено камер для отображения")
        return
    
    _LOGGER.info(f"Создание {len(cameras_data)} камер")
    
    # Создаем камеры для каждого UUID
    cameras = []
    for camera_data in cameras_data:
        uuid = camera_data.get("UUID")
        if not uuid:
            _LOGGER.warning(f"Пропущена камера без UUID: {camera_data}")
            continue
            
        name = camera_data.get("NAME") or camera_data.get("TITLE") or f"Камера {uuid[:8]}"
        
        # Получаем URL потока из данных API, если доступен
        stream_url = None
        media = camera_data.get("MEDIA", {})
        hls_data = media.get("HLS", {})
        live_data = hls_data.get("LIVE", {})
        if live_data:
            # Используем MAIN поток (обычный, более стабильный) вместо LOW_LATENCY
            stream_url = live_data.get("MAIN") or live_data.get("LOW_LATENCY")
        
        _LOGGER.info(f"Создание камеры: {name} (UUID: {uuid})")
        cameras.append(
            IntersvyazCamera(
                hass=hass,
                token=token,
                uuid=uuid,
                name=name,
                unique_id=f"intersvyaz_camera_{uuid}",
                stream_url=stream_url
            )
        )
    
    if cameras:
        _LOGGER.info(f"Добавление {len(cameras)} камер в Home Assistant")
        async_add_entities(cameras, update_before_add=True)
    else:
        _LOGGER.warning("Не создано ни одной камеры")


async def _get_cameras_data(
    session: aiohttp.ClientSession,
    token: str,
    group_id: str
) -> list:
    """Получение данных о камерах из API."""
    try:
        url = f"{BASE_URL_CAM}/api/get-group/{group_id}"
        headers = {"Authorization": f"Bearer {token}"}
        
        _LOGGER.debug(f"Запрос данных камер: {url}")
        async with session.get(url, headers=headers) as resp:
            _LOGGER.debug(f"Статус ответа API камер: {resp.status}")
            if resp.status == 200:
                data = await resp.json()
                _LOGGER.info(f"Ответ API на камеры: {data}")
                if isinstance(data, list):
                    _LOGGER.info(f"Найдено камер: {len(data)}")
                    return data
                _LOGGER.warning(f"Неожиданный формат данных камер: {type(data)}, данные: {data}")
            else:
                response_text = await resp.text()
                _LOGGER.error(f"Ошибка получения данных камер. Статус: {resp.status}, Ответ: {response_text}")
    except Exception as e:
        _LOGGER.error(f"Ошибка при получении данных камер: {e}", exc_info=True)
    
    return []

class IntersvyazCamera(Camera):
    """Камера Интерсвязь (WebRTC-ready)."""

    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(
        self,
        hass: HomeAssistant,
        token: str,
        uuid: str,
        name: str,
        unique_id: str,
        stream_url: Optional[str] = None,
    ):
        super().__init__()
        self._hass = hass
        self._token = token
        self._uuid = uuid

        self._attr_name = name
        self._attr_unique_id = unique_id
        self._attr_available = True

        # Превью (snapshot)
        self._attr_still_image_url = (
            f"https://cdn.cams.is74.ru/snapshot"
            f"?uuid={self._uuid}"
            f"&token=bearer-{self._token}"
        )

        self._stream_url = stream_url

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "main")},
            name="Домофон Интерсвязь",
            manufacturer="Интерсвязь",
            model="IS74",
        )

    async def async_stream_source(self) -> Optional[str]:
        """Источник видеопотока для WebRTC (через go2rtc)."""
        if self._stream_url:
            return self._stream_url

        return (
            f"https://cdn.cams.is74.ru/hls/playlists/multivariant.m3u8"
            f"?uuid={self._uuid}"
            f"&token=bearer-{self._token}"
        )


    async def async_camera_image(
        self, width: Optional[int] = None, height: Optional[int] = None
    ) -> Optional[bytes]:
        """Получение статического изображения с камеры."""
        try:
            session = async_get_clientsession(self._hass)
            snapshot_url = (
                f"https://cdn.cams.is74.ru/snapshot"
                f"?uuid={self._uuid}"
                f"&token=bearer-{self._token}"
            )
            _LOGGER.debug(f"Запрос snapshot для камеры {self._uuid}: {snapshot_url}")
            async with session.get(
                snapshot_url,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    return await resp.read()
                else:
                    _LOGGER.warning(f"Не удалось получить snapshot. Статус: {resp.status}")
        except Exception as e:
            _LOGGER.debug(f"Ошибка при получении snapshot для камеры {self._uuid}: {e}")
        
        return None

    async def async_update(self) -> None:
        """Обновление состояния камеры."""
        # Для HLS потоков проверка доступности через HEAD может быть ненадежной
        # Камера считается доступной, если она была создана
        # Home Assistant сам проверит доступность потока при попытке воспроизведения
        pass

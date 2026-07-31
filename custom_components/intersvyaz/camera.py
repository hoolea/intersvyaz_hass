"""Camera platform for Intersvyaz Domofon integration."""
from __future__ import annotations

import logging
import re
from typing import Optional

import aiohttp

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import IntersvyazDataUpdateCoordinator
from .api import IntersvyazApiClient
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


def _stream_name(name: str) -> str:
    """Convert camera name to safe go2rtc stream name."""
    name = name.lower()
    name = re.sub(r"[^0-9a-zа-яё_]+", "_", name, flags=re.IGNORECASE)
    return name.strip("_")



async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Настройка камер в Home Assistant."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: IntersvyazDataUpdateCoordinator = data["coordinator"]
    client: IntersvyazApiClient = data["client"]

    if not coordinator.data:
        _LOGGER.warning("Не найдено камер для отображения")
        return

    cameras = [
        IntersvyazCamera(
            hass,
            coordinator,
            client,
            uuid,
            entry.title,
            entry.entry_id,
        )
        for uuid in coordinator.data
    ]

    _LOGGER.info("Добавление %d камер в Home Assistant", len(cameras))
    async_add_entities(cameras)

    _LOGGER.info("")
    _LOGGER.info("=" * 72)
    _LOGGER.info("Copy this block into go2rtc.yaml")
    _LOGGER.info("")
    _LOGGER.info("streams:")

    rtsp_urls: list[str] = []

    for camera in cameras:
        media = camera._camera_data.get("MEDIA", {})
        mse_url = (
            media.get("MSE", {}).get("LIVE")
            or camera._camera_data.get("REALTIME_WS", {}).get("combined")
        )
        if not mse_url:
            continue
        if mse_url.startswith("ws://"):
            mse_url = "wss://" + mse_url[5:]
        elif mse_url.startswith("http://"):
            mse_url = "wss://" + mse_url[7:]
        elif mse_url.startswith("https://"):
            mse_url = "wss://" + mse_url[8:]

        stream = _stream_name(camera.name)

        _LOGGER.info("  %s:", stream)
        _LOGGER.info('    - "flussonic:%s"', mse_url)
        rtsp_urls.append(f"rtsp://127.0.0.1:8554/{stream}")

    if rtsp_urls:
        _LOGGER.info("")
        _LOGGER.info("After restarting go2rtc the streams will be available as:")
        _LOGGER.info("")
        for url in rtsp_urls:
            _LOGGER.info("  %s", url)
    else:
        _LOGGER.info("")
        _LOGGER.info("No MSE streams were returned by API.")

    _LOGGER.info("=" * 72)
    _LOGGER.info("")


class IntersvyazCamera(CoordinatorEntity[IntersvyazDataUpdateCoordinator], Camera):
    """Камера Интерсвязь."""

    _attr_supported_features = CameraEntityFeature.STREAM

    # У CDN Интерсвязь URL сегментов выглядит так:
    #   .../<file>_main_....ts/<hash-token>?i=...&ik=...
    # то есть после ".ts" идёт ещё один компонент пути без обычного
    # расширения. Начиная с ffmpeg 6.x демультиплексор HLS по умолчанию
    # проверяет расширение последнего сегмента URL и отклоняет всё, что не
    # входит в allowed_extensions (защита от эксплойтов), из-за чего
    # штатный компонент stream не мог читать сегменты этого CDN.
    # allowed_extensions=ALL отключает эту проверку.
    _attr_stream_options = {"allowed_extensions": "ALL"}

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: IntersvyazDataUpdateCoordinator,
        client: IntersvyazApiClient,
        uuid: str,
        address: str,
        entry_id: str,
    ) -> None:

        CoordinatorEntity.__init__(self, coordinator)
        Camera.__init__(self)
        self._hass = hass
        self._client = client
        self._uuid = uuid

        camera_data = coordinator.data.get(uuid, {})
        self._attr_name = (
            camera_data.get("NAME") or camera_data.get("TITLE") or f"Камера {uuid[:8]}"
        )
        self._attr_unique_id = f"intersvyaz_camera_{uuid}"
        self._attr_has_entity_name = True

        self._attr_device_info = DeviceInfo(
        identifiers={(DOMAIN, entry_id)},
        name=address,
        manufacturer="Интерсвязь",
        model="Домофон IS74",
        )

        _LOGGER.debug(
            "Камера %s создана, supported_features=%s",
            self._attr_unique_id,
            self._attr_supported_features,
        )

    @property
    def _camera_data(self) -> dict:
        return self.coordinator.data.get(self._uuid, {}) if self.coordinator.data else {}

    @property
    def available(self) -> bool:
        """Камера доступна, пока координатор её видит в последнем ответе API."""
        return bool(self.coordinator.last_update_success and self._camera_data)

    @property
    def extra_state_attributes(self) -> dict:
        """Доп. атрибуты, в т.ч. WebSocket MSE-URL для карточки intersvyaz-mse-camera.

        MEDIA.MSE.LIVE — это отдельный WebSocket-эндпоинт (Media Source
        Extensions), которым, судя по всему, пользуется официальное
        приложение Интерсвязь для гладкого воспроизведения без ffmpeg/HLS.
        Обновляется вместе с координатором, дополнительных запросов к API
        не требует.
        """
        media = self._camera_data.get("MEDIA", {})
        mse_url = media.get("MSE", {}).get("LIVE") or self._camera_data.get(
            "REALTIME_WS", {}
        ).get("combined")
        attrs: dict = {"uuid": self._uuid}
        if mse_url:
            attrs["mse_url"] = mse_url
        return attrs

    async def async_stream_source(self) -> Optional[str]:
        """Источник видеопотока.

        Раньше ссылка на поток вычислялась один раз при запуске Home
        Assistant и кэшировалась в атрибуте сущности. Поскольку ссылки на
        HLS-потоки от API подписаны и живут ограниченное время, это
        приводило к тому, что воспроизведение переставало работать вскоре
        после старта. Теперь ссылка всегда берётся из последних данных
        координатора; если данные устарели, предварительно запрашивается
        обновление.
        """
        _LOGGER.debug(
            "Камера %s: async_stream_source() вызван (available=%s, "
            "coordinator.last_update_success=%s, uuid_in_data=%s)",
            self._uuid,
            self.available,
            self.coordinator.last_update_success,
            self._uuid in (self.coordinator.data or {}),
        )
        try:
            if not self.coordinator.last_update_success or self._uuid not in (
                self.coordinator.data or {}
            ):
                await self.coordinator.async_request_refresh()

            media = self._camera_data.get("MEDIA", {})
            live = media.get("HLS", {}).get("LIVE", {})
            _LOGGER.debug("Камера %s: варианты потока от API: %s", self._uuid, live)

            if live:
                url = live.get("MAIN") or live.get("LOW_LATENCY")
                if url:
                    variant = "MAIN" if live.get("MAIN") else "LOW_LATENCY"
                    _LOGGER.info(
                        "Камера %s: используется вариант потока %s: %s",
                        self._uuid,
                        variant,
                        url,
                    )
                    return url

            if self._client.token:
                url = self._client.fallback_stream_url(self._uuid)
                _LOGGER.info(
                    "Камера %s: используется резервный (fallback) URL потока: %s",
                    self._uuid,
                    url,
                )
                return url

            _LOGGER.warning(
                "Камера %s: нет ни MEDIA.HLS.LIVE, ни токена — возвращаю None", self._uuid
            )
            return None
        except Exception:
            _LOGGER.exception(
                "Камера %s: исключение в async_stream_source()", self._uuid
            )
            raise

    async def async_camera_image(
        self, width: Optional[int] = None, height: Optional[int] = None
    ) -> Optional[bytes]:
        """Получение статического изображения с камеры (всегда со свежим токеном)."""
        if not self._client.token:
            return None

        session = async_get_clientsession(self._hass)
        snapshot_url = self._client.snapshot_url(self._uuid)
        try:
            async with session.get(
                snapshot_url, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    return await resp.read()
                _LOGGER.warning(
                    "Не удалось получить snapshot камеры %s: HTTP %s", self._uuid, resp.status
                )
        except aiohttp.ClientError as err:
            _LOGGER.debug("Ошибка при получении snapshot камеры %s: %s", self._uuid, err)

        return None

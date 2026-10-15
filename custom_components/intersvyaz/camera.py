"""Camera platform for Intersvyaz Domofon integration."""
from __future__ import annotations

import logging
import re
from typing import Optional

import aiohttp

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import IntersvyazDataUpdateCoordinator
from .api import IntersvyazApiClient
from .const import (
    CONF_GO2RTC_ENABLED,
    CONF_GO2RTC_URL,
    DEFAULT_GO2RTC_ENABLED,
    DEFAULT_GO2RTC_URL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def _stream_name(name: str) -> str:
    """Convert camera name to safe go2rtc stream name."""
    name = name.lower()
    name = re.sub(r"[^0-9a-zа-яё_]+", "_", name, flags=re.IGNORECASE)
    return name.strip("_")


def _mse_url(camera_data: dict) -> str | None:
    media = camera_data.get("MEDIA") or {}
    return (media.get("MSE") or {}).get("LIVE") or (
        camera_data.get("REALTIME_WS") or {}
    ).get("combined")


def _go2rtc_src(camera: "IntersvyazCamera") -> str | None:
    """MSE-URL камеры в виде go2rtc-источника (flussonic:wss://...)."""
    mse_url = _mse_url(camera._camera_data)
    if not mse_url:
        return None

    if mse_url.startswith("ws://"):
        mse_url = "wss://" + mse_url[5:]
    elif mse_url.startswith("http://"):
        mse_url = "wss://" + mse_url[7:]
    elif mse_url.startswith("https://"):
        mse_url = "wss://" + mse_url[8:]

    return f"flussonic:{mse_url}"


async def _async_sync_go2rtc_streams(
    hass: HomeAssistant, entry: ConfigEntry, cameras: list["IntersvyazCamera"]
) -> None:
    """Регистрирует/обновляет потоки камер в go2rtc через его REST API.

    Вместо того чтобы просить пользователя вручную копировать блок
    "streams:" в go2rtc.yaml, интеграция сама делает
    PUT /api/streams?name=...&src=... на запущенный go2rtc. Это создаёт
    поток, если его ещё нет, либо обновляет источник существующего —
    без перезапуска go2rtc и без редактирования файлов конфигурации.
    """
    if not entry.options.get(CONF_GO2RTC_ENABLED, DEFAULT_GO2RTC_ENABLED):
        return

    base_url = (entry.options.get(CONF_GO2RTC_URL) or DEFAULT_GO2RTC_URL).rstrip("/")
    session = async_get_clientsession(hass)

    for camera in cameras:
        src = _go2rtc_src(camera)
        if not src:
            continue

        stream = camera.go2rtc_stream
        try:
            async with session.put(
                f"{base_url}/api/streams",
                params={"name": stream, "src": src},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    _LOGGER.warning(
                        "go2rtc: не удалось зарегистрировать поток %s (HTTP %s): %s",
                        stream,
                        resp.status,
                        text,
                    )
                else:
                    _LOGGER.debug(
                        "go2rtc: поток %s зарегистрирован/обновлён (%s)", stream, base_url
                    )
        except aiohttp.ClientError as err:
            _LOGGER.warning(
                "go2rtc: не удалось подключиться к %s (%s). Проверьте, что go2rtc "
                "запущен и адрес указан верно в настройках интеграции "
                "(Настройки → Интервал обновления)",
                base_url,
                err,
            )
            return  # сервер недоступен — нет смысла пробовать оставшиеся камеры


def _log_manual_go2rtc_block(cameras: list["IntersvyazCamera"]) -> None:
    """Выводит блок для ручного копирования в go2rtc.yaml (запасной вариант)."""
    _LOGGER.info("")
    _LOGGER.info("=" * 72)
    _LOGGER.info(
        "Автоматическая регистрация в go2rtc отключена или недоступна. "
        "Скопируйте блок ниже в go2rtc.yaml"
    )
    _LOGGER.info("")
    _LOGGER.info("streams:")

    rtsp_urls: list[str] = []
    for camera in cameras:
        src = _go2rtc_src(camera)
        if not src:
            continue
        stream = camera.go2rtc_stream
        _LOGGER.info("  %s:", stream)
        _LOGGER.info('    - "%s"', src)
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


_CLAIMS_KEY = f"{DOMAIN}_camera_claims"


def _claims(hass: HomeAssistant) -> dict[str, dict[str, str]]:
    """Общий для всех записей реестр занятых unique_id и имён потоков go2rtc.

    Одна и та же камера «Умного двора» видна с нескольких адресов (соседние
    квартиры, квартира + дача в одном доме и т.п.). Раньше у неё был
    unique_id "intersvyaz_camera_<uuid>" для каждого адреса — второй экземпляр
    Home Assistant отбрасывал с ошибкой о неуникальном ID. Аналогично
    одноимённые камеры («Подъезд 1») с разных адресов перезаписывали друг
    друга в go2rtc.
    Значение: {"unique_ids": {unique_id: owner}, "streams": {name: owner}},
    где owner = "<entry_id>/<device identifier>".
    """
    return hass.data.setdefault(_CLAIMS_KEY, {"unique_ids": {}, "streams": {}})


def _release_claims(hass: HomeAssistant, entry_id: str) -> None:
    claims = _claims(hass)
    for table in claims.values():
        for key in [k for k, owner in table.items() if owner.startswith(f"{entry_id}/")]:
            table.pop(key)


def _camera_unique_id(
    hass: HomeAssistant, entry: ConfigEntry, account: dict, uuid: str
) -> str:
    """unique_id камеры, совместимый со старыми установками.

    Первый адрес, у которого встретилась камера, сохраняет прежний ID
    "intersvyaz_camera_<uuid>" (сущность и её история не меняются).
    Остальные адреса получают ID с суффиксом записи и адреса.
    """
    owner = f"{entry.entry_id}/{account['device_identifier'][1]}"
    legacy = f"intersvyaz_camera_{uuid}"
    suffixed = f"{legacy}_{entry.entry_id}{account['unique_suffix']}"
    claimed = _claims(hass)["unique_ids"]

    if claimed.get(legacy, owner) != owner:
        claimed[suffixed] = owner
        return suffixed

    # Если старый ID уже есть в реестре сущностей, он принадлежит тому
    # адресу (устройству), к которому сущность была привязана изначально.
    ent_reg = er.async_get(hass)
    entity_id = ent_reg.async_get_entity_id("camera", DOMAIN, legacy)
    if entity_id:
        reg_entry = ent_reg.async_get(entity_id)
        device = dr.async_get(hass).async_get_device(
            identifiers={account["device_identifier"]}
        )
        # Устройства этого адреса может ещё не быть (платформы кнопок и
        # камер поднимаются параллельно) — тогда сущность, уже привязанная к
        # какому-то устройству, точно принадлежит другому адресу.
        owned = reg_entry.config_entry_id == entry.entry_id and (
            reg_entry.device_id is None
            or (device is not None and reg_entry.device_id == device.id)
        )
        if not owned:
            claimed[suffixed] = owner
            return suffixed

    claimed[legacy] = owner
    return legacy


def _go2rtc_stream_name(
    hass: HomeAssistant, entry: ConfigEntry, account: dict, uuid: str, name: str
) -> str:
    """Уникальное имя потока go2rtc; для уникальных камер — как раньше."""
    owner = f"{entry.entry_id}/{account['device_identifier'][1]}/{uuid}"
    streams = _claims(hass)["streams"]
    stream = _stream_name(name) or f"intersvyaz_{uuid[:8]}"
    if streams.get(stream, owner) != owner:
        stream = f"{stream}_{uuid[:8]}"
    if streams.get(stream, owner) != owner:
        stream = f"{stream}_{account['device_identifier'][1][-8:]}"
    streams[stream] = owner
    return stream


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Настройка камер в Home Assistant."""
    accounts: list[dict] = hass.data[DOMAIN][entry.entry_id]["accounts"]
    entry.async_on_unload(lambda: _release_claims(hass, entry.entry_id))
    go2rtc_enabled = entry.options.get(CONF_GO2RTC_ENABLED, DEFAULT_GO2RTC_ENABLED)

    cameras: list["IntersvyazCamera"] = []
    for account in accounts:
        coordinator: IntersvyazDataUpdateCoordinator = account["coordinator"]
        if not coordinator.cameras:
            _LOGGER.debug("У адреса %s нет камер", account["address"])

        account_cameras: list["IntersvyazCamera"] = []

        @callback
        def _add_new_cameras(
            _account: dict = account,
            _coordinator: IntersvyazDataUpdateCoordinator = coordinator,
            _account_cameras: list["IntersvyazCamera"] = account_cameras,
            _initial: bool = False,
        ) -> None:
            known = {c.uuid for c in _account_cameras}
            new = [
                IntersvyazCamera(hass, entry, _coordinator, _account, uuid)
                for uuid in _coordinator.cameras
                if uuid not in known
            ]
            _account_cameras.extend(new)
            if _initial:
                cameras.extend(new)
                return
            if new:
                _LOGGER.info("Найдено новых камер: %d", len(new))
                async_add_entities(new)
            # При каждом обновлении (подписанные MSE-ссылки меняются) заново
            # регистрируем потоки этого адреса в go2rtc.
            if go2rtc_enabled:
                hass.async_create_task(
                    _async_sync_go2rtc_streams(hass, entry, list(_account_cameras))
                )

        _add_new_cameras(_initial=True)
        entry.async_on_unload(coordinator.async_add_listener(_add_new_cameras))

    if not cameras:
        _LOGGER.info("Камер для отображения пока нет")
        return

    _LOGGER.info("Добавление %d камер в Home Assistant", len(cameras))
    async_add_entities(cameras)

    if go2rtc_enabled:
        await _async_sync_go2rtc_streams(hass, entry, cameras)
    else:
        _log_manual_go2rtc_block(cameras)


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

    # Подписанная ссылка меняется при каждом обновлении и содержит токен —
    # хранить её в истории (recorder) бессмысленно и небезопасно.
    _unrecorded_attributes = frozenset({"mse_url"})

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coordinator: IntersvyazDataUpdateCoordinator,
        account: dict,
        uuid: str,
    ) -> None:

        CoordinatorEntity.__init__(self, coordinator)
        Camera.__init__(self)
        self._hass = hass
        self._client: IntersvyazApiClient = account["client"]
        self.uuid = uuid
        self._uuid = uuid

        camera_data = coordinator.cameras.get(uuid, {})
        self._attr_name = (
            camera_data.get("NAME") or camera_data.get("TITLE") or f"Камера {uuid[:8]}"
        )
        self._attr_unique_id = _camera_unique_id(hass, entry, account, uuid)
        self._attr_has_entity_name = True
        self.go2rtc_stream = _go2rtc_stream_name(
            hass, entry, account, uuid, self._attr_name
        )

        self._attr_device_info = DeviceInfo(
            identifiers={account["device_identifier"]},
            name=account["address"],
            manufacturer="Интерсвязь",
            model="Домофон IS74",
        )

    @property
    def _camera_data(self) -> dict:
        return self.coordinator.cameras.get(self._uuid, {})

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
        mse_url = _mse_url(self._camera_data)
        attrs: dict = {"uuid": self._uuid, "go2rtc_stream": self.go2rtc_stream}
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
            self._uuid in self.coordinator.cameras,
        )
        try:
            if (
                not self.coordinator.last_update_success
                or self._uuid not in self.coordinator.cameras
            ):
                await self.coordinator.async_request_refresh()

            media = self._camera_data.get("MEDIA", {})
            live = media.get("HLS", {}).get("LIVE", {})
            _LOGGER.debug(
                "Камера %s: варианты потока от API: %s", self._uuid, sorted(live)
            )

            if live:
                url = live.get("MAIN") or live.get("LOW_LATENCY")
                if url:
                    variant = "MAIN" if live.get("MAIN") else "LOW_LATENCY"
                    # Сам URL не логируем: он подписан и содержит токен.
                    _LOGGER.debug(
                        "Камера %s: используется вариант потока %s", self._uuid, variant
                    )
                    return url

            if self._client.token:
                url = self._client.fallback_stream_url(self._uuid)
                _LOGGER.debug(
                    "Камера %s: используется резервный (fallback) URL потока",
                    self._uuid,
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

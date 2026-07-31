"""Интеграция Интерсвязь Домофон."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
import homeassistant.helpers.config_validation as cv

from .api import IntersvyazApiClient, IntersvyazApiError, IntersvyazAuthError
from .const import (
    CONF_SCAN_INTERVAL,
    CONF_TOKEN,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CAMERA, Platform.BUTTON]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


class IntersvyazDataUpdateCoordinator(DataUpdateCoordinator[dict]):
    """Периодически обновляет список камер и подписанные ссылки на потоки.

    ВАЖНО: ссылки на HLS-потоки в поле MEDIA ответа API подписаны и имеют
    ограниченный срок жизни. Раньше они запрашивались один раз при старте
    Home Assistant и сохранялись в атрибуте сущности камеры навсегда — из-за
    этого воспроизведение видео переставало работать вскоре после
    перезапуска, хотя снимок (snapshot) и кнопка открытия двери продолжали
    работать, так как использовали долгоживущий Bearer-токен, а не
    подписанную ссылку. Координатор устраняет эту проблему, регулярно
    получая свежие данные.
    """

    def __init__(self, hass: HomeAssistant, client: IntersvyazApiClient, update_interval: int) -> None:
        self.client = client
        self.group_id: str | None = None
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=update_interval),
        )

    async def _async_update_data(self) -> dict[str, dict]:
        try:
            if not self.group_id:
                self.group_id = await self.client.get_group_id()
            cameras = await self.client.get_group_cameras(self.group_id)
        except IntersvyazAuthError as err:
            raise ConfigEntryAuthFailed("Токен недействителен, требуется повторный вход") from err
        except IntersvyazApiError as err:
            raise UpdateFailed(str(err)) from err

        return {c["UUID"]: c for c in cameras if c.get("UUID")}


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Настройка интеграции (YAML не поддерживается)."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Настройка записи конфигурации: клиент API, координатор, платформы."""
    session = async_get_clientsession(hass)

    token = entry.data.get(CONF_TOKEN)
    if not token:
        _LOGGER.error("Токен не найден в конфигурации")
        return False

    client = IntersvyazApiClient(session, token)
    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    coordinator = IntersvyazDataUpdateCoordinator(hass, client, scan_interval)

    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryAuthFailed:
        raise
    except UpdateFailed as err:
        raise ConfigEntryNotReady(str(err)) from err

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "client": client,
        "coordinator": coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    if not hass.services.has_service(DOMAIN, "open_door"):

        async def handle_open_door(call: ServiceCall) -> None:
            """Обработчик сервиса intersvyaz.open_door."""
            relay_id = await client.get_relay_id()
            await client.open_door(relay_id)

        hass.services.async_register(DOMAIN, "open_door", handle_open_door)

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Перезагрузка записи при изменении опций (например, интервала обновления)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Выгрузка записи конфигурации."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, "open_door")
    return unload_ok

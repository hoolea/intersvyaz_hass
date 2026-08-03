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
    CONF_ADDRESS,
    CONF_ADDRESSES,
    CONF_PRIMARY_USER_ID,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN,
    CONF_USER_ID,
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


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Миграция записи конфигурации на новую схему (список адресов).

    Раньше одна запись конфигурации содержала ровно один адрес (для
    телефонной авторизации — CONF_ADDRESS/CONF_USER_ID/CONF_TOKEN прямо в
    entry.data). Теперь одна запись может содержать несколько адресов
    (CONF_ADDRESSES), чтобы можно было добавлять и удалять их через
    Options Flow, не создавая отдельную интеграцию на каждый адрес.
    Единственный адрес старой записи становится "основным" (CONF_PRIMARY_USER_ID),
    благодаря чему у него сохраняется прежний device identifier и entity
    unique_id, и апгрейд не создаёт для существующих пользователей дубликаты
    устройств/сущностей.
    """
    if entry.version == 1:
        data = dict(entry.data)
        if CONF_ADDRESS in data and CONF_ADDRESSES not in data:
            user_id = data.pop(CONF_USER_ID, None)
            address = data.pop(CONF_ADDRESS, None)
            token = data.pop(CONF_TOKEN, None)
            data[CONF_ADDRESSES] = [
                {CONF_ADDRESS: address, CONF_USER_ID: user_id, CONF_TOKEN: token}
            ]
            data[CONF_PRIMARY_USER_ID] = user_id

        hass.config_entries.async_update_entry(entry, data=data, version=2)
        _LOGGER.debug("Запись %s мигрирована на версию 2", entry.entry_id)

    return True


def _build_accounts(
    hass: HomeAssistant,
    entry: ConfigEntry,
    session,
    scan_interval: int,
) -> list[dict]:
    """Построить список аккаунтов (клиент + координатор) для записи конфигурации.

    Запись, созданная через авторизацию по телефону, может содержать
    несколько адресов (CONF_ADDRESSES) — под каждый заводится свой клиент
    API и координатор, так как у каждого адреса свой Bearer-токен и своя
    группа камер. Запись, созданная через логин/пароль, по-прежнему
    содержит ровно один адрес (без CONF_ADDRESSES).
    """
    addresses = entry.data.get(CONF_ADDRESSES)
    primary_user_id = entry.data.get(CONF_PRIMARY_USER_ID)
    accounts: list[dict] = []

    if addresses:
        for addr in addresses:
            token = addr.get(CONF_TOKEN)
            if not token:
                continue
            user_id = addr.get(CONF_USER_ID)
            is_primary = user_id is not None and user_id == primary_user_id
            client = IntersvyazApiClient(session, token)
            coordinator = IntersvyazDataUpdateCoordinator(hass, client, scan_interval)
            accounts.append(
                {
                    "client": client,
                    "coordinator": coordinator,
                    "address": addr.get(CONF_ADDRESS) or entry.title,
                    "user_id": user_id,
                    "device_identifier": (DOMAIN, entry.entry_id)
                    if is_primary
                    else (DOMAIN, f"{entry.entry_id}_{user_id}"),
                    "unique_suffix": "" if is_primary else f"_{user_id}",
                }
            )
    else:
        token = entry.data.get(CONF_TOKEN)
        if token:
            client = IntersvyazApiClient(session, token)
            coordinator = IntersvyazDataUpdateCoordinator(hass, client, scan_interval)
            accounts.append(
                {
                    "client": client,
                    "coordinator": coordinator,
                    "address": entry.title,
                    "user_id": None,
                    "device_identifier": (DOMAIN, entry.entry_id),
                    "unique_suffix": "",
                }
            )

    return accounts


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Настройка записи конфигурации: клиенты API, координаторы, платформы."""
    session = async_get_clientsession(hass)
    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

    accounts = _build_accounts(hass, entry, session, scan_interval)
    if not accounts:
        _LOGGER.error("Не найдено ни одного адреса в конфигурации")
        return False

    for account in accounts:
        try:
            await account["coordinator"].async_config_entry_first_refresh()
        except ConfigEntryAuthFailed:
            raise
        except UpdateFailed as err:
            raise ConfigEntryNotReady(str(err)) from err

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {"accounts": accounts}

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    if not hass.services.has_service(DOMAIN, "open_door"):
        # Сервис исторически привязан к первому обнаруженному аккаунту
        # (см. кнопку "Открыть" у каждого устройства для точечного
        # управления конкретным адресом/домофоном).
        client = accounts[0]["client"]

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

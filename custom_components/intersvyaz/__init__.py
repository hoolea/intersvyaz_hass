"""Интеграция Интерсвязь Домофон."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
import homeassistant.helpers.config_validation as cv
from homeassistant.util import dt as dt_util

from .api import (
    IntersvyazApiClient,
    IntersvyazApiError,
    IntersvyazAuthError,
    IntersvyazNoCamerasError,
)
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


GROUPS_REFRESH_INTERVAL = timedelta(hours=1)
SERVICE_OPEN_DOOR = "open_door"
ATTR_RELAY_ID = "relay_id"

OPEN_DOOR_SCHEMA = vol.Schema({vol.Optional(ATTR_RELAY_ID): vol.All(cv.string, vol.Strip)})


class IntersvyazDataUpdateCoordinator(DataUpdateCoordinator[dict]):
    """Периодически обновляет камеры (с подписанными ссылками) и список реле.

    ВАЖНО: ссылки на HLS-потоки в поле MEDIA ответа API подписаны и имеют
    ограниченный срок жизни, поэтому их нужно регулярно перезапрашивать.

    Формат self.data:
        {
            "cameras": {uuid: <данные камеры>, ...},
            "relays": {relay_id: <данные реле>, ...},   # порядок как в API
        }
    """

    def __init__(self, hass: HomeAssistant, client: IntersvyazApiClient, update_interval: int) -> None:
        self.client = client
        self.group_ids: list[str] | None = None
        self._groups_fetched_at: datetime | None = None
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=update_interval),
        )

    @property
    def cameras(self) -> dict[str, dict]:
        return (self.data or {}).get("cameras", {})

    @property
    def relays(self) -> dict[str, dict]:
        return (self.data or {}).get("relays", {})

    async def _async_fetch_cameras(self) -> dict[str, dict]:
        now = dt_util.utcnow()
        if (
            self.group_ids is None
            or self._groups_fetched_at is None
            or now - self._groups_fetched_at > GROUPS_REFRESH_INTERVAL
        ):
            try:
                self.group_ids = await self.client.get_group_ids()
            except IntersvyazNoCamerasError:
                # Домофон без видео — это не ошибка, кнопки всё равно нужны.
                self.group_ids = []
            self._groups_fetched_at = now

        cameras = await self.client.get_cameras(self.group_ids)
        return {c["UUID"]: c for c in cameras}

    async def _async_fetch_relays(self) -> dict[str, dict]:
        try:
            relays = await self.client.get_relays()
        except IntersvyazAuthError:
            raise
        except IntersvyazApiError as err:
            # Реле меняются редко: при временной ошибке оставляем прежний
            # список, чтобы кнопки не становились недоступными из-за сбоя
            # одного запроса.
            _LOGGER.debug("Не удалось обновить список реле: %s", err)
            return self.relays
        return {r["RELAY_ID"]: r for r in relays}

    async def _async_update_data(self) -> dict[str, dict]:
        try:
            cameras = await self._async_fetch_cameras()
            relays = await self._async_fetch_relays()
        except IntersvyazAuthError as err:
            raise ConfigEntryAuthFailed("Токен недействителен, требуется повторный вход") from err
        except IntersvyazApiError as err:
            # Если список групп устарел (группу удалили), перезапросим его.
            self.group_ids = None
            raise UpdateFailed(str(err)) from err

        return {"cameras": cameras, "relays": relays}


def _iter_accounts(hass: HomeAssistant):
    for entry_data in hass.data.get(DOMAIN, {}).values():
        if isinstance(entry_data, dict):
            yield from entry_data.get("accounts", [])


async def _async_handle_open_door(call: ServiceCall) -> None:
    """intersvyaz.open_door [relay_id].

    Без relay_id открывается первое реле первого адреса (как раньше).
    С relay_id — конкретное реле любого из подключённых адресов.
    """
    hass = call.hass
    accounts = list(_iter_accounts(hass))
    if not accounts:
        raise HomeAssistantError("Интеграция Интерсвязь не настроена")

    relay_id = call.data.get(ATTR_RELAY_ID)
    try:
        if relay_id:
            for account in accounts:
                if relay_id in account["coordinator"].relays:
                    await account["client"].open_door(relay_id)
                    return
            raise ServiceValidationError(
                f"Реле {relay_id} не найдено ни у одного подключённого адреса"
            )
        client: IntersvyazApiClient = accounts[0]["client"]
        await client.open_door(await client.get_relay_id())
    except IntersvyazApiError as err:
        raise HomeAssistantError(f"Не удалось открыть домофон: {err}") from err


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Настройка интеграции (YAML не поддерживается).

    Сервис регистрируется один раз здесь, а не в async_setup_entry: раньше
    он замыкал клиента первой записи и после её выгрузки/перезагрузки
    продолжал использовать устаревший объект (или пропадал совсем).
    """
    hass.data.setdefault(DOMAIN, {})
    hass.services.async_register(
        DOMAIN, SERVICE_OPEN_DOOR, _async_handle_open_door, schema=OPEN_DOOR_SCHEMA
    )
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

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Перезагрузка записи при изменении опций (например, интервала обновления)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Выгрузка записи конфигурации."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok

"""Button platform for Intersvyaz Domofon integration."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import IntersvyazApiClient, IntersvyazApiError, IntersvyazAuthError
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Сохранить прежние кнопки адресов и обнаруживать все отдельные двери."""
    accounts: list[dict] = hass.data[DOMAIN][entry.entry_id]["accounts"]

    buttons = [
        DomofonButton(
            account["client"],
            account["address"],
            account["device_identifier"],
            f"domofon_button_{entry.entry_id}{account['unique_suffix']}",
        )
        for account in accounts
    ]
    async_add_entities(buttons)

    for account in accounts:
        coordinator = IntersvyazRelaysCoordinator(
            hass, entry, account["client"],
            entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )
        # Ошибка списка дверей не должна блокировать камеры и прежнюю кнопку.
        await coordinator.async_refresh()
        known_ids: set[str] = set()

        @callback
        def discover_relays(
            coordinator=coordinator, account=account, known_ids=known_ids,
        ) -> None:
            if not coordinator.last_update_success:
                return
            entities = []
            for relay_id, relay in (coordinator.data or {}).items():
                if relay_id in known_ids:
                    continue
                known_ids.add(relay_id)
                entities.append(DomofonRelayButton(
                    coordinator, relay_id, relay,
                    account["address"], account["device_identifier"],
                    f"domofon_button_{entry.entry_id}{account['unique_suffix']}_relay_{relay_id}",
                ))
            if entities:
                async_add_entities(entities)

        # Listener поддерживает опрос и обнаружение, даже если пока нет дверей.
        entry.async_on_unload(coordinator.async_add_listener(discover_relays))
        discover_relays()


class IntersvyazRelaysCoordinator(DataUpdateCoordinator[dict[str, dict]]):
    """Один запрос списка реле на адрес, независимо от количества кнопок."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry,
        client: IntersvyazApiClient, scan_interval: int,
    ) -> None:
        self.client = client
        super().__init__(
            hass, _LOGGER, config_entry=entry, name=f"{DOMAIN} relays",
            update_interval=timedelta(seconds=scan_interval),
            always_update=False,
        )

    async def _async_update_data(self) -> dict[str, dict]:
        try:
            relays = await self.client.get_relays()
        except IntersvyazAuthError as err:
            raise ConfigEntryAuthFailed("Токен недействителен, требуется повторный вход") from err
        except IntersvyazApiError as err:
            raise UpdateFailed(str(err)) from err
        return {relay["RELAY_ID"]: relay for relay in relays}


def _relay_name(relay: dict, relay_id: str) -> str:
    """Описание различает калитки одного типа; ID — последний fallback."""
    for key in ("RELAY_DESCR", "RELAY_TYPE", "ADDRESS"):
        name = relay.get(key)
        if isinstance(name, str) and name.strip():
            return name.strip()
    return f"Домофон {relay_id}"


class DomofonRelayButton(CoordinatorEntity, ButtonEntity):
    """Кнопка конкретного реле, без переключения на первую дверь при ошибке."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:door-open"

    def __init__(
        self, coordinator: IntersvyazRelaysCoordinator, relay_id: str, relay: dict,
        address: str, device_identifier: tuple[str, str], unique_id: str,
    ) -> None:
        super().__init__(coordinator, context=relay_id)
        self._relay_id = relay_id
        self._attr_name = f"Открыть: {_relay_name(relay, relay_id)}"
        self._attr_unique_id = unique_id
        self._attr_device_info = DeviceInfo(
            identifiers={device_identifier}, name=address,
            manufacturer="Интерсвязь", model="Домофон IS74",
        )
        # Не публикуем подписанные IMAGE_URL, QR salt и весь ответ API в state.
        self._attr_extra_state_attributes = {"relay_id": relay_id}

    @property
    def available(self) -> bool:
        return super().available and self._relay_id in (self.coordinator.data or {})

    @callback
    def _handle_coordinator_update(self) -> None:
        relay = (self.coordinator.data or {}).get(self._relay_id)
        if relay is not None:
            self._attr_name = f"Открыть: {_relay_name(relay, self._relay_id)}"
        super()._handle_coordinator_update()

    async def async_press(self) -> None:
        # Подтвердить наличие выбранной двери в актуальном списке, прежде чем POST.
        await self.coordinator.async_refresh()
        if not self.available:
            raise HomeAssistantError("Выбранный домофон недоступен")
        try:
            await self.coordinator.client.open_door(self._relay_id)
        except IntersvyazApiError as err:
            # Не повторять потенциально уже выполненное открытие после тайм-аута.
            raise HomeAssistantError("Не удалось открыть выбранный домофон") from err


class DomofonButton(ButtonEntity):
    """Кнопка открытия домофона."""

    _attr_has_entity_name = True

    def __init__(
        self,
        client: IntersvyazApiClient,
        address: str,
        device_identifier: tuple[str, str],
        unique_id: str,
    ) -> None:
        self._client = client
        self._relay_id: str | None = None
        self._attr_name = "Открыть"
        self._attr_unique_id = unique_id
        self._attr_device_info = DeviceInfo(
            identifiers={device_identifier},
            name=address,
            manufacturer="Интерсвязь",
            model="Домофон IS74",
        )

    async def async_press(self) -> None:
        try:
            if not self._relay_id:
                self._relay_id = await self._client.get_relay_id()
            try:
                await self._client.open_door(self._relay_id)
            except IntersvyazApiError:
                self._relay_id = await self._client.get_relay_id()
                await self._client.open_door(self._relay_id)
        except IntersvyazApiError as err:
            _LOGGER.error("Не удалось открыть домофон: %s", err)
            raise HomeAssistantError(f"Не удалось открыть домофон: {err}") from err
        else:
            _LOGGER.info("Дверь успешно открыта")

"""Button platform for Intersvyaz Domofon integration.

На каждый адрес создаются:
  * по одной кнопке на каждое реле из /domofon/relays (главный вход,
    запасной вход, калитки, ворота…);
  * старая общая кнопка «Открыть» — для совместимости с уже настроенными
    автоматизациями и карточками; она открывает первое реле, как раньше.

Реле, появившиеся у адреса позже (например, поставили новую калитку),
добавляются автоматически при очередном обновлении координатора.
"""
from __future__ import annotations

import logging
import re
from collections import Counter

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import IntersvyazDataUpdateCoordinator
from .api import IntersvyazApiClient, IntersvyazApiError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Поля ответа /domofon/relays, в которых может лежать человекочитаемое
# название реле. Формат ответа не документирован, поэтому перебираем
# несколько вариантов по порядку.
_RELAY_NAME_KEYS = ("RELAY_DESCR", "DESCRIPTION", "RELAY_NAME", "NAME", "TITLE")

# Ошибки HTTP, при которых имеет смысл перезапросить список реле и
# повторить попытку (реле переименовали/заменили). При сетевых ошибках и
# таймаутах повтор не делаем: команда могла дойти, и дверь открылась бы дважды.
_RETRY_STATUSES = {400, 404, 410}


def _device_info(account: dict) -> DeviceInfo:
    return DeviceInfo(
        identifiers={account["device_identifier"]},
        name=account["address"],
        manufacturer="Интерсвязь",
        model="Домофон IS74",
    )


def relay_display_name(relay: dict, index: int) -> str:
    """Название реле для имени кнопки (без персональных данных)."""
    for key in _RELAY_NAME_KEYS:
        value = relay.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    porch = relay.get("PORCH_NUM") or relay.get("ENTRANCE")
    if porch not in (None, "", 0, "0"):
        return f"Подъезд {porch}"
    return f"Реле {index}"


# Поля, в которых может лежать адрес дома, где стоит реле. Формат ответа не
# документирован, поэтому перебираем варианты по порядку.
_RELAY_ADDRESS_KEYS = (
    "ADDRESS",
    "FULL_ADDRESS",
    "ADDRESS_FULL",
    "BUILDING_ADDRESS",
    "HOUSE_ADDRESS",
    "ADDR",
)

_APARTMENT_RE = re.compile(r"[,.\s]+(кв|квартира|офис|оф|пом)\b.*$", re.IGNORECASE)


def _norm(text: str) -> str:
    """Нормализация адреса для сравнения: регистр, ё, пунктуация, квартира."""
    text = _APARTMENT_RE.sub("", text.lower().replace("ё", "е"))
    return " ".join(re.sub(r"[^0-9a-zа-я]+", " ", text).split())


def _same_building(a: str, b: str) -> bool:
    """Адреса указывают на один дом (один целиком входит в другой по словам)."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    return f" {na} " in f" {nb} " or f" {nb} " in f" {na} "


def relay_address(relay: dict) -> str | None:
    for key in _RELAY_ADDRESS_KEYS:
        value = relay.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().rstrip(".,")
    return None


def _short_address(address: str, home: str | None) -> str:
    """Убирает из адреса город, если он совпадает с городом основного адреса.

    "Челябинск, ул. Лесопарковая, д. 9" -> "ул. Лесопарковая, д. 9"
    """
    parts = [p.strip() for p in address.split(",") if p.strip()]
    if len(parts) > 1 and home:
        city = _norm(parts[0])
        city = city[2:] if city.startswith("г ") else city
        if city and f" {city} " in f" {_norm(home)} ":
            parts = parts[1:]
    return ", ".join(parts)


def _home_address(relays: dict[str, dict], account_address: str | None) -> str | None:
    """Адрес «своего» дома, относительно которого остальные реле считаются чужими.

    Берётся адрес реле, совпадающий с адресом подключения (авторизация по
    телефону). Если такого нет (например, при входе по логину название
    записи — это логин, а не адрес), — самый частый адрес среди реле.
    """
    addresses = [a for a in (relay_address(r) for r in relays.values()) if a]
    if not addresses:
        return None
    if account_address:
        for addr in addresses:
            if _same_building(addr, account_address):
                return addr
    return Counter(addresses).most_common(1)[0][0]


def relay_names(
    relays: dict[str, dict], account_address: str | None = None
) -> dict[str, str]:
    """Уникальные в пределах адреса названия всех реле.

    Реле в других домах (доступ к соседнему подъезду, калитке и т.п.)
    получают в названии адрес своего дома:
    "ул. Лесопарковая, д. 9, Подъезд №1" вместо просто "Подъезд №1".
    """
    home = _home_address(relays, account_address)
    names: dict[str, str] = {}
    used: dict[str, int] = {}
    for index, (relay_id, relay) in enumerate(relays.items(), start=1):
        base = relay_display_name(relay, index)
        addr = relay_address(relay)
        if addr and home and not _same_building(addr, home):
            short = _short_address(addr, home)
            if short and _norm(short) not in _norm(base):
                base = f"{short}, {base}"
        used[base] = used.get(base, 0) + 1
        names[relay_id] = base if used[base] == 1 else f"{base} ({used[base]})"
    return names


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Настройка кнопок в Home Assistant."""
    accounts: list[dict] = hass.data[DOMAIN][entry.entry_id]["accounts"]

    entities: list[ButtonEntity] = []
    for account in accounts:
        coordinator: IntersvyazDataUpdateCoordinator = account["coordinator"]
        base_unique_id = f"domofon_button_{entry.entry_id}{account['unique_suffix']}"

        # Старая общая кнопка: unique_id не меняется, чтобы не ломать
        # существующие сущности, автоматизации и карточки.
        entities.append(
            DomofonButton(account["client"], _device_info(account), base_unique_id)
        )

        known: set[str] = set()

        @callback
        def _add_new_relays(
            _account: dict = account,
            _coordinator: IntersvyazDataUpdateCoordinator = coordinator,
            _known: set[str] = known,
            _base: str = base_unique_id,
            _initial: list | None = None,
        ) -> list[ButtonEntity]:
            names = relay_names(_coordinator.relays, _account["address"])
            new = [
                RelayButton(
                    _coordinator,
                    _account["client"],
                    _device_info(_account),
                    f"{_base}_relay_{relay_id}",
                    relay_id,
                    names[relay_id],
                )
                for relay_id in _coordinator.relays
                if relay_id not in _known
            ]
            _known.update(b.relay_id for b in new)
            if _initial is not None:
                _initial.extend(new)
            elif new:
                async_add_entities(new)
            return new

        _add_new_relays(_initial=entities)
        entry.async_on_unload(coordinator.async_add_listener(_add_new_relays))

    async_add_entities(entities)


class DomofonButton(ButtonEntity):
    """Общая кнопка «Открыть» (первое реле адреса) — для совместимости."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:door-open"

    def __init__(
        self, client: IntersvyazApiClient, device_info: DeviceInfo, unique_id: str
    ) -> None:
        self._client = client
        self._relay_id: str | None = None
        self._attr_name = "Открыть"
        self._attr_unique_id = unique_id
        self._attr_device_info = device_info

    async def async_press(self) -> None:
        try:
            if not self._relay_id:
                self._relay_id = await self._client.get_relay_id()
            try:
                await self._client.open_door(self._relay_id)
            except IntersvyazApiError as err:
                if err.status not in _RETRY_STATUSES:
                    raise
                self._relay_id = await self._client.get_relay_id()
                await self._client.open_door(self._relay_id)
        except IntersvyazApiError as err:
            raise HomeAssistantError(f"Не удалось открыть домофон: {err}") from err
        _LOGGER.info("Дверь успешно открыта")


class RelayButton(CoordinatorEntity[IntersvyazDataUpdateCoordinator], ButtonEntity):
    """Кнопка открытия конкретного реле (вход, калитка, ворота)."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:door-open"

    def __init__(
        self,
        coordinator: IntersvyazDataUpdateCoordinator,
        client: IntersvyazApiClient,
        device_info: DeviceInfo,
        unique_id: str,
        relay_id: str,
        relay_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._client = client
        self.relay_id = relay_id
        self._attr_name = f"Открыть: {relay_name}"
        self._attr_unique_id = unique_id
        self._attr_device_info = device_info

    @property
    def available(self) -> bool:
        """Недоступна, если реле пропало из ответа API."""
        return self.relay_id in self.coordinator.relays

    @property
    def extra_state_attributes(self) -> dict:
        # Только идентификатор: он нужен для сервиса intersvyaz.open_door.
        # Адрес и URL снимков из ответа API сюда намеренно не попадают.
        return {"relay_id": self.relay_id}

    async def async_press(self) -> None:
        try:
            await self._client.open_door(self.relay_id)
        except IntersvyazApiError as err:
            raise HomeAssistantError(
                f"Не удалось открыть «{self.name}»: {err}"
            ) from err
        _LOGGER.info("Реле %s открыто", self.relay_id)

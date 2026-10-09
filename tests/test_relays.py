"""Регрессии для всех дверей; только вымышленные данные и mocked API."""
import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.intersvyaz.api import (
    BASE_URL, IntersvyazApiClient, IntersvyazApiError, IntersvyazAuthError,
)
from custom_components.intersvyaz.button import (
    DomofonButton, DomofonRelayButton, IntersvyazRelaysCoordinator,
    _relay_name, async_setup_entry,
)
from custom_components.intersvyaz.const import DOMAIN


def relays():
    return [
        {"RELAY_ID": "101", "RELAY_TYPE": "Главный вход", "RELAY_DESCR": None},
        {"RELAY_ID": "102", "RELAY_TYPE": "Запасной вход", "RELAY_DESCR": "Запасной вход"},
        {"RELAY_ID": "103", "RELAY_TYPE": "Калитка во двор", "RELAY_DESCR": "Калитка А"},
        {"RELAY_ID": "104", "RELAY_TYPE": "Калитка во двор", "RELAY_DESCR": "Калитка Б"},
        {"RELAY_ID": "105", "RELAY_TYPE": "Калитка во двор", "RELAY_DESCR": "Калитка В"},
    ]


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = IntersvyazApiClient(Mock())
        self.client._request = AsyncMock()

    async def test_all_five_relays_and_authenticated_get(self):
        self.client._request.return_value = relays()
        self.assertEqual(await self.client.get_relays(), relays())
        self.client._request.assert_awaited_once_with("GET", f"{BASE_URL}/domofon/relays", auth=True)

    async def test_normalize_deduplicate_and_skip_malformed_records(self):
        self.client._request.return_value = [
            {"RELAY_ID": 101}, {"RELAY_ID": " 101 "}, None, [], {},
            {"RELAY_ID": False}, {"RELAY_ID": ""}, {"RELAY_ID": 1.5},
            {"RELAY_ID": "../open"}, {"RELAY_ID": "١٠٢"}, {"RELAY_ID": "102"},
        ]
        self.assertEqual(await self.client.get_relays(), [{"RELAY_ID": "101"}, {"RELAY_ID": "102"}])

    async def test_empty_list_and_invalid_response(self):
        self.client._request.return_value = []
        self.assertEqual(await self.client.get_relays(), [])
        for data in (None, {}, "not a list"):
            self.client._request.return_value = data
            with self.assertRaises(IntersvyazApiError):
                await self.client.get_relays()

    async def test_legacy_first_relay_remains_compatible(self):
        self.client._request.return_value = relays()
        self.assertEqual(await self.client.get_relay_id(), "101")

    async def test_exact_open_endpoint_is_preserved(self):
        await self.client.open_door("104")
        self.client._request.assert_awaited_once_with(
            "POST", f"{BASE_URL}/domofon/relays/104/open?from=app",
            auth=True, headers={"Content-Type": "application/json"},
        )


class ButtonTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass = HomeAssistant("/tmp/intersvyaz-unit-tests")
        self.unload_callbacks = []
        self.entry = SimpleNamespace(
            entry_id="test_entry", options={}, pref_disable_polling=False,
            async_on_unload=self.unload_callbacks.append, async_start_reauth=Mock(),
        )
        self.client = SimpleNamespace(
            get_relays=AsyncMock(return_value=relays()),
            get_relay_id=AsyncMock(return_value="101"), open_door=AsyncMock(),
        )
        self.account = {
            "client": self.client, "address": "Тестовый адрес А",
            "device_identifier": (DOMAIN, "test_entry"), "unique_suffix": "",
        }
        self.hass.data[DOMAIN] = {self.entry.entry_id: {"accounts": [self.account]}}
        self.buttons = []
        self.coordinators = []

    async def asyncTearDown(self):
        for unload in reversed(self.unload_callbacks):
            result = unload()
            if inspect.isawaitable(result):
                await result
        await self.hass.async_block_till_done()

    async def setup_buttons(self):
        def create_coordinator(*args):
            coordinator = IntersvyazRelaysCoordinator(*args)
            self.coordinators.append(coordinator)
            return coordinator

        with patch("custom_components.intersvyaz.button.IntersvyazRelaysCoordinator", side_effect=create_coordinator):
            await async_setup_entry(self.hass, self.entry, self.buttons.extend)
        return [b for b in self.buttons if isinstance(b, DomofonRelayButton)]

    async def test_create_five_targeted_buttons_and_preserve_legacy_identity(self):
        specific = await self.setup_buttons()
        self.assertEqual(len(specific), 5)
        legacy = next(b for b in self.buttons if isinstance(b, DomofonButton))
        self.assertEqual(legacy.unique_id, "domofon_button_test_entry")
        self.assertEqual(legacy.name, "Открыть")
        self.assertEqual(len({b.unique_id for b in specific}), 5)
        self.assertEqual(len({b.name for b in specific}), 5)
        self.assertEqual(specific[2].name, "Открыть: Калитка А")
        self.client.get_relays.assert_awaited_once()

    async def test_press_only_selected_relay_after_reordering(self):
        specific = await self.setup_buttons()
        self.client.get_relays.return_value = list(reversed(relays()))
        await specific[3].async_press()
        self.client.open_door.assert_awaited_once_with("104")
        self.client.get_relay_id.assert_not_awaited()
        self.assertEqual(len(self.buttons), 6)

    async def test_open_failure_never_retries_or_falls_back(self):
        specific = await self.setup_buttons()
        self.client.open_door.side_effect = IntersvyazApiError("timeout")
        with self.assertRaises(HomeAssistantError):
            await specific[3].async_press()
        self.client.open_door.assert_awaited_once_with("104")
        self.client.get_relay_id.assert_not_awaited()

    async def test_missing_relay_is_unavailable_and_cannot_open_another(self):
        specific = await self.setup_buttons()
        self.client.get_relays.return_value = relays()[:2]
        with self.assertRaises(HomeAssistantError):
            await specific[3].async_press()
        self.assertFalse(specific[3].available)
        self.client.open_door.assert_not_awaited()
        self.client.get_relay_id.assert_not_awaited()

    async def test_failed_refresh_does_not_use_cached_permission(self):
        specific = await self.setup_buttons()
        self.client.get_relays.side_effect = IntersvyazApiError("network failure")
        with self.assertRaises(HomeAssistantError):
            await specific[3].async_press()
        self.client.open_door.assert_not_awaited()

    async def test_discover_new_relays_without_duplicates_and_restore_old(self):
        self.client.get_relays.return_value = relays()[:1]
        specific = await self.setup_buttons()
        coordinator = specific[0].coordinator
        coordinator.async_set_updated_data({r["RELAY_ID"]: r for r in relays()})
        self.assertEqual(len(self.buttons), 6)
        coordinator.async_set_updated_data({r["RELAY_ID"]: r for r in reversed(relays())})
        self.assertEqual(len(self.buttons), 6)
        coordinator.async_set_updated_data({})
        self.assertFalse(specific[0].available)
        coordinator.async_set_updated_data({"101": relays()[0]})
        self.assertTrue(specific[0].available)
        self.assertEqual(len(self.buttons), 6)

    async def test_empty_initial_list_can_discover_doors_later(self):
        self.client.get_relays.return_value = []
        await self.setup_buttons()
        self.assertEqual(len(self.buttons), 1)
        # Discovery works even before any relay entities have subscribed.
        self.coordinators[0].async_set_updated_data({r["RELAY_ID"]: r for r in relays()})
        self.assertEqual(len(self.buttons), 6)

    async def test_initial_error_does_not_remove_legacy_button(self):
        self.client.get_relays.side_effect = IntersvyazApiError("temporary outage")
        specific = await self.setup_buttons()
        self.assertEqual(specific, [])
        self.assertEqual(len(self.buttons), 1)
        self.assertIsInstance(self.buttons[0], DomofonButton)
        self.coordinators[0].async_set_updated_data({r["RELAY_ID"]: r for r in relays()})
        self.assertEqual(len(self.buttons), 6)

    async def test_display_name_changes_without_changing_relay_identity(self):
        specific = await self.setup_buttons()
        button = specific[2]
        old_id = button.unique_id
        button.async_write_ha_state = Mock()
        button.coordinator.data["103"]["RELAY_DESCR"] = "Новое название калитки"
        button._handle_coordinator_update()
        self.assertEqual(button.name, "Открыть: Новое название калитки")
        self.assertEqual(button.unique_id, old_id)

    async def test_multiple_addresses_have_distinct_ids_and_clients(self):
        second_client = SimpleNamespace(get_relays=AsyncMock(return_value=relays()), open_door=AsyncMock())
        second = {**self.account, "client": second_client, "address": "Тестовый адрес Б",
                  "unique_suffix": "_second", "device_identifier": (DOMAIN, "test_entry_second")}
        self.hass.data[DOMAIN][self.entry.entry_id]["accounts"].append(second)
        specific = await self.setup_buttons()
        self.assertEqual(len(specific), 10)
        self.assertEqual(len({b.unique_id for b in self.buttons}), 12)
        second_client.get_relays.return_value = relays()[:1]
        await specific[5].async_press()
        second_client.open_door.assert_awaited_once_with("101")
        self.client.open_door.assert_not_awaited()
        # Both discovery callbacks retain their own account and known-ID set.
        specific[0].coordinator.async_set_updated_data({"106": {"RELAY_ID": "106"}})
        specific[5].coordinator.async_set_updated_data({"106": {"RELAY_ID": "106"}})
        self.assertEqual(len({b.unique_id for b in self.buttons}), 14)

    async def test_states_do_not_publish_private_api_payload(self):
        self.client.get_relays.return_value = [{**relays()[0], "IMAGE_URL": "private", "QR_OPTIONS": {"salt": "private"}}]
        specific = await self.setup_buttons()
        self.assertEqual(specific[0].extra_state_attributes, {"relay_id": "101"})

    async def test_coordinator_error_types(self):
        coordinator = IntersvyazRelaysCoordinator(self.hass, self.entry, self.client, 120)
        self.client.get_relays.side_effect = IntersvyazAuthError("expired")
        with self.assertRaises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()
        self.client.get_relays.side_effect = IntersvyazApiError("offline")
        with self.assertRaises(UpdateFailed):
            await coordinator._async_update_data()

    def test_label_fallbacks(self):
        self.assertEqual(_relay_name({"RELAY_DESCR": "  ", "RELAY_TYPE": "Вход"}, "101"), "Вход")
        self.assertEqual(_relay_name({"ADDRESS": "Тестовый адрес"}, "101"), "Тестовый адрес")
        self.assertEqual(_relay_name({}, "101"), "Домофон 101")


if __name__ == "__main__":
    unittest.main()

import logging
import aiohttp
import asyncio
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

DOMAIN = "intersvyaz"
_LOGGER = logging.getLogger(__name__)

CONF_USERNAME = "username"
CONF_PASSWORD = "password"
BASE_URL = "https://api.is74.ru"

class IntersvyazConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            session = aiohttp.ClientSession()
            token = await get_token(session, user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
            if token:
                return self.async_create_entry(title="Intersvyaz", data=user_input)
            else:
                errors["base"] = "auth_failed"
        
        return self.async_show_form(
            step_id="user", 
            data_schema=vol.Schema({
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
            }),
            errors=errors
        )

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    
    session = aiohttp.ClientSession()
    token = await get_token(session, username, password)
    if not token:
        _LOGGER.error("Failed to authenticate with Intersvyaz API")
        return False
    
    relay_id = await get_relay_id(session, token)
    if not relay_id:
        _LOGGER.error("Failed to retrieve relay ID")
        return False
    
    async def handle_open_door(call: ServiceCall):
        await open_door(session, token, relay_id)
    
    hass.services.async_register(DOMAIN, "open_door", handle_open_door)
    
    hass.data[DOMAIN] = {
        "session": session,
        "token": token,
        "relay_id": relay_id
    }
    
    return True

async def get_token(session, username, password):
    url = f"{BASE_URL}/auth/login"
    payload = {"login": username, "password": password}
    headers = {"Content-Type": "application/json"}
    async with session.post(url, json=payload, headers=headers) as resp:
        if resp.status == 200:
            data = await resp.json()
            return data.get("token")
    return None

async def get_relay_id(session, token):
    url = f"{BASE_URL}/domofon/relays"
    headers = {"Authorization": f"Bearer {token}"}
    async with session.get(url, headers=headers) as resp:
        if resp.status == 200:
            data = await resp.json()
            if "relays" in data and len(data["relays"]) > 0:
                return data["relays"][0]["id"]  # Берем первый реле
    return None

async def open_door(session, token, relay_id):
    url = f"{BASE_URL}/domofon/relays/{relay_id}/open?from=app"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    async with session.post(url, headers=headers) as resp:
        if resp.status == 200:
            _LOGGER.info("Door opened successfully")
        else:
            _LOGGER.error("Failed to open door")

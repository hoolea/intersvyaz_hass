"""Config flow for Intersvyaz Domofon integration."""
from typing import Any, Dict, Optional
import logging
import aiohttp
import asyncio
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
import uuid

from .const import (
    DOMAIN,
    BASE_URL,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_PHONE,
    CONF_AUTH_METHOD,
    CONF_SMS_CODE,
    CONF_ADDRESS,
    CONF_DEVICE_ID,
    CONF_AUTH_ID,
    CONF_USER_ID,
    AUTH_METHOD_LOGIN,
    AUTH_METHOD_PHONE,
)
from . import get_token, get_token_by_phone

_LOGGER = logging.getLogger(__name__)

class DomofonConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Intersvyaz Domofon."""

    VERSION = 1
    
    def __init__(self):
        """Initialize flow."""
        self.auth_method = None
        self.phone_data = {}

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None):
        """Выбор метода авторизации."""
        if user_input is not None:
            self.auth_method = user_input[CONF_AUTH_METHOD]
            if self.auth_method == AUTH_METHOD_LOGIN:
                return await self.async_step_login()
            return await self.async_step_phone_number()

        auth_methods = {
            AUTH_METHOD_LOGIN: "Логин",
            AUTH_METHOD_PHONE: "Номер телефона"
        }

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_AUTH_METHOD): vol.In(auth_methods)
            })
        )

    async def async_step_login(self, user_input: Optional[Dict[str, Any]] = None):
        """Авторизация по логину и паролю."""
        errors = {}
        if user_input is not None:
            try:
                async with aiohttp.ClientSession() as session:
                    _LOGGER.debug(f"Попытка авторизации для пользователя: {user_input[CONF_USERNAME]}")
                    token = await get_token(
                        session,
                        user_input[CONF_USERNAME],
                        user_input[CONF_PASSWORD]
                    )
                    _LOGGER.debug(f"Результат получения токена: {token}")
                    
                    if token:
                        return self.async_create_entry(
                            title=user_input[CONF_USERNAME],
                            data={
                                CONF_USERNAME: user_input[CONF_USERNAME],
                                CONF_PASSWORD: user_input[CONF_PASSWORD],
                                "token": token
                            }
                        )
                    errors["base"] = "auth_error"
            except aiohttp.ClientError as e:
                _LOGGER.error(f"Ошибка сети при авторизации: {e}")
                errors["base"] = "cannot_connect"
            except Exception as e:
                _LOGGER.error(f"Неожиданная ошибка при авторизации: {e}")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="login",
            data_schema=vol.Schema({
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
            }),
            errors=errors
        )

    async def async_step_phone_number(self, user_input: Optional[Dict[str, Any]] = None):
        """Ввод номера телефона."""
        errors = {}
        if user_input is not None:
            # Сохраняем номер телефона и генерируем device_id в любом случае
            self.phone_data = {
                CONF_PHONE: user_input[CONF_PHONE],
                CONF_DEVICE_ID: str(uuid.uuid4()).replace("-", "")
            }
            
            async with aiohttp.ClientSession() as session:
                result = await get_token_by_phone(session, user_input[CONF_PHONE])
                if "error" not in result:
                    self.phone_data[CONF_DEVICE_ID] = result["device_id"]
                    return await self.async_step_sms_code()
                
                # При любой ошибке отправки СМС предлагаем ввести старый код
                error_message = result.get("message", "")
                _LOGGER.debug(f"Ошибка отправки СМС: {error_message}")
                
                if "limit" in str(error_message).lower():
                    description = f"\n\n{error_message}"
                else:
                    description = "\n\nВы можете ввести код из предыдущего СМС"
                
                return await self.async_step_sms_code(error_message=description)

        return self.async_show_form(
            step_id="phone_number",
            data_schema=vol.Schema({
                vol.Required(CONF_PHONE): str,
            }),
            description_placeholders={
                "format": "+79991234567"
            },
            errors=errors
        )

    async def async_step_sms_code(self, user_input: Optional[Dict[str, Any]] = None, error_message: Optional[str] = None):
        """Ввод SMS кода."""
        errors = {}
        description_placeholders = {
            "error_message": error_message if error_message else ""
        }
        
        if user_input is not None:
            async with aiohttp.ClientSession() as session:
                result = await get_token_by_phone(
                    session,
                    self.phone_data[CONF_PHONE],
                    code=user_input[CONF_SMS_CODE],
                    device_id=self.phone_data[CONF_DEVICE_ID]
                )
                if "error" not in result:
                    self.phone_data.update({
                        CONF_AUTH_ID: result["auth_id"],
                        "addresses": result["addresses"]
                    })
                    return await self.async_step_address_select()
                errors["base"] = "invalid_code"

        return self.async_show_form(
            step_id="sms_code",
            data_schema=vol.Schema({
                vol.Required(CONF_SMS_CODE): str,
            }),
            description_placeholders=description_placeholders,
            errors=errors
        )

    async def async_step_address_select(self, user_input: Optional[Dict[str, Any]] = None):
        """Выбор адреса."""
        errors = {}
        if user_input is not None:
            try:
                _LOGGER.debug(f"Все сохраненные данные: {self.phone_data}")
                _LOGGER.debug(f"Выбранный адрес: {user_input}")
                
                selected_address = next(
                    addr for addr in self.phone_data["addresses"]
                    if addr["ADDRESS"] == user_input[CONF_ADDRESS]
                )
                
                _LOGGER.debug(f"Данные выбранного адреса: {selected_address}")
                _LOGGER.debug(f"AUTH_ID: {self.phone_data.get(CONF_AUTH_ID)}")
                _LOGGER.debug(f"USER_ID: {selected_address.get('USER_ID')}")
                
                async with aiohttp.ClientSession() as session:
                    # Сразу пытаемся получить токен без отправки SMS
                    result = await get_token_by_phone(
                        session,
                        phone=self.phone_data[CONF_PHONE],
                        auth_id=self.phone_data[CONF_AUTH_ID],
                        user_id=selected_address["USER_ID"],
                        skip_sms=True  # Добавляем флаг пропуска проверки SMS
                    )
                    _LOGGER.debug(f"Результат получения токена: {result}")
                    
                    if "error" not in result and result.get("token"):
                        _LOGGER.debug("Токен успешно получен, создаем entry")
                        return self.async_create_entry(
                            title=user_input[CONF_ADDRESS],
                            data={
                                CONF_PHONE: self.phone_data[CONF_PHONE],
                                "token": result["token"],
                                CONF_ADDRESS: user_input[CONF_ADDRESS],
                                CONF_USER_ID: selected_address["USER_ID"]
                            }
                        )
                    
                    _LOGGER.error(f"Ошибка получения токена: {result}")
                    errors["base"] = "token_error"
                        
            except Exception as e:
                _LOGGER.exception(f"Неожиданная ошибка при получении токена: {e}")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="address_select",
            data_schema=vol.Schema({
                vol.Required(CONF_ADDRESS): vol.In(
                    [addr["ADDRESS"] for addr in self.phone_data.get("addresses", [])]
                ),
            }),
            errors=errors
        )

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Настройка интеграции при добавлении записи."""
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    
    async with aiohttp.ClientSession() as session:
        token = await get_token(session, username, password)
        if not token:
            _LOGGER.error("Не удалось аутентифицироваться в API Intersvyaz")
            return False
        
        relay_id = await get_relay_id(session, token)
        if not relay_id:
            _LOGGER.error("Не удалось получить ID реле")
            return False
        
        async def handle_open_door(call: ServiceCall):
            """Обработчик сервиса открытия двери."""
            await open_door(session, token, relay_id)
        
        hass.services.async_register(DOMAIN, "open_door", handle_open_door)
        
        hass.data[DOMAIN] = {
            "session": session,
            "token": token,
            "relay_id": relay_id
        }
    
    return True

async def get_token(session: aiohttp.ClientSession, username: str, password: str) -> str | None:
    """Получение токена авторизации."""
    url = f"{BASE_URL}/auth/mobile"
    payload = {"username": username, "password": password}
    headers = {"Content-Type": "application/json"}
    
    try:
        _LOGGER.debug(f"Отправка запроса авторизации для пользователя: {username}")
        async with session.post(url, json=payload, headers=headers) as resp:
            response_text = await resp.text()
            _LOGGER.debug(f"Ответ сервера: {response_text}")
            
            if resp.status == 200:
                try:
                    data = await resp.json()
                    if "TOKEN" in data:
                        return data["TOKEN"]
                    _LOGGER.error("Токен отсутствует в ответе")
                except ValueError as e:
                    _LOGGER.error(f"Ошибка разбора JSON ответа: {e}")
            else:
                _LOGGER.error(f"Ошибка авторизации. Статус: {resp.status}")
    except aiohttp.ClientError as e:
        _LOGGER.error(f"Ошибка сети при авторизации: {e}")
    except Exception as e:
        _LOGGER.error(f"Неожиданная ошибка при авторизации: {e}")
    
    return None

async def get_relay_id(session: aiohttp.ClientSession, token: str) -> str | None:
    """Получение ID реле."""
    url = f"{BASE_URL}/domofon/relays"
    headers = {"Authorization": f"Bearer {token}"}
    async with session.get(url, headers=headers) as resp:
        if resp.status == 200:
            data = await resp.json()
            if "relays" in data and len(data["relays"]) > 0:
                return data["relays"][0]["id"]  # Берем первый реле
    return None

async def open_door(session: aiohttp.ClientSession, token: str, relay_id: str) -> None:
    """Открытие двери через API."""
    url = f"{BASE_URL}/domofon/relays/{relay_id}/open?from=app"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    async with session.post(url, headers=headers) as resp:
        if resp.status == 200:
            _LOGGER.info("Дверь успешно открыта")
        else:
            _LOGGER.error("Ошибка открытия двери")

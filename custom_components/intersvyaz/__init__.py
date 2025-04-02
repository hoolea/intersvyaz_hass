import logging
import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType
import uuid
from typing import Optional
from homeassistant.const import Platform
import homeassistant.helpers.config_validation as cv

# Определение домена интеграции и базовых URL
DOMAIN = "intersvyaz"
BASE_URL = "https://api.is74.ru"
BASE_URL_CAM = "https://cams.is74.ru"

# Логгер для отладки
_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CAMERA, Platform.BUTTON]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Настройка интеграции."""
    hass.data.setdefault(DOMAIN, {})
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Настройка интеграции для камеры и кнопки."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = entry.data

    # Настройка камеры и кнопки
    await hass.config_entries.async_forward_entry_setups(entry, ["camera", "button"])
    
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Разгрузка конфигурационной записи."""
    unload_ok = await hass.config_entries.async_forward_entry_unload(entry, "camera")
    unload_ok_button = await hass.config_entries.async_forward_entry_unload(entry, "button")
    
    if unload_ok and unload_ok_button:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok and unload_ok_button

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
        data = await resp.json()
        _LOGGER.info(f"Ответ API на relays: {data}")
        
        if isinstance(data, list) and data:
            return data[0].get("RELAY_ID")
        _LOGGER.error("API не вернул список реле!")
    return None

async def get_group_id(session: aiohttp.ClientSession, token: str) -> str | None:
    """Получение ID группы с названием, начинающимся на 'Умный двор', или 'Свои камеры' через дополнительный запрос."""
    # Первый запрос: ищем "Умный двор" по основному URL
    url = f"{BASE_URL_CAM}/api/get-group/"
    headers = {"Authorization": f"Bearer {token}"}
    
    async with session.get(url, headers=headers) as resp:
        data = await resp.json()
        _LOGGER.info(f"Ответ API на group (основной URL): {data}")
        
        # Проверяем наличие "Умный двор"
        for group in data:
            if group.get("NAME", "").startswith("Умный двор"):
                _LOGGER.info(f"Найдена группа 'Умный двор' с ID: {group.get('ID')}")
                return group.get("ID")
        
        _LOGGER.info("Группа 'Умный двор' не найдена в основном запросе, проверяем 'Свои камеры'")

    # Второй запрос: ищем "Свои камеры" с параметром ?selfCams=true
    url_self_cams = f"{BASE_URL_CAM}/api/get-group/?selfCams=true"
    async with session.get(url_self_cams, headers=headers) as resp:
        data = await resp.json()
        _LOGGER.info(f"Ответ API на group (с ?selfCams=true): {data}")
        
        # Проверяем наличие "Свои камеры"
        for group in data:
            if group.get("NAME", "") == "Свои камеры":
                _LOGGER.info(f"Найдена группа 'Свои камеры' с ID: {group.get('ID')}")
                return group.get("ID")
        
        _LOGGER.error("Не найдены ни группа 'Умный двор', ни 'Свои камеры'!")
    return None

async def get_uuid_cam(session: aiohttp.ClientSession, token: str, group_id: str) -> list[str]:
    """Получение списка UUID камер."""
    url = f"{BASE_URL_CAM}/api/get-group/{group_id}"
    headers = {"Authorization": f"Bearer {token}"}
    
    async with session.get(url, headers=headers) as resp:
        data = await resp.json()
        _LOGGER.info(f"Ответ API на камеры: {data}")
        
        if isinstance(data, list) and data:
            return [camera["UUID"] for camera in data if "UUID" in camera]
        _LOGGER.error("API не вернул список UUID камер!")
    return []

async def open_door(session: aiohttp.ClientSession, token: str, relay_id: str) -> None:
    """Открытие домофона."""
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

async def get_token_by_phone(
    session: aiohttp.ClientSession, 
    phone: str,
    code: Optional[str] = None,
    device_id: Optional[str] = None,
    auth_id: Optional[str] = None,
    user_id: Optional[str] = None,
    skip_sms: bool = False
) -> dict:
    """Авторизация по номеру телефона."""
    clean_phone = phone.replace("+7", "")
    
    if auth_id and user_id and skip_sms:
        # Получение токена для выбранного адреса
        url = f"{BASE_URL}/mobile/auth/get-token"
        headers = {"Content-Type": "application/json"}
        payload = {
            "authId": str(auth_id).strip(),
            "userId": str(user_id).strip()
        }
        
        _LOGGER.debug(f"Отправка запроса на получение токена: {payload}")
        
        try:
            async with session.post(url, json=payload, headers=headers) as resp:
                response_text = await resp.text()
                _LOGGER.debug(f"Ответ сервера при получении токена: {response_text}")
                
                if resp.status == 200:
                    data = await resp.json()
                    if "TOKEN" in data:
                        return {"token": data["TOKEN"]}
                
                _LOGGER.error(f"Ошибка получения токена. Статус: {resp.status}, Ответ: {response_text}")
                return {"error": "token_error"}
                
        except Exception as e:
            _LOGGER.error(f"Ошибка при получении токена: {e}")
            return {"error": "token_error"}
    
    if not code:
        # Первый шаг - отправка СМС
        device_id = str(uuid.uuid4()).replace("-", "")
        url = f"{BASE_URL}/mobile/auth/send-sms"
        payload = {
            "phone": clean_phone,
            "uniqueDeviceId": device_id
        }
        
        _LOGGER.debug(f"Отправка запроса на SMS: {payload}")
        async with session.post(url, json=payload) as resp:
            response_text = await resp.text()
            _LOGGER.debug(f"Ответ сервера: {response_text}")
            
            try:
                data = await resp.json()
                if resp.status != 200:
                    error_message = data.get("message", "") if isinstance(data, dict) else str(data)
                    return {"error": "sms_error", "message": error_message}
                        
                return {"device_id": device_id}
            except Exception as e:
                _LOGGER.error(f"Ошибка разбора ответа: {e}")
                return {"error": "parse_error"}
            
    elif code and device_id and not auth_id:
        # Второй шаг - подтверждение кода
        url = f"{BASE_URL}/mobile/auth/confirm"
        payload = {
            "confirmCode": code,
            "phone": clean_phone,
            "uniqueDeviceId": device_id
        }
        
        _LOGGER.debug(f"Отправка запроса подтверждения: {payload}")
        async with session.post(url, json=payload) as resp:
            response_text = await resp.text()
            _LOGGER.debug(f"Ответ сервера при подтверждении: {response_text}")
            
            try:
                data = await resp.json()
                if "authId" in data and "addresses" in data:
                    return {
                        "auth_id": data["authId"],
                        "addresses": data["addresses"]
                    }
                return {"error": "wrong_code"}
            except Exception as e:
                _LOGGER.error(f"Ошибка разбора ответа: {e}")
                return {"error": "parse_error"}
            
    elif auth_id and user_id:
        # Третий шаг - получение токена для выбранного адреса
        url = f"{BASE_URL}/mobile/auth/get-token"
        headers = {"Content-Type": "application/json"}
        payload = {
            "authId": str(auth_id).strip(),
            "userId": str(user_id).strip()
        }
        
        _LOGGER.debug(f"Отправка запроса на получение токена:")
        _LOGGER.debug(f"URL: {url}")
        _LOGGER.debug(f"Headers: {headers}")
        _LOGGER.debug(f"Payload: {payload}")
        
        try:
            async with session.post(url, json=payload, headers=headers) as resp:
                response_text = await resp.text()
                _LOGGER.debug(f"Статус ответа: {resp.status}")
                _LOGGER.debug(f"Заголовки ответа: {resp.headers}")
                _LOGGER.debug(f"Тело ответа: {response_text}")
                
                if resp.status != 200:
                    _LOGGER.error(f"Ошибка HTTP: {resp.status}")
                    return {"error": "http_error", "message": f"HTTP {resp.status}"}
                
                try:
                    data = await resp.json()
                    _LOGGER.debug(f"Разобранный JSON: {data}")
                    
                    if isinstance(data, dict):
                        if "TOKEN" in data:
                            _LOGGER.debug(f"Успешно получен токен: {data['TOKEN']}")
                            return {"token": data["TOKEN"]}
                        elif "token" in data:
                            _LOGGER.debug(f"Успешно получен токен (lower case): {data['token']}")
                            return {"token": data["token"]}
                        else:
                            _LOGGER.error(f"Токен отсутствует в ответе: {data}")
                            return {"error": "no_token", "message": "Токен отсутствует в ответе"}
                    else:
                        _LOGGER.error(f"Неожиданный формат ответа: {data}")
                        return {"error": "invalid_response", "message": "Неверный формат ответа"}
                        
                except ValueError as e:
                    _LOGGER.error(f"Ошибка разбора JSON: {e}")
                    return {"error": "parse_error", "message": str(e)}
                    
        except aiohttp.ClientError as e:
            _LOGGER.error(f"Ошибка сети: {e}")
            return {"error": "network_error", "message": str(e)}
        except Exception as e:
            _LOGGER.error(f"Неожиданная ошибка: {e}")
            return {"error": "unknown_error", "message": str(e)}
    
    return {"error": "invalid_params", "message": "Неверные параметры запроса"}

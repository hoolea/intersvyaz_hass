"""Единый асинхронный клиент для API Интерсвязь.

Раньше запросы к api.is74.ru и cams.is74.ru были продублированы в
__init__.py и в config_flow.py (с разным, местами несовместимым разбором
ответа — например get_relay_id в двух файлах ожидал разные форматы JSON).
Этот модуль — единственное место, где происходит обращение к сети, что
упрощает сопровождение и тестирование.
"""
from __future__ import annotations

import logging
import uuid as uuid_lib
from typing import Any, Optional

import aiohttp

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://api.is74.ru"
BASE_URL_CAM = "https://cams.is74.ru"
BASE_URL_CDN = "https://cdn.cams.is74.ru"

API_TIMEOUT = aiohttp.ClientTimeout(total=15)


class IntersvyazApiError(Exception):
    """Базовая ошибка при обращении к API Интерсвязь."""


class IntersvyazAuthError(IntersvyazApiError):
    """Токен недействителен/истёк или неверные учётные данные."""


class IntersvyazApiClient:
    """Тонкая обёртка над мобильным API Интерсвязь."""

    def __init__(self, session: aiohttp.ClientSession, token: Optional[str] = None) -> None:
        self._session = session
        self.token = token

    def _auth_headers(self) -> dict[str, str]:
        if not self.token:
            raise IntersvyazAuthError("Токен не установлен")
        return {"Authorization": f"Bearer {self.token}"}

    async def _request(
        self, method: str, url: str, *, auth: bool = False, **kwargs: Any
    ) -> Any:
        headers = kwargs.pop("headers", {}) or {}
        if auth:
            headers.update(self._auth_headers())

        try:
            async with self._session.request(
                method, url, headers=headers, timeout=API_TIMEOUT, **kwargs
            ) as resp:
                text = await resp.text()

                if resp.status in (401, 403):
                    raise IntersvyazAuthError(
                        f"Ошибка авторизации ({resp.status}) при запросе {url}"
                    )
                if resp.status != 200:
                    _LOGGER.debug("Ответ %s на %s: %s", resp.status, url, text[:500])
                    raise IntersvyazApiError(f"HTTP {resp.status} при запросе {url}")

                if not text:
                    return None
                try:
                    return await resp.json(content_type=None)
                except ValueError as err:
                    raise IntersvyazApiError(
                        f"Не удалось разобрать ответ сервера ({url}): {err}"
                    ) from err
        except aiohttp.ClientError as err:
            raise IntersvyazApiError(f"Ошибка сети при запросе {url}: {err}") from err
        except TimeoutError as err:
            raise IntersvyazApiError(f"Таймаут при запросе {url}") from err

    # ------------------------------------------------------------------ #
    # Авторизация по логину/паролю
    # ------------------------------------------------------------------ #
    async def login(self, username: str, password: str) -> str:
        data = await self._request(
            "POST",
            f"{BASE_URL}/auth/mobile",
            json={"username": username, "password": password},
            headers={"Content-Type": "application/json"},
        )
        token = data.get("TOKEN") if data else None
        if not token:
            raise IntersvyazAuthError("Токен отсутствует в ответе сервера")
        self.token = token
        return token

    # ------------------------------------------------------------------ #
    # Авторизация по номеру телефона (SMS)
    # ------------------------------------------------------------------ #
    async def send_sms(self, phone: str) -> str:
        """Отправляет СМС с кодом, возвращает device_id для следующих шагов."""
        device_id = uuid_lib.uuid4().hex
        await self._request(
            "POST",
            f"{BASE_URL}/mobile/auth/send-sms",
            json={"phone": phone.replace("+7", ""), "uniqueDeviceId": device_id},
        )
        return device_id

    async def confirm_sms(self, phone: str, code: str, device_id: str) -> dict:
        data = await self._request(
            "POST",
            f"{BASE_URL}/mobile/auth/confirm",
            json={
                "confirmCode": code,
                "phone": phone.replace("+7", ""),
                "uniqueDeviceId": device_id,
            },
        )
        if not data or "authId" not in data or "addresses" not in data:
            raise IntersvyazApiError("Неверный код подтверждения")
        return data

    async def token_by_address(self, auth_id: str, user_id: str) -> str:
        data = await self._request(
            "POST",
            f"{BASE_URL}/mobile/auth/get-token",
            json={"authId": str(auth_id).strip(), "userId": str(user_id).strip()},
            headers={"Content-Type": "application/json"},
        )
        token = (data.get("TOKEN") or data.get("token")) if data else None
        if not token:
            raise IntersvyazAuthError("Токен отсутствует в ответе сервера")
        self.token = token
        return token

    # ------------------------------------------------------------------ #
    # Домофон / реле
    # ------------------------------------------------------------------ #
    async def get_relays(self) -> list[dict]:
        """Вернуть все доступные реле с уникальными нормализованными ID."""
        data = await self._request("GET", f"{BASE_URL}/domofon/relays", auth=True)
        if not isinstance(data, list):
            raise IntersvyazApiError("API не вернул список реле")

        relays: list[dict] = []
        seen: set[str] = set()
        for relay in data:
            if not isinstance(relay, dict):
                continue
            value = relay.get("RELAY_ID")
            if isinstance(value, bool) or not isinstance(value, (str, int)):
                continue
            relay_id = str(value).strip()
            if not relay_id.isascii() or not relay_id.isdecimal() or relay_id in seen:
                continue
            seen.add(relay_id)
            relays.append({**relay, "RELAY_ID": relay_id})
        return relays

    async def get_relay_id(self) -> str:
        data = await self._request("GET", f"{BASE_URL}/domofon/relays", auth=True)
        if isinstance(data, list) and data:
            relay_id = data[0].get("RELAY_ID")
            if relay_id:
                return relay_id
        raise IntersvyazApiError("API не вернул список реле")

    async def open_door(self, relay_id: str) -> None:
        await self._request(
            "POST",
            f"{BASE_URL}/domofon/relays/{relay_id}/open?from=app",
            auth=True,
            headers={"Content-Type": "application/json"},
        )

    # ------------------------------------------------------------------ #
    # Камеры
    # ------------------------------------------------------------------ #
    async def get_group_id(self) -> str:
        """ID группы 'Умный двор', либо, если её нет, 'Свои камеры'."""
        data = await self._request("GET", f"{BASE_URL_CAM}/api/get-group/", auth=True)
        for group in data or []:
            if group.get("NAME", "").startswith("Умный двор"):
                return group["ID"]

        data = await self._request(
            "GET", f"{BASE_URL_CAM}/api/get-group/?selfCams=true", auth=True
        )
        for group in data or []:
            if group.get("NAME") == "Свои камеры":
                return group["ID"]

        raise IntersvyazApiError(
            "Не найдены ни группа 'Умный двор', ни 'Свои камеры'"
        )

    async def get_group_cameras(self, group_id: str) -> list[dict]:
        data = await self._request(
            "GET", f"{BASE_URL_CAM}/api/get-group/{group_id}", auth=True
        )
        if not isinstance(data, list):
            raise IntersvyazApiError(f"Неожиданный формат данных камер: {type(data)}")
        return data

    def snapshot_url(self, camera_uuid: str) -> str:
        return f"{BASE_URL_CDN}/snapshot?uuid={camera_uuid}&token=bearer-{self.token}"

    def fallback_stream_url(self, camera_uuid: str) -> str:
        return (
            f"{BASE_URL_CDN}/hls/playlists/multivariant.m3u8"
            f"?uuid={camera_uuid}&token=bearer-{self.token}"
        )

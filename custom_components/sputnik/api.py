"""HTTP client for the Sputnik REST API.

Base URL: https://api-mobile-gorod.sputnik.systems/api/mobile/v2/

Auth flow:
  POST auth2/apply         -> request_id
  POST auth2/<id>/confirm  -> {jwt, citizen_state}

The resulting jwt is sent on every request via the "jwt" header.
POST requests whose path contains "auth2" also carry an
HMAC-SHA256 signature header ("X-Sign").
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from .const import (
    DEFAULT_APP_NAME,
    DEFAULT_APP_VERSION,
    DEFAULT_BASE_URL,
    JWT_HEADER,
    SIGN_HEADER,
    SIGN_SECRET,
    _LOGGER,
)


class ApiError(Exception):
    """Generic API error.

    ``code``/``message_field`` mirror the structured ``code``/``message``
    fields the server returns in most error bodies (e.g. 11005 rate-limit),
    when available.
    """

    def __init__(
        self,
        message: str,
        *,
        code: int | str | None = None,
        message_field: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message_field = message_field


class AuthError(ApiError):
    """Authentication / credentials error (-> ConfigEntryAuthFailed)."""


@dataclass
class SputnikAddress:
    address_id: str
    title: str = ""


@dataclass
class SputnikCamera:
    """A camera/intercom with its media url and doorbell device uuid."""

    uuid: str
    device_uuid: str
    stream_url: str = ""
    thumb: str = ""
    title: str = ""


@dataclass
class SputnikProfile:
    """Lightweight profile view: selected address + cameras + full raw body."""

    addresses: list[SputnikAddress] = field(default_factory=list)
    cameras: list[SputnikCamera] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def address_id(self) -> str:
        return self.addresses[0].address_id if self.addresses else ""

    def field(self, key: str, default: Any = None) -> Any:
        """Return a raw ``account/settings`` field, e.g. ``call_enabled``."""
        return self.raw.get(key, default)


class SputnikApiClient:
    """Thin aiohttp based client around the Sputnik REST API."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        jwt: str = "",
        app_version: str = DEFAULT_APP_VERSION,
        app_name: str = DEFAULT_APP_NAME,
        phone: str = "",
        timeout: int = 15,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self.jwt = jwt
        self.app_version = app_version
        self.app_name = app_name
        self.phone = phone
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None

    # -- lifecycle -----------------------------------------------------
    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None

    # -- signing -------------------------------------------------------
    def _sign(self, phone: str) -> str:
        """HMAC-SHA256("<phone>-<unix_seconds>", secret), lowercase hex."""
        message = f"{phone}-{int(time.time())}"
        digest = hmac.new(
            SIGN_SECRET.encode(), message.encode(), hashlib.sha256
        ).hexdigest()
        return digest.lower()

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "X-App-Version": self.app_version,
            "X-App-Name": self.app_name,
        }
        if self.jwt:
            headers[JWT_HEADER] = self.jwt
        if extra:
            headers.update(extra)
        return headers

    # -- low level -----------------------------------------------------
    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        json: Any = None,
        query: dict[str, str] | None = None,
        sign_phone: str | None = None,
        expect_auth: bool = True,
    ) -> dict[str, Any]:
        session = await self._get_session()
        url = f"{self._base_url}/{endpoint.lstrip('/')}"
        headers = self._headers()
        if sign_phone is not None:
            headers[SIGN_HEADER] = self._sign(sign_phone)
        try:
            async with session.request(
                method, url, headers=headers, json=json, params=query
            ) as resp:
                if expect_auth and resp.status in (401, 403):
                    raise AuthError("Authentication rejected (401/403)")
                body = await _read_json(resp)
                _LOGGER.debug("%s %s -> %s body=%s", method, endpoint, resp.status, body)
                if not resp.ok:
                    err_code = body.get("code") if isinstance(body, dict) else None
                    err_msg = (
                        body.get("message") if isinstance(body, dict) else None
                    )
                    # 429 = Too Many Requests by definition; the server body may
                    # be empty, so a missing code is still a rate-limit.
                    if resp.status == 429:
                        err_code = err_code or 11005
                        err_msg = err_msg or (body.get("error") if isinstance(body, dict) else None)
                    detail = err_msg or body
                    raise ApiError(
                        f"{method} {endpoint} -> HTTP {resp.status}: {detail}",
                        code=err_code,
                        message_field=err_msg,
                    )
                return body or {}
        except aiohttp.ClientError as err:
            raise ApiError(f"Request failed: {err}") from err

    # -- authentication ------------------------------------------------
    async def async_login(self, phone: str, auth_code: str) -> str:
        """Run apply -> confirm and return the jwt token."""
        request_id = await self.async_apply(phone)
        return await self.async_confirm(request_id, auth_code)

    async def async_apply(self, phone: str) -> str:
        """POST auth2/apply and return the request_id."""
        self.phone = phone
        self.jwt = ""
        apply = await self._request(
            "POST",
            "auth2/apply",
            json={
                "phone": phone,
                "locale": "ru",
                "platform": self.app_name,
                # Marker required by the server for this endpoint.
                "app": "default",
                "time": int(time.time()),
            },
            sign_phone=phone,
        )
        request_id = _first(apply, "request_id")
        if not request_id:
            raise AuthError("Login: no request_id returned by apply")
        return str(request_id)

    async def async_confirm(self, request_id: str, auth_code: str) -> str:
        """POST auth2/<request_id>/confirm and return the jwt token."""
        confirm = await self._request(
            "POST",
            f"auth2/{request_id}/confirm",
            json={
                "request_id": request_id,
                "auth_code": auth_code,
                "platform": "android",
                # The "app" marker must match the context created by apply.
                "app": "default",
                "phone": self.phone,
                "time": int(time.time()),
            },
            sign_phone=self.phone,
        )
        jwt = _first(confirm, "jwt")
        if not jwt:
            raise AuthError("Login: no jwt returned by confirm")
        self.jwt = jwt
        return jwt

    # -- read endpoints ------------------------------------------------
    async def async_get_profile(self) -> SputnikProfile:
        data = await self._request("GET", "account/settings")
        profile = SputnikProfile(raw=_as_dict(data))
        for addr in profile.raw.get("addresses") or []:
            addr_id = _first(addr, "id") or _first(addr, "addressId")
            if addr_id:
                profile.addresses.append(
                    SputnikAddress(
                        address_id=str(addr_id),
                        title=_first(addr, "title")
                        or _first(addr, "fullAddress")
                        or "",
                    )
                )
        self.async_populate_cameras_from(data, profile)
        return profile

    def async_populate_cameras_from(
        self, data: dict[str, Any], profile: SputnikProfile
    ) -> None:
        cameras = []
        for cam in _as_dict(data).get("cameras") or []:
            cameras.append(
                SputnikCamera(
                    uuid=str(_first(cam, "uuid") or _first(cam, "id")),
                    device_uuid=str(
                        _first(cam, "device_uuid")
                        or _first(cam, "deviceSerial")
                        or ""
                    ),
                    stream_url=_first(cam, "stream_url") or "",
                    thumb=_first(cam, "thumb") or "",
                    title=_first(cam, "title") or "",
                )
            )
        profile.cameras = cameras

    async def async_get_cameras(self, address_id: str) -> list[SputnikCamera]:
        # The backend rejects an empty "list" param (HTTP 422),
        # so version/list/order must all be populated.
        data = await self._request(
            "GET",
            f"addresses/{address_id}/cameras",
            query={"version": "2", "list": "all", "order": "main_screen"},
        )
        cameras = []
        for cam in _as_dict(data).get("cameras") or _as_dict(data).get("items") or []:
            cameras.append(
                SputnikCamera(
                    uuid=str(_first(cam, "uuid") or _first(cam, "id")),
                    device_uuid=str(
                        _first(cam, "device_uuid")
                        or _first(cam, "deviceSerial")
                        or ""
                    ),
                    stream_url=_first(cam, "stream_url") or "",
                    thumb=_first(cam, "thumb") or "",
                    title=_first(cam, "title") or "",
                )
            )
        return cameras

    # -- actions -------------------------------------------------------
    async def async_open_door(
        self, address_id: str, device_uuid: str, producer: str = "app"
    ) -> None:
        await self._request(
            "POST",
            f"addresses/{address_id}/devices/{device_uuid}/open_door",
            json={"widget": False, "producer": producer},
        )


async def _read_json(resp: aiohttp.ClientResponse) -> Any:
    try:
        raw = await resp.text()
    except Exception:  # noqa: BLE001
        raw = ""
    _LOGGER.debug(
        "raw body=%r ct=%r ce=%r",
        raw[:500],
        resp.headers.get("Content-Type"),
        resp.headers.get("Content-Encoding"),
    )
    if raw.strip():
        try:
            return json.loads(raw)
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _as_dict(value: Any) -> dict[str, Any]:
    """Return a dict, tolerant of list/None bodies."""
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return {"items": value}
    return {}


def _first(obj: Any, *keys: str) -> Any:
    if not isinstance(obj, dict):
        return None
    for key in keys:
        if key in obj and obj[key] not in (None, ""):
            return obj[key]
    return None

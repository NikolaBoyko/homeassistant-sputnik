"""Config flow for the Sputnik (sputnik.axmor.com) integration."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.exceptions import HomeAssistantError

from .api import ApiError, AuthError, SputnikApiClient
from .const import (
    DEFAULT_APP_NAME,
    DEFAULT_APP_VERSION,
    DEFAULT_BASE_URL,
    DOMAIN,
    _LOGGER,
)

# Server rate-limit code for repeated auth requests (see docs/sputnik-api.md).
RATE_LIMIT_CODE = 11005


def _error_base(err: Exception) -> str:
    """Map an API/auth exception to a config-flow ``errors`` key."""
    if isinstance(err, AuthError):
        return "invalid_auth"
    if isinstance(err, ApiError):
        if err.code == RATE_LIMIT_CODE:
            return "rate_limited"
        return "cannot_connect"
    return "unknown"


class SputnikConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Sputnik."""

    VERSION = 1

    def __init__(self) -> None:
        super().__init__()
        # Persisted across steps.
        self._phone: str = ""
        self._app_version: str = DEFAULT_APP_VERSION
        self._app_name: str = DEFAULT_APP_NAME
        self._base_url: str = DEFAULT_BASE_URL
        self._request_id: str = ""

    # ------------------------------------------------------------------
    async def async_step_user(
        self, user_input: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        """Step 1 — phone number.  Calls auth2/apply to request an SMS code."""
        errors: dict[str, str] = {}

        if user_input is not None:
            phone = user_input["phone"].strip()
            self._phone = phone
            self._app_version = user_input.get("app_version", DEFAULT_APP_VERSION)
            self._app_name = user_input.get("app_name", DEFAULT_APP_NAME)
            self._base_url = user_input.get("base_url", DEFAULT_BASE_URL)

            client = self._build_client()
            try:
                self._request_id = await client.async_apply(phone)
            except Exception as err:  # noqa: BLE001 - mapped below
                _LOGGER.debug("apply/confirm error: %s", err)
                errors["base"] = _error_base(err)
            finally:
                await client.close()

            if not errors:
                return await self.async_step_confirm()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("phone"): str,
                    vol.Optional(
                        "app_version", default=DEFAULT_APP_VERSION
                    ): str,
                    vol.Optional("app_name", default=DEFAULT_APP_NAME): str,
                    vol.Optional("base_url", default=DEFAULT_BASE_URL): str,
                }
            ),
            errors=errors,
        )

    # ------------------------------------------------------------------
    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        """Step 2 — SMS / push code.  Calls auth2/<id>/confirm."""
        errors: dict[str, str] = {}

        if user_input is not None:
            code = user_input["auth_code"].strip()
            client = self._build_client()
            try:
                jwt = await client.async_confirm(self._request_id, code)
                # Quick sanity-check: verify we can read the profile.
                await client.async_get_profile()
            except Exception as err:  # noqa: BLE001 - mapped below
                _LOGGER.debug("apply/confirm error: %s", err)
                errors["base"] = _error_base(err)
            finally:
                await client.close()

            if not errors:
                await self.async_set_unique_id(self._phone)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=self._phone,
                    data={
                        "phone": self._phone,
                        "jwt": jwt,
                        "app_version": self._app_version,
                        "app_name": self._app_name,
                        "base_url": self._base_url,
                    },
                )

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({vol.Required("auth_code"): str}),
            errors=errors,
            description_placeholders={"phone": self._phone},
        )

    # ------------------------------------------------------------------
    async def async_step_reauth(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Re-auth flow triggered when the JWT is rejected (401/403)."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        """Re-auth: re-run phone → SMS → jwt and update the existing entry."""
        errors: dict[str, str] = {}
        existing = self._get_reauth_entry()
        self._phone = existing.data["phone"]
        self._app_version = existing.data.get("app_version", DEFAULT_APP_VERSION)
        self._app_name = existing.data.get("app_name", DEFAULT_APP_NAME)
        self._base_url = existing.data.get("base_url", DEFAULT_BASE_URL)

        if user_input is not None:
            client = self._build_client()
            try:
                self._request_id = await client.async_apply(self._phone)
            except Exception as err:  # noqa: BLE001 - mapped below
                _LOGGER.debug("apply/confirm error: %s", err)
                errors["base"] = _error_base(err)
            finally:
                await client.close()

            if not errors:
                return await self.async_step_reauth_code()

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders={"phone": self._phone},
        )

    async def async_step_reauth_code(
        self, user_input: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        """Re-auth step 2: enter new SMS code."""
        errors: dict[str, str] = {}

        if user_input is not None:
            code = user_input["auth_code"].strip()
            client = self._build_client()
            try:
                jwt = await client.async_confirm(self._request_id, code)
                await client.async_get_profile()
            except Exception as err:  # noqa: BLE001 - mapped below
                _LOGGER.debug("apply/confirm error: %s", err)
                errors["base"] = _error_base(err)
            finally:
                await client.close()

            if not errors:
                existing = self._get_reauth_entry()
                return self.async_update_reload_and_abort(
                    existing,
                    data_updates={"jwt": jwt},
                )

        return self.async_show_form(
            step_id="reauth_code",
            data_schema=vol.Schema({vol.Required("auth_code"): str}),
            errors=errors,
        )

    # ------------------------------------------------------------------
    def _build_client(self) -> SputnikApiClient:
        return SputnikApiClient(
            base_url=self._base_url,
            app_version=self._app_version,
            app_name=self._app_name,
            phone=self._phone,
        )

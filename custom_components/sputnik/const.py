"""Constants for the Sputnik integration."""

from __future__ import annotations

import logging

DOMAIN = "sputnik"

_LOGGER = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api-mobile-gorod.sputnik.systems/api/mobile/v2/"

DEFAULT_APP_VERSION = "6.29.5(454)"
DEFAULT_APP_NAME = "android"

SIGN_HEADER = "X-Sign"
SIGN_SECRET = "vYYGXkqpbFfLM1MEsk7Tmyn2s22MVQ3N9riBfVnap77qrx4L"

JWT_HEADER = "jwt"

PLATFORMS = ["switch", "camera"]

# How often the coordinator refreshes profile/cameras.
SCAN_INTERVAL_SECONDS = 60

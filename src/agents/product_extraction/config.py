"""
Configuration & shared errors
=============================

One place for every tunable. Values come from the local .env (loaded with
override=True so the file always beats stale shell variables, matching the
other agents in this project).
"""

import os

from dotenv import load_dotenv

load_dotenv(override=True)

# --- credentials -----------------------------------------------------------
# APIFY_API_TOKEN is only required when a URL routes to an Apify actor, so it is
# read lazily (see require_apify_token) instead of exploding at import time —
# Playwright-only usage must work without an Apify account.
APIFY_API_TOKEN = os.environ.get("APIFY_API_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")  # used implicitly by ChatOpenAI

# --- models ----------------------------------------------------------------
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5-nano")
AGENT_MAX_STEPS = int(os.environ.get("PRODUCT_AGENT_MAX_STEPS", "20"))

# --- shopper location ------------------------------------------------------
# E-commerce prices are geo-dependent: currency, taxes, shipping cost and even
# availability change with the visitor's country. Playwright browses from THIS
# machine (already the right country), but Apify actors run in the cloud and
# default to US proxies — which is why the same product came back in USD.
# Every country-aware actor and the browser context are pinned to these values.
TARGET_COUNTRY = os.environ.get("PRODUCT_COUNTRY", "MA").upper()      # ISO 3166-1

# locale, timezone and Accept-Language per country. They must agree with each
# other: a browser claiming en-US while sitting in Africa/Casablanca is a
# fingerprint mismatch that anti-bot scripts flag.
_COUNTRY_PROFILES = {
    "MA": ("fr-MA", "Africa/Casablanca", "fr-MA,fr;q=0.9,ar-MA;q=0.8,ar;q=0.7,en;q=0.6"),
    "US": ("en-US", "America/New_York", "en-US,en;q=0.9"),
    "GB": ("en-GB", "Europe/London", "en-GB,en;q=0.9"),
    "FR": ("fr-FR", "Europe/Paris", "fr-FR,fr;q=0.9,en;q=0.8"),
    "ES": ("es-ES", "Europe/Madrid", "es-ES,es;q=0.9,en;q=0.8"),
    "DE": ("de-DE", "Europe/Berlin", "de-DE,de;q=0.9,en;q=0.8"),
    "IT": ("it-IT", "Europe/Rome", "it-IT,it;q=0.9,en;q=0.8"),
    "CA": ("en-CA", "America/Toronto", "en-CA,en;q=0.9,fr-CA;q=0.8"),
    "AE": ("ar-AE", "Asia/Dubai", "ar-AE,ar;q=0.9,en;q=0.8"),
    "SA": ("ar-SA", "Asia/Riyadh", "ar-SA,ar;q=0.9,en;q=0.8"),
    "EG": ("ar-EG", "Africa/Cairo", "ar-EG,ar;q=0.9,en;q=0.8"),
    "DZ": ("fr-DZ", "Africa/Algiers", "fr-DZ,fr;q=0.9,ar;q=0.8,en;q=0.7"),
    "TN": ("fr-TN", "Africa/Tunis", "fr-TN,fr;q=0.9,ar;q=0.8,en;q=0.7"),
}
_profile = _COUNTRY_PROFILES.get(TARGET_COUNTRY, ("en-US", "UTC", "en-US,en;q=0.9"))

# Explicit env values always win over the country profile.
TARGET_LOCALE = os.environ.get("PRODUCT_LOCALE") or _profile[0]
TARGET_TIMEZONE = os.environ.get("PRODUCT_TIMEZONE") or _profile[1]
ACCEPT_LANGUAGE = os.environ.get("PRODUCT_ACCEPT_LANGUAGE") or _profile[2]

# --- Playwright ------------------------------------------------------------
HEADLESS = os.environ.get("PRODUCT_HEADLESS", "1") != "0"
BROWSER_CHANNEL = os.environ.get("PRODUCT_BROWSER_CHANNEL") or None  # e.g. "chrome"
PAGE_TIMEOUT_MS = int(os.environ.get("PRODUCT_PAGE_TIMEOUT_MS", "45000"))
# Extra dwell time after load so client-side rendered prices/variants appear.
SETTLE_MS = int(os.environ.get("PRODUCT_SETTLE_MS", "1500"))
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# --- Apify -----------------------------------------------------------------
APIFY_BASE_URL = "https://api.apify.com/v2"
# Actor runs are started through the REST API (origin=API) rather than MCP:
# actors built on an outdated Apify SDK crash on the 'MCP' run origin.
ACTOR_START_TIMEOUT_S = int(os.environ.get("PRODUCT_ACTOR_TIMEOUT_S", "300"))
ACTOR_POLL_INTERVAL_S = 5

# --- output scope ----------------------------------------------------------
# One URL = one product. Marketplaces often expose every purchasable SKU of the
# listing (AliExpress returns 40 colour/size rows for a single shirt), which
# buries the product itself. Variants are therefore OFF by default: the answer
# describes the product at the URL. Turn them on with --variants /
# PRODUCT_VARIANTS=1. The `variants` key stays in the JSON either way, so the
# schema never changes shape — it is simply empty.
INCLUDE_VARIANTS = os.environ.get("PRODUCT_VARIANTS", "0") not in ("0", "", "false", "False")

# --- LLM input budget ------------------------------------------------------
# How much page text / raw actor JSON is handed to the model. Enough for a long
# product page, small enough to stay cheap and fast.
MAX_PAGE_TEXT_CHARS = int(os.environ.get("PRODUCT_MAX_PAGE_TEXT", "18000"))
MAX_RAW_RECORD_CHARS = int(os.environ.get("PRODUCT_MAX_RAW_RECORD", "18000"))


def require_apify_token() -> str:
    if not APIFY_API_TOKEN:
        raise ConfigError(
            "APIFY_API_TOKEN is not set — this URL needs an Apify actor. "
            "Add it to your .env file."
        )
    return APIFY_API_TOKEN


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class ExtractionError(Exception):
    """Base class for every failure raised by this package."""


class ConfigError(ExtractionError):
    """Missing credentials or invalid settings."""


class PageLoadError(ExtractionError):
    """The page could not be fetched or rendered (network, timeout, block)."""


class ActorRunError(ExtractionError):
    """An Apify actor run failed, timed out, or returned nothing."""


class UnsupportedUrlError(ExtractionError):
    """The input is not a usable product URL."""

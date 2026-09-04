"""
Configuration & shared errors
=============================

One place for every tunable. Values come from the local .env (loaded with
override=True so the file always beats stale shell variables, matching the
other agents in this project).
"""

import os
from typing import Any

from dotenv import load_dotenv

load_dotenv(override=True)

# --- credentials -----------------------------------------------------------
# APIFY_API_TOKEN is only required when a URL routes to an Apify actor, so it is
# read lazily (see require_apify_token) instead of exploding at import time —
# Playwright-only usage must work without an Apify account.
APIFY_API_TOKEN = os.environ.get("APIFY_API_TOKEN")
# Same rule for the model key: only `use_agent=True` reaches the LLM, so the
# deterministic pipeline must keep working without it (see require_anthropic_key).
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")  # used implicitly by ChatAnthropic

# --- models ----------------------------------------------------------------
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5")
# Claude counts thinking and answer against the same ceiling, so this has to
# cover both: too low and the product sheet is truncated mid-JSON.
#
# This is a backstop, not a tuning knob: the model never sees it, and a run that
# hits it is billed in full for an answer that is then thrown away. Opus 5 thinks
# by default, which eats into the same ceiling, so 8000 left very little room for
# a long spec table. Raised to 16000 -- the largest value that stays clear of the
# SDK's HTTP timeout on a non-streaming request. Unused headroom costs nothing.
ANTHROPIC_MAX_TOKENS = int(os.environ.get("PRODUCT_MAX_OUTPUT_TOKENS", "16000"))
AGENT_MAX_STEPS = int(os.environ.get("PRODUCT_AGENT_MAX_STEPS", "20"))

# Public list prices (input, output) in USD per million tokens. Hand-entered, not
# queried online: recheck on every model migration. A model missing from this table
# is reported by `summarize_usage`, never silently counted as free.
PRICES_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}
# Multipliers applied to the base input price for cached tokens. The ratios are the
# same on every Claude model; only the base price differs.
CACHE_READ_MULTIPLIER: float = 0.10
CACHE_WRITE_5MIN_MULTIPLIER: float = 1.25
CACHE_WRITE_1H_MULTIPLIER: float = 2.00

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


def require_anthropic_key() -> str:
    """Same contract as require_apify_token, for the LLM half of the extraction.

    Without this the missing key surfaces as whatever exception the Anthropic
    client raises inside its constructor, which the error mapper does not
    recognize and reports as a bare 500.
    """
    if not ANTHROPIC_API_KEY:
        raise ConfigError(
            "ANTHROPIC_API_KEY is not set — the agent-assisted extraction needs it. "
            "Add it to your .env file, or call with use_agent=False."
        )
    return ANTHROPIC_API_KEY


def summarize_usage(usage: dict[str, Any]) -> str:
    """One line of token accounting for an agent run, with an estimated cost.

    This agent is the only one in the repository whose cost grows with the number of
    products imported rather than with the number of studies, and the only one running
    a reasoning loop — where every step re-bills the whole history. It is therefore the
    one that most needs to be counted.

    Cache tokens are priced apart on purpose. `langchain_anthropic` folds cache reads
    and writes into `input_tokens` (Anthropic's own `input_tokens` excludes them), so
    charging the whole thing at the base input rate would overcharge a cache read
    tenfold and — worse — report the same cost with and without caching, hiding the
    very saving the cache is there to produce.

    Args:
        usage: `model -> usage metadata`, as produced by
            `langchain_core.callbacks.get_usage_metadata_callback`.

    Returns:
        A recap line, empty when no call was made.
    """
    if not usage:
        return ""
    parts: list[str] = []
    total = 0.0
    missing_price = False
    for model, metrics in sorted(usage.items()):
        details = metrics.get("input_token_details") or {}
        cache_read = int(details.get("cache_read", 0) or 0)
        # `cache_creation` and the per-TTL breakdown are mutually exclusive:
        # langchain_anthropic zeroes the former as soon as the latter is filled in,
        # so summing them never double-counts.
        write_5min = int(details.get("ephemeral_5m_input_tokens", 0) or 0)
        write_1h = int(details.get("ephemeral_1h_input_tokens", 0) or 0)
        write_untyped = int(details.get("cache_creation", 0) or 0)  # TTL unknown
        cache_write = write_5min + write_1h + write_untyped

        total_input = int(metrics.get("input_tokens", 0) or 0)
        fresh_input = max(total_input - cache_read - cache_write, 0)
        output = int(metrics.get("output_tokens", 0) or 0)

        prices = PRICES_USD_PER_MTOK.get(model)
        if prices is None:
            missing_price = True
            price_in, price_out = 0.0, 0.0
        else:
            price_in, price_out = prices

        cost = (
            fresh_input * price_in
            + cache_read * price_in * CACHE_READ_MULTIPLIER
            + (write_5min + write_untyped) * price_in * CACHE_WRITE_5MIN_MULTIPLIER
            + write_1h * price_in * CACHE_WRITE_1H_MULTIPLIER
            + output * price_out
        ) / 1_000_000
        total += cost

        line = f"{model}: {fresh_input} input / {output} output"
        if cache_read or cache_write:
            share = 100.0 * cache_read / total_input if total_input else 0.0
            line += (
                f" / {cache_read} cache read ({share:.0f}% of input)"
                f" / {cache_write} cache written"
            )
        line += " (price unknown)" if prices is None else f" (~${cost:.4f})"
        parts.append(line)

    recap = " | ".join(parts) + f" | estimated total ~${total:.4f}"
    if missing_price:
        recap += (
            " | WARNING: a model absent from PRICES_USD_PER_MTOK was counted as $0 "
            "- the total is understated"
        )
    return recap


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


class PlatformUnsupportedError(UnsupportedUrlError):
    """The URL is a real product page on a site nothing can currently scrape.

    A subclass rather than a message, because the two cases call for different
    answers. A malformed URL is the caller's mistake and they can fix it; a site
    that blocks every scraper is ours to solve, and no amount of retrying by the
    caller will help. Telling them apart is what lets the API say which it is.
    """

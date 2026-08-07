"""Constants and error codes of the ``products`` domain."""

from enum import StrEnum

# Accepted image content types, mapped to the extension used for the stored object.
ALLOWED_IMAGE_CONTENT_TYPES: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}

# Binary signatures (magic bytes) used to verify the declared content type.
JPEG_SIGNATURE = b"\xff\xd8\xff"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
WEBP_RIFF_SIGNATURE = b"RIFF"
WEBP_FORMAT_SIGNATURE = b"WEBP"

# Content type sent by clients that do not know the real type; never trusted on its own.
GENERIC_CONTENT_TYPES = frozenset({"", "application/octet-stream"})

DEFAULT_IMAGE_SLUG = "image"
MAX_IMAGE_SLUG_LENGTH = 64
IMAGE_READ_CHUNK_SIZE = 64 * 1024


# ---------------------------------------------------------------------------
# Automated extraction (POST /products/extract)
# ---------------------------------------------------------------------------
# Shopper countries accepted by the extraction endpoint. Overridable with
# EXTRACTION_ALLOWED_REGIONS.
DEFAULT_ALLOWED_REGIONS = "MA,FR,ES,US,AE"

REGION_PATTERN = r"^[A-Z]{2}$"

# Browser identity per shopper country, applied per request (see extraction._apply_region).
#
# Deliberately a local copy rather than an import of the agent's private
# ``_COUNTRY_PROFILES``: this domain must not depend on a private name of a package it
# is forbidden to modify. The three values have to agree with one another -- a browser
# claiming en-US from Africa/Casablanca is a fingerprint mismatch anti-bot scripts flag.
REGION_PROFILES: dict[str, tuple[str, str, str]] = {
    "MA": ("fr-MA", "Africa/Casablanca", "fr-MA,fr;q=0.9,ar-MA;q=0.8,ar;q=0.7,en;q=0.6"),
    "FR": ("fr-FR", "Europe/Paris", "fr-FR,fr;q=0.9,en;q=0.8"),
    "ES": ("es-ES", "Europe/Madrid", "es-ES,es;q=0.9,en;q=0.8"),
    "US": ("en-US", "America/New_York", "en-US,en;q=0.9"),
    "AE": ("ar-AE", "Asia/Dubai", "ar-AE,ar;q=0.9,en;q=0.8"),
    "GB": ("en-GB", "Europe/London", "en-GB,en;q=0.9"),
    "DE": ("de-DE", "Europe/Berlin", "de-DE,de;q=0.9,en;q=0.8"),
    "IT": ("it-IT", "Europe/Rome", "it-IT,it;q=0.9,en;q=0.8"),
    "CA": ("en-CA", "America/Toronto", "en-CA,en;q=0.9,fr-CA;q=0.8"),
    "SA": ("ar-SA", "Asia/Riyadh", "ar-SA,ar;q=0.9,en;q=0.8"),
    "EG": ("ar-EG", "Africa/Cairo", "ar-EG,ar;q=0.9,en;q=0.8"),
    "DZ": ("fr-DZ", "Africa/Algiers", "fr-DZ,fr;q=0.9,ar;q=0.8,en;q=0.7"),
    "TN": ("fr-TN", "Africa/Tunis", "fr-TN,fr;q=0.9,ar;q=0.8,en;q=0.7"),
}

# Fallback for an allowed region that has no profile above.
NEUTRAL_REGION_PROFILE = ("en-US", "UTC", "en-US,en;q=0.9")

# Separator between an Apify actor key and the region it is pinned to
# ("amazon" + "MA" -> "amazon@MA"). See extraction.register_region_adapters.
REGION_ACTOR_SEPARATOR = "@"

# Keys through which Apify actors express the shopper country. Only keys an adapter
# already sends are rewritten, so an actor with no country input stays untouched.
ACTOR_COUNTRY_KEYS = ("proxyCountry", "countryCode")


class ErrorCode(StrEnum):
    PRODUCT_NOT_FOUND = "No product sheet found for this identifier."
    IMAGE_TYPE_NOT_ALLOWED = (
        "Unsupported image file: the content must be a JPEG, PNG or WebP image."
    )
    IMAGE_TOO_LARGE = "The image exceeds the maximum allowed size."
    IMAGE_UPLOAD_FAILED = "The image could not be uploaded to storage; the product was not created."
    UNSUPPORTED_URL = "The URL is not a usable product page URL."
    PAGE_LOAD_FAILED = (
        "The product page could not be loaded or rendered (network, timeout, or anti-bot block)."
    )
    ACTOR_RUN_FAILED = "The hosted scraper run failed or returned no product for this URL."
    EXTRACTION_NOT_CONFIGURED = (
        "The extraction agent is not configured on the server: a required credential "
        "or setting is missing."
    )
    EXTRACTION_TIMEOUT = "The extraction exceeded the configured time budget."
    EXTRACTION_INCOMPLETE = (
        "The extraction did not yield the fields required to store a product sheet: "
        "name, description and category are all mandatory."
    )
    EXTRACTION_FAILED = "The extraction failed because of an unexpected server error."

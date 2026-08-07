"""Supabase Storage access for product images.

Plain REST calls (``{SUPABASE_URL}/storage/v1/object/...``) issued with the shared
``httpx.AsyncClient`` created at application startup -- no ``supabase-py`` SDK, no client
rebuilt per request. Isolated in this module so the router and the service stay testable
without touching Supabase.
"""

import logging
from urllib.parse import quote

import httpx
from fastapi import Request

from src.products.config import products_settings
from src.products.exceptions import ImageUploadFailed

logger = logging.getLogger(__name__)


def _storage_base_url() -> str:
    return f"{str(products_settings.SUPABASE_URL).rstrip('/')}/storage/v1/object"


def build_public_image_url(image_path: str | None) -> str | None:
    """Rebuild the public URL of an object stored in the (public) product images bucket."""
    if image_path is None:
        return None
    bucket = products_settings.SUPABASE_STORAGE_BUCKET
    return f"{_storage_base_url()}/public/{bucket}/{quote(image_path)}"


class ProductImageStorage:
    """Thin async wrapper around the Supabase Storage REST API."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def upload(self, *, path: str, content: bytes, content_type: str) -> None:
        """Upload ``content`` to ``path`` in the product images bucket.

        Raises ``ImageUploadFailed`` (502) on any transport or non-2xx response, so the
        caller can abort before writing a product row pointing at a missing object.
        """
        bucket = products_settings.SUPABASE_STORAGE_BUCKET
        url = f"{_storage_base_url()}/{bucket}/{quote(path)}"
        headers = {
            "Authorization": f"Bearer {products_settings.SUPABASE_SERVICE_ROLE_KEY}",
            "apikey": products_settings.SUPABASE_SERVICE_ROLE_KEY,
            "Content-Type": content_type,
            "Cache-Control": "3600",
            "x-upsert": "true",
        }
        try:
            response = await self._client.post(
                url,
                content=content,
                headers=headers,
                timeout=products_settings.STORAGE_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            logger.warning("Supabase Storage upload failed for %s: %s", path, exc)
            raise ImageUploadFailed() from exc

        if response.is_error:
            logger.warning(
                "Supabase Storage rejected the upload of %s: %s %s",
                path,
                response.status_code,
                response.text,
            )
            raise ImageUploadFailed()


async def get_image_storage(request: Request) -> ProductImageStorage:
    """Inject the storage client backed by the application-wide ``httpx.AsyncClient``."""
    return ProductImageStorage(request.app.state.http_client)

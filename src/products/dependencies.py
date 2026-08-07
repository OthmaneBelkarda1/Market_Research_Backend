"""Route dependencies of the ``products`` domain.

Dependencies do not just inject: they load and validate, so a route body never has to
check whether the resource it received actually exists.
"""

import uuid
from typing import Annotated

from fastapi import Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.products.exceptions import ProductNotFound
from src.products.models import Product
from src.products.storage import ProductImageStorage, get_image_storage

DbSession = Annotated[AsyncSession, Depends(get_db)]
ImageStorage = Annotated[ProductImageStorage, Depends(get_image_storage)]


async def valid_product_id(
    product_id: Annotated[uuid.UUID, Path(description="Identifier of the product sheet.")],
    db: DbSession,
) -> Product:
    """Load the product sheet named by the path, or raise 404."""
    product = await db.get(Product, product_id)
    if product is None:
        raise ProductNotFound()
    return product


ExistingProduct = Annotated[Product, Depends(valid_product_id)]

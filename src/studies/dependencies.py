"""Route dependencies of the ``studies`` domain.

Like in ``products``, a dependency loads and validates: a route body never has to check
whether the study it received actually exists.
"""

import uuid
from typing import Annotated

from fastapi import Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.studies import service
from src.studies.models import Study

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def valid_study_id(
    study_id: Annotated[uuid.UUID, Path(description="Identifier of the study.")],
    db: DbSession,
) -> Study:
    """Load the study named by the path, or raise 404."""
    return await service.get_study(db, study_id)


ExistingStudy = Annotated[Study, Depends(valid_study_id)]

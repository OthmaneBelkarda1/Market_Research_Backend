"""allow the ``empty`` status on study_source_data

A collector that exits 0 having found nothing has *run* successfully and *collected*
nothing. ``succeeded`` conflated the two, and study 8609db9e is what that cost: AliExpress
returned zero offers, was filed ``succeeded``, and the report compared prices across one of
its two channels without ever saying so. ``StudySourceStatus`` now answers both questions,
and this revision lets the database store the new answer.

The constraint is named in full rather than through ``op.create_check_constraint``: the
naming convention that produced ``study_source_data_status_check`` lives on the models'
``MetaData``, and whether Alembic's operation object applies it here depends on how the
migration context was configured. Spelling the name out removes the question.

Widening only: every row already stored keeps its status, and the downgrade refuses to
rewrite data rather than doing it silently (see ``downgrade``).

Revision ID: 20260904_0005
Revises: 20260806_0004
Create Date: 2026-09-04

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260904_0005"
down_revision: str | None = "20260806_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONTRAINTE = "study_source_data_status_check"
_AVANT = ("succeeded", "failed", "skipped_region")
_APRES = ("succeeded", "empty", "failed", "skipped_region")


def _appliquer(valeurs: tuple[str, ...]) -> None:
    """Replace the status check constraint with one over ``valeurs``.

    Args:
        valeurs: The statuses the column may hold.
    """
    listees = ", ".join(f"'{valeur}'" for valeur in valeurs)
    op.execute(
        f"ALTER TABLE study_source_data DROP CONSTRAINT IF EXISTS {_CONTRAINTE}"
    )
    op.execute(
        f"ALTER TABLE study_source_data ADD CONSTRAINT {_CONTRAINTE} "
        f"CHECK (status IN ({listees}))"
    )


def upgrade() -> None:
    _appliquer(_APRES)


def downgrade() -> None:
    """Narrow the constraint back — and fail if any row would be left illegal.

    ``ADD CONSTRAINT`` validates the existing rows, so this raises on the first ``empty``
    one. That is the wanted behaviour: rewriting those rows to ``succeeded`` would put back
    exactly the confusion this revision removes, and do it without a trace. If the
    downgrade really has to go through, decide what those studies mean and update them
    explicitly first.
    """
    _appliquer(_AVANT)

"""add platform priority columns to guild_settings

Revision ID: 0002_platform_priority
Revises: 0001_initial
Create Date: 2026-08-17

NOTE: 0001_initial builds the schema with Base.metadata.create_all, so it is
not a frozen snapshot — on a *fresh* database it already creates whatever the
current models declare, including these columns. On a database created before
the model change, the columns are missing and must be added. This migration
therefore checks first and is a no-op when the column already exists, so both
paths converge. Future additive migrations need the same treatment until
0001 is replaced by an explicit, frozen schema.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_platform_priority"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

_TABLE = "guild_settings"
# Empty list = "use the built-in tiers", so existing rows get a usable default.
_NEW_COLUMNS = ("preferred_platforms", "deprioritized_platforms")


def _existing_columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {col["name"] for col in inspector.get_columns(_TABLE)}


def upgrade() -> None:
    existing = _existing_columns()
    for name in _NEW_COLUMNS:
        if name not in existing:
            op.add_column(
                _TABLE,
                sa.Column(name, sa.JSON(), nullable=True, server_default=sa.text("'[]'")),
            )


def downgrade() -> None:
    existing = _existing_columns()
    for name in reversed(_NEW_COLUMNS):
        if name in existing:
            op.drop_column(_TABLE, name)

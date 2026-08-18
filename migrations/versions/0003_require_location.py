"""add require_location to guild_settings

Revision ID: 0003_require_location
Revises: 0002_platform_priority
Create Date: 2026-08-18

Same idempotency caveat as 0002: 0001_initial uses metadata.create_all, so a
fresh database already has this column while an older one does not.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_require_location"
down_revision = "0002_platform_priority"
branch_labels = None
depends_on = None

_TABLE = "guild_settings"
_COLUMN = "require_location"


def _has_column() -> bool:
    inspector = sa.inspect(op.get_bind())
    return _COLUMN in {col["name"] for col in inspector.get_columns(_TABLE)}


def upgrade() -> None:
    if not _has_column():
        op.add_column(
            _TABLE,
            sa.Column(_COLUMN, sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    if _has_column():
        op.drop_column(_TABLE, _COLUMN)

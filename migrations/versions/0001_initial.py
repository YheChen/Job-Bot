"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-14

The initial schema is created directly from the SQLAlchemy metadata so it stays
in lock-step with the ORM models. Subsequent migrations should be generated with
`alembic revision --autogenerate` and use explicit op.* operations.
"""

from __future__ import annotations

from alembic import op

from jobbot.db import models  # noqa: F401  (register tables on metadata)
from jobbot.db.base import Base

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)

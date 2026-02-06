"""Add ah_refresh_token setting

Revision ID: 002
Revises: 001
Create Date: 2026-02-06
"""
from typing import Sequence, Union

from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "INSERT INTO mealieah_settings (key, value) VALUES "
        "('ah_refresh_token', '') "
        "ON CONFLICT (key) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM mealieah_settings WHERE key = 'ah_refresh_token'")

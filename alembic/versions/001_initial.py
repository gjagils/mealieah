"""Initial tables

Revision ID: 001
Revises:
Create Date: 2026-02-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mealieah_ingredient_mappings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("recipe_slug", sa.String(500), index=True, nullable=False),
        sa.Column("recipe_name", sa.String(500), server_default=""),
        sa.Column("ingredient_reference_id", sa.String(100), index=True, nullable=False),
        sa.Column("ingredient_display", sa.String(500), nullable=False),
        sa.Column("status", sa.String(20), server_default="unmapped"),
        sa.Column("ah_product_id", sa.Integer(), nullable=True),
        sa.Column("ah_product_name", sa.String(500), nullable=True),
        sa.Column("ah_product_image_url", sa.Text(), nullable=True),
        sa.Column("ah_product_unit_size", sa.String(100), nullable=True),
        sa.Column("ah_product_price", sa.String(20), nullable=True),
        sa.Column("ah_quantity", sa.Integer(), server_default="1"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )

    op.create_table(
        "mealieah_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(100), unique=True, index=True, nullable=False),
        sa.Column("value", sa.Text(), server_default=""),
    )

    # Insert default settings
    op.execute(
        "INSERT INTO mealieah_settings (key, value) VALUES "
        "('verbose_logging', 'false'), "
        "('ah_user_token', '')"
    )


def downgrade() -> None:
    op.drop_table("mealieah_settings")
    op.drop_table("mealieah_ingredient_mappings")

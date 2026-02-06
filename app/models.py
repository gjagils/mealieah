from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class IngredientMapping(Base):
    __tablename__ = "mealieah_ingredient_mappings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recipe_slug: Mapped[str] = mapped_column(String(500), index=True)
    recipe_name: Mapped[str] = mapped_column(String(500), default="")
    ingredient_reference_id: Mapped[str] = mapped_column(String(100), index=True)
    ingredient_display: Mapped[str] = mapped_column(String(500))

    # "mapped" | "skipped" | "unmapped"
    status: Mapped[str] = mapped_column(String(20), default="unmapped")

    # AH product info (null when status is "skipped" or "unmapped")
    ah_product_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ah_product_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    ah_product_image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    ah_product_unit_size: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ah_product_price: Mapped[str | None] = mapped_column(String(20), nullable=True)
    ah_quantity: Mapped[int] = mapped_column(Integer, default=1)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class AppSetting(Base):
    __tablename__ = "mealieah_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    value: Mapped[str] = mapped_column(Text, default="")

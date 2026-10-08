import json
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Recipe(Base):
    __tablename__ = "recipes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text, default="")
    servings: Mapped[str] = mapped_column(String(100), default="")
    total_time: Mapped[str] = mapped_column(String(100), default="")
    source_url: Mapped[str] = mapped_column(Text, default="")
    # id of the recipe in Allerhande when added from there (prevents duplicates)
    ah_recipe_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # slug of the recipe in Mealie when imported from there (prevents duplicates)
    mealie_slug: Mapped[str | None] = mapped_column(String(500), nullable=True, index=True)
    image_url: Mapped[str] = mapped_column(Text, default="")
    # Glutenvrij voor minstens 1 persoon: "none" | "extra" (extra glutenvrij product erbij)
    # | "replace" (ingrediënt voor iedereen vervangen)
    gf_mode: Mapped[str] = mapped_column(String(10), default="none")
    gf_note: Mapped[str] = mapped_column(Text, default="")
    # JSON list of {"text", "search", "skip", "quantity", "product": {...}|None,
    #               "gluten": bool, "gf_search": str, "gf_product": {...}|None}
    ingredients_json: Mapped[str] = mapped_column(Text, default="[]")
    # JSON list of strings
    instructions_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    @property
    def ingredients(self) -> list[dict]:
        return json.loads(self.ingredients_json or "[]")

    @ingredients.setter
    def ingredients(self, value: list[dict]) -> None:
        self.ingredients_json = json.dumps(value, ensure_ascii=False)

    @property
    def instructions(self) -> list[str]:
        return json.loads(self.instructions_json or "[]")

    @instructions.setter
    def instructions(self, value: list[str]) -> None:
        self.instructions_json = json.dumps(value, ensure_ascii=False)


class PlanEntry(Base):
    """A recipe planned on a specific date."""

    __tablename__ = "plan_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD
    recipe_id: Mapped[int] = mapped_column(Integer, index=True)


class CartPush(Base):
    """What we already put on the AH shopping list for a given week."""

    __tablename__ = "cart_pushes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    week_start: Mapped[str] = mapped_column(String(10), index=True)
    product_id: Mapped[int] = mapped_column(Integer)
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    name: Mapped[str] = mapped_column(String(500), default="")


class AppSetting(Base):
    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    value: Mapped[str] = mapped_column(Text, default="")

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
    # slug of the recipe in Mealie when imported from there (prevents duplicates)
    mealie_slug: Mapped[str | None] = mapped_column(String(500), nullable=True, index=True)
    image_url: Mapped[str] = mapped_column(Text, default="")
    # JSON list of {"text", "search", "skip", "quantity", "product": {...}|None}
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


class WeekmenuEntry(Base):
    __tablename__ = "weekmenu_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    day: Mapped[int] = mapped_column(Integer)  # 0 = maandag ... 6 = zondag
    recipe_id: Mapped[int] = mapped_column(Integer, index=True)


class AppSetting(Base):
    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    value: Mapped[str] = mapped_column(Text, default="")

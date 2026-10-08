"""Read-only import of recipes from a Mealie instance."""
import re

import httpx

from app.logging_config import logger

UNIT_RE = re.compile(
    r"^[\d.,/½¼¾⅓⅔\s-]*(g|gr|gram|kg|ml|l|cl|dl|el|tl|eetlepels?|theelepels?|stuks?|stuk|blikjes?|"
    r"blik|zakjes?|zak|potjes?|pot|bosjes?|bos|teentjes?|tenen?|takjes?|plakjes?|snufje|snuf)?\b\s*",
    re.IGNORECASE,
)


def search_term(text: str, food_name: str = "") -> str:
    """Best-effort supermarket search term for an ingredient line."""
    if food_name.strip():
        return food_name.strip()
    cleaned = UNIT_RE.sub("", text.strip(), count=1)
    cleaned = re.split(r"[,(]", cleaned)[0].strip()
    return cleaned


def convert_recipe(full: dict) -> dict:
    """Map a full Mealie recipe (GET /api/recipes/{slug}) to our recipe fields."""
    ingredients = []
    for ing in full.get("recipeIngredient") or []:
        food = (ing.get("food") or {}).get("name", "") or ""
        text = (ing.get("display") or ing.get("originalText") or "").strip()
        if not text:
            parts = [
                str(ing["quantity"]) if ing.get("quantity") else "",
                (ing.get("unit") or {}).get("name", ""),
                food,
                ing.get("note") or "",
            ]
            text = " ".join(p for p in parts if p).strip()
        if not text:
            continue
        search = search_term(text, food)
        ingredients.append(
            {"text": text, "search": search, "skip": not search, "quantity": 1, "product": None}
        )
    instructions = [
        (s.get("text") or "").strip()
        for s in full.get("recipeInstructions") or []
        if (s.get("text") or "").strip()
    ]
    return {
        "name": (full.get("name") or "").strip(),
        "description": (full.get("description") or "").strip(),
        "servings": str(full.get("recipeYield") or full.get("recipeServings") or "").strip(),
        "total_time": str(full.get("totalTime") or "").strip(),
        "source_url": full.get("orgURL") or "",
        "ingredients": ingredients,
        "instructions": instructions,
    }


class MealieClient:
    def __init__(self, base_url: str, api_token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {api_token}"} if api_token else {}

    async def list_slugs(self, client: httpx.AsyncClient) -> list[str]:
        slugs: list[str] = []
        page = 1
        while True:
            resp = await client.get(
                f"{self.base_url}/api/recipes", headers=self.headers,
                params={"page": page, "perPage": 100},
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get("items", []) if isinstance(data, dict) else data
            slugs += [i["slug"] for i in items if i.get("slug")]
            if not items or (isinstance(data, dict) and page >= data.get("total_pages", 1)):
                return slugs
            page += 1

    async def get_recipe(self, client: httpx.AsyncClient, slug: str) -> dict:
        resp = await client.get(f"{self.base_url}/api/recipes/{slug}", headers=self.headers)
        resp.raise_for_status()
        return resp.json()

    async def get_image(self, client: httpx.AsyncClient, recipe_id: str) -> bytes | None:
        try:
            resp = await client.get(
                f"{self.base_url}/api/media/recipes/{recipe_id}/images/original.webp",
                headers=self.headers,
            )
            return resp.content if resp.status_code == 200 else None
        except httpx.HTTPError as e:
            logger.warning("Mealie image fetch failed for %s: %s", recipe_id, e)
            return None

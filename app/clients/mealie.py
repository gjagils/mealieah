import httpx

from app.config import settings
from app.logging_config import logger


class MealieClient:
    def __init__(self, base_url: str | None = None, api_token: str | None = None):
        self.base_url = (base_url or settings.mealie_url).rstrip("/")
        self.api_token = api_token or settings.mealie_api_token

    @property
    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_token:
            h["Authorization"] = f"Bearer {self.api_token}"
        return h

    async def get_recipes(self, page: int = 1, per_page: int = 50) -> dict:
        async with httpx.AsyncClient() as client:
            logger.debug("Fetching recipes page=%d", page)
            resp = await client.get(
                f"{self.base_url}/api/recipes",
                headers=self._headers,
                params={"page": page, "perPage": per_page},
            )
            resp.raise_for_status()
            return resp.json()

    async def get_recipe(self, slug: str) -> dict:
        async with httpx.AsyncClient() as client:
            logger.debug("Fetching recipe: %s", slug)
            resp = await client.get(
                f"{self.base_url}/api/recipes/{slug}",
                headers=self._headers,
            )
            resp.raise_for_status()
            return resp.json()

    async def get_mealplans(self, start_date: str, end_date: str) -> list[dict]:
        async with httpx.AsyncClient() as client:
            logger.debug("Fetching meal plans %s to %s", start_date, end_date)
            resp = await client.get(
                f"{self.base_url}/api/households/mealplans",
                headers=self._headers,
                params={"start_date": start_date, "end_date": end_date},
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("items", data) if isinstance(data, dict) else data

    async def create_recipe(self, name: str) -> dict:
        """Create a new recipe in Mealie (returns the created recipe with slug)."""
        async with httpx.AsyncClient() as client:
            logger.info("Creating recipe in Mealie: %s", name)
            resp = await client.post(
                f"{self.base_url}/api/recipes",
                headers=self._headers,
                json={"name": name},
            )
            resp.raise_for_status()
            return resp.json()

    async def update_recipe(self, slug: str, data: dict) -> dict:
        """Update recipe fields in Mealie."""
        async with httpx.AsyncClient() as client:
            logger.info("Updating recipe in Mealie: %s", slug)
            resp = await client.patch(
                f"{self.base_url}/api/recipes/{slug}",
                headers=self._headers,
                json=data,
            )
            resp.raise_for_status()
            return resp.json()

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.base_url}/api/app/about",
                    headers=self._headers,
                    timeout=5,
                )
                return resp.status_code == 200
        except Exception:
            logger.warning("Mealie health check failed")
            return False


mealie_client = MealieClient()

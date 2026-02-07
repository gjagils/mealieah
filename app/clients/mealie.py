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
        """Update recipe fields in Mealie using safe GET-merge-PATCH approach."""
        # Fields safe to read from GET and send back via PATCH
        SAFE_FIELDS = {
            "name", "description", "recipeYield", "totalTime", "prepTime",
            "performTime", "recipeCategory", "tags", "tools", "nutrition",
            "recipeIngredient", "recipeInstructions", "settings", "notes",
            "orgURL", "slug",
        }
        async with httpx.AsyncClient() as client:
            # Fetch recipe to use as base, keeping only safe fields
            logger.info("Fetching recipe before update: %s", slug)
            get_resp = await client.get(
                f"{self.base_url}/api/recipes/{slug}",
                headers=self._headers,
                timeout=30,
            )
            if get_resp.status_code == 200:
                full_recipe = get_resp.json()
                update_payload = {k: v for k, v in full_recipe.items() if k in SAFE_FIELDS}
                update_payload.update(data)
            else:
                logger.warning("Could not fetch recipe %s (HTTP %s), using data as-is",
                               slug, get_resp.status_code)
                update_payload = data

            logger.info("Updating recipe in Mealie: %s", slug)
            resp = await client.patch(
                f"{self.base_url}/api/recipes/{slug}",
                headers=self._headers,
                json=update_payload,
                timeout=30,
            )
            if resp.status_code >= 400:
                body = resp.text[:500]
                logger.error("Mealie PATCH %s returned %s: %s", slug, resp.status_code, body)
                # Try PUT as fallback
                resp = await client.put(
                    f"{self.base_url}/api/recipes/{slug}",
                    headers=self._headers,
                    json=update_payload,
                    timeout=30,
                )
                if resp.status_code >= 400:
                    body = resp.text[:500]
                    logger.error("Mealie PUT %s returned %s: %s", slug, resp.status_code, body)
                    raise httpx.HTTPStatusError(
                        f"Mealie update failed (HTTP {resp.status_code}): {body}",
                        request=resp.request,
                        response=resp,
                    )
            return resp.json()

    async def upload_recipe_image(self, slug: str, image_data: bytes, media_type: str) -> bool:
        """Upload an image as the recipe's cover photo."""
        ext_map = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}
        ext = ext_map.get(media_type, "jpg")
        async with httpx.AsyncClient() as client:
            logger.info("Uploading recipe image for %s (%d bytes)", slug, len(image_data))
            headers = {}
            if self.api_token:
                headers["Authorization"] = f"Bearer {self.api_token}"
            resp = await client.put(
                f"{self.base_url}/api/recipes/{slug}/image",
                headers=headers,
                files={"image": (f"recipe.{ext}", image_data, media_type)},
                data={"extension": ext},
                timeout=30,
            )
            resp.raise_for_status()
            return True

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

import html
from typing import Callable

import httpx

from app.logging_config import logger

AH_AUTH_URL = "https://api.ah.nl/mobile-auth/v1/auth/token/anonymous"
AH_TOKEN_URL = "https://api.ah.nl/mobile-auth/v1/auth/token"
AH_AUTHORIZE_URL = "https://login.ah.nl/secure/oauth/authorize"
AH_REDIRECT_URI = "appie://login-exit"
AH_REFRESH_URL = "https://api.ah.nl/mobile-auth/v1/auth/token/refresh"
AH_SEARCH_URL = "https://api.ah.nl/mobile-services/product/search/v2"
AH_CART_URL = "https://api.ah.nl/mobile-services/shoppinglist/v2/items"
AH_GRAPHQL_URL = "https://api.ah.nl/graphql"
AH_RECIPE_URL = "https://www.ah.nl/allerhande/recept/R-R{id}/{slug}"

DEFAULT_HEADERS = {
    "User-Agent": "Appie/8.22.3",
    "Content-Type": "application/json",
    "x-application": "AHWEBSHOP",
}


class AHClient:
    def __init__(self) -> None:
        self._anonymous_token: str | None = None
        self._user_token: str | None = None
        self._user_refresh_token: str | None = None
        self._on_tokens_updated: Callable[[str, str], None] | None = None

    async def _get_anonymous_token(self) -> str:
        if self._anonymous_token:
            return self._anonymous_token
        async with httpx.AsyncClient() as client:
            logger.debug("Requesting anonymous AH token")
            resp = await client.post(
                AH_AUTH_URL,
                headers=DEFAULT_HEADERS,
                json={"clientId": "appie"},
            )
            resp.raise_for_status()
            data = resp.json()
            self._anonymous_token = data["access_token"]
            logger.info("Obtained anonymous AH token")
            return self._anonymous_token

    @staticmethod
    def get_login_url() -> str:
        """Return the AH OAuth2 login URL."""
        return (
            f"{AH_AUTHORIZE_URL}?client_id=appie"
            f"&redirect_uri={AH_REDIRECT_URI}&response_type=code"
        )

    async def exchange_code(self, code: str) -> dict:
        """Exchange an OAuth2 authorization code for tokens."""
        async with httpx.AsyncClient() as client:
            logger.info("Exchanging AH authorization code for tokens")
            resp = await client.post(
                AH_TOKEN_URL,
                headers=DEFAULT_HEADERS,
                json={"code": code, "clientId": "appie"},
            )
            resp.raise_for_status()
            data = resp.json()
            self._user_token = data["access_token"]
            self._user_refresh_token = data["refresh_token"]
            logger.info("AH code exchange successful")
            return data

    def set_user_tokens(
        self,
        access_token: str,
        refresh_token: str,
        on_tokens_updated: Callable[[str, str], None] | None = None,
    ) -> None:
        self._user_token = access_token
        self._user_refresh_token = refresh_token
        self._on_tokens_updated = on_tokens_updated

    def set_user_token(self, token: str) -> None:
        self._user_token = token

    async def _refresh_user_token(self) -> bool:
        if not self._user_refresh_token:
            return False
        try:
            async with httpx.AsyncClient() as client:
                logger.info("Refreshing AH user token")
                resp = await client.post(
                    AH_REFRESH_URL,
                    headers=DEFAULT_HEADERS,
                    json={
                        "refreshToken": self._user_refresh_token,
                        "clientId": "appie",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                self._user_token = data["access_token"]
                self._user_refresh_token = data["refresh_token"]
                logger.info("AH user token refreshed successfully")
                if self._on_tokens_updated:
                    self._on_tokens_updated(
                        self._user_token, self._user_refresh_token
                    )
                return True
        except Exception as e:
            logger.error("Failed to refresh AH user token: %s", e)
            return False

    async def search_products(self, query: str, size: int = 10) -> list[dict]:
        token = await self._get_anonymous_token()
        headers = {**DEFAULT_HEADERS, "Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient() as client:
            logger.debug("Searching AH products: %s", query)
            resp = await client.get(
                AH_SEARCH_URL,
                headers=headers,
                params={"query": query, "sortOn": "RELEVANCE", "size": size},
            )
            if resp.status_code == 401:
                logger.info("Anonymous token expired, refreshing")
                self._anonymous_token = None
                token = await self._get_anonymous_token()
                headers["Authorization"] = f"Bearer {token}"
                resp = await client.get(
                    AH_SEARCH_URL,
                    headers=headers,
                    params={"query": query, "sortOn": "RELEVANCE", "size": size},
                )
            resp.raise_for_status()
            data = resp.json()

        products = []
        for product in data.get("products", []):
            products.append(
                {
                    "id": product.get("webshopId"),
                    "name": product.get("title", ""),
                    "unit_size": product.get("salesUnitSize", ""),
                    "price": str(product.get("priceBeforeBonus", product.get("currentPrice", ""))),
                    "image_url": (
                        product.get("images", [{}])[0].get("url", "")
                        if product.get("images")
                        else ""
                    ),
                    "brand": product.get("brand", ""),
                }
            )
        logger.debug("Found %d AH products for '%s'", len(products), query)
        return products

    async def graphql(self, query: str, variables: dict) -> dict:
        """Run a GraphQL query with the user token when set, else an anonymous one."""
        async with httpx.AsyncClient(timeout=30) as client:
            for attempt in (1, 2):
                token = self._user_token or await self._get_anonymous_token()
                headers = {
                    **DEFAULT_HEADERS,
                    "x-client-name": "appie-ios",
                    "x-client-version": "9.28",
                    "Authorization": f"Bearer {token}",
                }
                resp = await client.post(
                    AH_GRAPHQL_URL, headers=headers, json={"query": query, "variables": variables}
                )
                if resp.status_code == 401 and attempt == 1 and not self._user_token:
                    self._anonymous_token = None
                    continue
                resp.raise_for_status()
                body = resp.json()
                if body.get("errors"):
                    raise ValueError("AH: " + "; ".join(e.get("message", "") for e in body["errors"]))
                return body["data"]
        raise RuntimeError("unreachable")

    async def search_recipes(self, text: str, size: int = 12) -> list[dict]:
        query = """query RecipeSearch($query: RecipeSearchParams!) {
  recipeSearch(query: $query) {
    result { id title slug time { cook oven wait } serving { number type } }
  }
}"""
        data = await self.graphql(query, {"query": {"searchText": text, "size": size}})
        return [
            {
                "id": r["id"],
                "title": html.unescape(r.get("title", "")),
                "slug": r.get("slug", ""),
                "url": AH_RECIPE_URL.format(id=r["id"], slug=r.get("slug", "")),
                "servings": _servings(r.get("serving")),
            }
            for r in data["recipeSearch"]["result"]
        ]

    async def get_recipe(self, recipe_id: int) -> dict:
        query = """query Recipe($id: Int!) {
  recipe(id: $id) {
    id title description cookTime
    servings { number type }
    ingredients { text name { singular } }
    preparation { steps }
  }
}"""
        data = await self.graphql(query, {"id": recipe_id})
        return data["recipe"]

    async def add_to_cart(self, items: list[dict]) -> dict:
        if not self._user_token:
            raise ValueError(
                "AH token niet ingesteld. Ga naar Instellingen."
            )
        headers = {
            **DEFAULT_HEADERS,
            "Authorization": f"Bearer {self._user_token}",
        }
        cart_items = [
            {
                "originCode": "PRD",
                "productId": item["product_id"],
                "quantity": item.get("quantity", 1),
                "type": "SHOPPABLE",
            }
            for item in items
        ]
        async with httpx.AsyncClient() as client:
            logger.info("Adding %d items to AH cart", len(cart_items))
            resp = await client.patch(
                AH_CART_URL,
                headers=headers,
                json={"items": cart_items},
            )
            # If 401, try refreshing the token
            if resp.status_code == 401:
                logger.info("AH user token expired, attempting refresh")
                refreshed = await self._refresh_user_token()
                if not refreshed:
                    raise ValueError(
                        "AH token verlopen en refresh mislukt. "
                        "Voer een nieuw refresh token in via Instellingen."
                    )
                headers["Authorization"] = f"Bearer {self._user_token}"
                resp = await client.patch(
                    AH_CART_URL,
                    headers=headers,
                    json={"items": cart_items},
                )
            resp.raise_for_status()
            logger.info("Successfully added items to AH cart")
            return resp.json()


def _servings(serving: dict | None) -> str:
    if not serving or not serving.get("number"):
        return ""
    return f"{serving['number']} {serving.get('type') or 'personen'}"


NO_BUY = {"water", "kraanwater", "kokend water", "ijswater"}


def convert_ah_recipe(r: dict) -> dict:
    """Map an Allerhande recipe (GraphQL `recipe`) to our recipe fields."""
    ingredients = []
    for ing in r.get("ingredients") or []:
        text = html.unescape(ing.get("text") or "").strip()
        if not text:
            continue
        search = html.unescape(((ing.get("name") or {}).get("singular")) or "").strip()
        skip = not search or search.lower() in NO_BUY
        ingredients.append({"text": text, "search": search, "skip": skip, "quantity": 1, "product": None})
    steps = [html.unescape(x).strip() for x in ((r.get("preparation") or {}).get("steps") or [])]
    cook = r.get("cookTime")
    return {
        "name": html.unescape(r.get("title") or "").strip(),
        "description": html.unescape(r.get("description") or "").strip(),
        "servings": _servings(r.get("servings")),
        "total_time": f"{cook} minuten" if cook else "",
        "ingredients": ingredients,
        "instructions": [x for x in steps if x],
    }


ah_client = AHClient()

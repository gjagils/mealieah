import httpx

from app.logging_config import logger

AH_AUTH_URL = "https://api.ah.nl/mobile-auth/v1/auth/token/anonymous"
AH_SEARCH_URL = "https://api.ah.nl/mobile-services/product/search/v2"
AH_CART_URL = "https://api.ah.nl/mobile-services/shoppinglist/v2/items"

DEFAULT_HEADERS = {
    "User-Agent": "Appie/8.22.3",
    "Content-Type": "application/json",
    "x-application": "AHWEBSHOP",
}


class AHClient:
    def __init__(self) -> None:
        self._anonymous_token: str | None = None
        self._user_token: str | None = None

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

    def set_user_token(self, token: str) -> None:
        self._user_token = token

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

    async def add_to_cart(self, items: list[dict]) -> dict:
        if not self._user_token:
            raise ValueError(
                "AH user token required for cart operations. "
                "Set your token in Settings."
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
            resp.raise_for_status()
            logger.info("Successfully added items to AH cart")
            return resp.json()


ah_client = AHClient()

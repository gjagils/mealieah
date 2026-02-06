import httpx
import pytest
import respx

from app.clients.ah import AH_AUTH_URL, AH_CART_URL, AH_REFRESH_URL, AH_SEARCH_URL, AHClient


@pytest.fixture
def ah():
    return AHClient()


@respx.mock
@pytest.mark.asyncio
async def test_get_anonymous_token(ah):
    respx.post(AH_AUTH_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "anon-tok-123"})
    )
    token = await ah._get_anonymous_token()
    assert token == "anon-tok-123"


@respx.mock
@pytest.mark.asyncio
async def test_search_products(ah):
    respx.post(AH_AUTH_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "anon-tok"})
    )
    respx.get(AH_SEARCH_URL).mock(
        return_value=httpx.Response(200, json={
            "products": [
                {
                    "webshopId": 12345,
                    "title": "AH Kipfilet",
                    "salesUnitSize": "300 g",
                    "currentPrice": 4.99,
                    "brand": "AH",
                    "images": [{"url": "https://img.ah.nl/12345.jpg"}],
                },
                {
                    "webshopId": 67890,
                    "title": "AH Biologisch kipfilet",
                    "salesUnitSize": "250 g",
                    "priceBeforeBonus": 6.49,
                    "currentPrice": 5.49,
                    "brand": "AH Biologisch",
                    "images": [],
                },
            ]
        })
    )

    products = await ah.search_products("kipfilet")

    assert len(products) == 2
    assert products[0]["id"] == 12345
    assert products[0]["name"] == "AH Kipfilet"
    assert products[0]["unit_size"] == "300 g"
    assert products[0]["image_url"] == "https://img.ah.nl/12345.jpg"
    assert products[1]["price"] == "6.49"  # priceBeforeBonus preferred
    assert products[1]["image_url"] == ""  # no images


@respx.mock
@pytest.mark.asyncio
async def test_search_products_token_refresh(ah):
    respx.post(AH_AUTH_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "new-tok"})
    )
    # First call returns 401 (expired token), then succeeds
    search_route = respx.get(AH_SEARCH_URL)
    search_route.side_effect = [
        httpx.Response(401),
        httpx.Response(200, json={"products": []}),
    ]

    # Force an existing (expired) token
    ah._anonymous_token = "expired-tok"
    products = await ah.search_products("melk")

    assert products == []
    assert ah._anonymous_token == "new-tok"


@respx.mock
@pytest.mark.asyncio
async def test_search_empty_results(ah):
    respx.post(AH_AUTH_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "tok"})
    )
    respx.get(AH_SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"products": []})
    )

    products = await ah.search_products("ietswatnietbestaat")
    assert products == []


@respx.mock
@pytest.mark.asyncio
async def test_add_to_cart(ah):
    ah.set_user_token("user-tok-abc")
    respx.patch(AH_CART_URL).mock(
        return_value=httpx.Response(200, json={"success": True})
    )

    result = await ah.add_to_cart([
        {"product_id": 12345, "quantity": 2},
        {"product_id": 67890, "quantity": 1},
    ])

    assert result == {"success": True}
    # Verify the request body
    req = respx.calls.last.request
    import json
    body = json.loads(req.content)
    assert len(body["items"]) == 2
    assert body["items"][0]["productId"] == 12345
    assert body["items"][0]["quantity"] == 2


@pytest.mark.asyncio
async def test_add_to_cart_no_token(ah):
    with pytest.raises(ValueError, match="AH token niet ingesteld"):
        await ah.add_to_cart([{"product_id": 1, "quantity": 1}])


@respx.mock
@pytest.mark.asyncio
async def test_refresh_user_token(ah):
    ah._user_refresh_token = "old-refresh"
    respx.post(AH_REFRESH_URL).mock(
        return_value=httpx.Response(200, json={
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_in": 604798,
        })
    )

    result = await ah._refresh_user_token()
    assert result is True
    assert ah._user_token == "new-access"
    assert ah._user_refresh_token == "new-refresh"


@respx.mock
@pytest.mark.asyncio
async def test_refresh_callback_called(ah):
    saved = {}
    def on_update(access, refresh):
        saved["access"] = access
        saved["refresh"] = refresh

    ah.set_user_tokens("old-access", "old-refresh", on_tokens_updated=on_update)
    respx.post(AH_REFRESH_URL).mock(
        return_value=httpx.Response(200, json={
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_in": 604798,
        })
    )

    await ah._refresh_user_token()
    assert saved["access"] == "new-access"
    assert saved["refresh"] == "new-refresh"


@respx.mock
@pytest.mark.asyncio
async def test_cart_auto_refresh_on_401(ah):
    ah.set_user_tokens("expired-access", "valid-refresh")
    respx.post(AH_REFRESH_URL).mock(
        return_value=httpx.Response(200, json={
            "access_token": "fresh-access",
            "refresh_token": "fresh-refresh",
            "expires_in": 604798,
        })
    )
    cart_route = respx.patch(AH_CART_URL)
    cart_route.side_effect = [
        httpx.Response(401),
        httpx.Response(200, json={"success": True}),
    ]

    result = await ah.add_to_cart([{"product_id": 1, "quantity": 1}])
    assert result == {"success": True}
    assert ah._user_token == "fresh-access"

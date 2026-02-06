import httpx
import pytest
import respx

from app.clients.mealie import MealieClient

BASE = "http://mealie-test:9000"


@pytest.fixture
def mealie():
    return MealieClient(base_url=BASE, api_token="test-token")


@respx.mock
@pytest.mark.asyncio
async def test_get_recipes(mealie):
    respx.get(f"{BASE}/api/recipes").mock(
        return_value=httpx.Response(200, json={
            "items": [
                {"slug": "pasta-bolognese", "name": "Pasta Bolognese"},
                {"slug": "caesar-salad", "name": "Caesar Salad"},
            ],
            "total": 2,
        })
    )

    data = await mealie.get_recipes()
    assert len(data["items"]) == 2
    assert data["items"][0]["slug"] == "pasta-bolognese"


@respx.mock
@pytest.mark.asyncio
async def test_get_recipe(mealie):
    respx.get(f"{BASE}/api/recipes/pasta-bolognese").mock(
        return_value=httpx.Response(200, json={
            "slug": "pasta-bolognese",
            "name": "Pasta Bolognese",
            "recipeIngredient": [
                {
                    "referenceId": "ing-001",
                    "quantity": 500,
                    "unit": {"name": "g"},
                    "food": {"name": "gehakt"},
                    "note": "",
                    "display": "500 g gehakt",
                },
                {
                    "referenceId": "ing-002",
                    "quantity": 1,
                    "unit": {"name": "blik"},
                    "food": {"name": "tomaten"},
                    "note": "gepeld",
                    "display": "1 blik tomaten, gepeld",
                },
            ],
        })
    )

    recipe = await mealie.get_recipe("pasta-bolognese")
    assert recipe["name"] == "Pasta Bolognese"
    assert len(recipe["recipeIngredient"]) == 2
    assert recipe["recipeIngredient"][0]["referenceId"] == "ing-001"


@respx.mock
@pytest.mark.asyncio
async def test_get_mealplans(mealie):
    respx.get(f"{BASE}/api/households/mealplans").mock(
        return_value=httpx.Response(200, json={
            "items": [
                {
                    "date": "2026-02-02",
                    "entryType": "dinner",
                    "recipe": {"slug": "pasta-bolognese", "name": "Pasta Bolognese"},
                },
                {
                    "date": "2026-02-03",
                    "entryType": "dinner",
                    "recipe": {"slug": "caesar-salad", "name": "Caesar Salad"},
                },
            ]
        })
    )

    plans = await mealie.get_mealplans("2026-02-02", "2026-02-08")
    assert len(plans) == 2
    assert plans[0]["recipe"]["slug"] == "pasta-bolognese"


@respx.mock
@pytest.mark.asyncio
async def test_get_mealplans_list_response(mealie):
    """Mealie might return a plain list instead of {items: [...]}."""
    respx.get(f"{BASE}/api/households/mealplans").mock(
        return_value=httpx.Response(200, json=[
            {"date": "2026-02-02", "entryType": "dinner", "recipe": {"slug": "test"}},
        ])
    )

    plans = await mealie.get_mealplans("2026-02-02", "2026-02-08")
    assert len(plans) == 1


@respx.mock
@pytest.mark.asyncio
async def test_health_check_ok(mealie):
    respx.get(f"{BASE}/api/app/about").mock(
        return_value=httpx.Response(200, json={"version": "2.0.0"})
    )

    result = await mealie.health_check()
    assert result is True


@respx.mock
@pytest.mark.asyncio
async def test_health_check_fail(mealie):
    respx.get(f"{BASE}/api/app/about").mock(
        return_value=httpx.Response(500)
    )

    result = await mealie.health_check()
    assert result is False


@respx.mock
@pytest.mark.asyncio
async def test_headers_include_auth(mealie):
    respx.get(f"{BASE}/api/recipes").mock(
        return_value=httpx.Response(200, json={"items": []})
    )

    await mealie.get_recipes()
    req = respx.calls.last.request
    assert req.headers["Authorization"] == "Bearer test-token"

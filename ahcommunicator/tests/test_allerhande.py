from fastapi.testclient import TestClient

from app.api import routes
from app.clients.ah import convert_ah_recipe
from app.main import app
from app.models import Recipe
from tests.test_routes import db  # noqa: F401  (fixture)

RAW = {
    "id": 42, "title": "Kip &amp; rijst", "description": "Lekker", "cookTime": 30,
    "servings": {"number": 4, "type": "personen"},
    "ingredients": [
        {"text": "400 g kipfilet", "name": {"singular": "kipfilet"}},
        {"text": "500 ml water", "name": {"singular": "water"}},
        {"text": "1&#189; ui", "name": None},
    ],
    "preparation": {"steps": ["Bak de kip.", ""]},
}


def test_convert_ah_recipe():
    out = convert_ah_recipe(RAW)
    assert out["name"] == "Kip & rijst"
    assert out["servings"] == "4 personen" and out["total_time"] == "30 minuten"
    assert [i["skip"] for i in out["ingredients"]] == [False, True, True]
    assert out["ingredients"][2]["text"] == "1½ ui"
    assert out["instructions"] == ["Bak de kip."]


def test_search_and_add_is_idempotent(db, monkeypatch):
    async def fake_search(text, size=12):
        return [{"id": 42, "title": "Kip & rijst", "slug": "kip", "url": "https://x", "servings": "4 personen"}]

    async def fake_get(recipe_id):
        return RAW

    monkeypatch.setattr(routes.ah_client, "search_recipes", fake_search)
    monkeypatch.setattr(routes.ah_client, "get_recipe", fake_get)
    client = TestClient(app)
    assert "Kip &amp; rijst" in client.get("/allerhande?q=kip").text
    first = client.post("/api/allerhande/add", data={"recipe_id": 42}).json()
    second = client.post("/api/allerhande/add", data={"recipe_id": 42}).json()
    assert first["ok"] and first["id"] == second["id"]
    assert db.query(Recipe).count() == 1
    assert "In je bibliotheek" in client.get("/allerhande?q=kip").text

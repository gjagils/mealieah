import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import routes
from app.database import Base, get_db
from app.main import app
from app.models import Recipe


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    app.dependency_overrides[get_db] = lambda: session
    yield session
    app.dependency_overrides.clear()


def _recipe(db, name, ingredients):
    r = Recipe(name=name)
    r.ingredients = ingredients
    r.instructions = ["Kook."]
    db.add(r)
    db.commit()
    return r


def _ing(text, pid=None, qty=1, skip=False):
    product = {"id": pid, "name": f"p{pid}", "price": "1.00", "unit_size": "1 st"} if pid else None
    return {"text": text, "search": text, "skip": skip, "quantity": qty, "product": product}


def test_aggregate_cart_sums_same_product_and_reports_unmatched(db):
    a = _recipe(db, "A", [_ing("ui", 1), _ing("rijst", 2, qty=2), _ing("water", skip=True)])
    b = _recipe(db, "B", [_ing("ui", 1, qty=3), _ing("saffraan")])
    cart, unmatched = routes.aggregate_cart([a, b])
    assert {i["product_id"]: i["quantity"] for i in cart} == {1: 4, 2: 2}
    assert unmatched == ["saffraan"]


def test_index_and_detail_render(db):
    r = _recipe(db, "Pasta", [_ing("pasta", 5)])
    client = TestClient(app)
    assert "Pasta" in client.get("/").text
    assert "Pasta" in client.get(f"/recipe/{r.id}").text
    assert client.get("/recipe/999").status_code == 404
    assert client.get("/weekmenu").status_code == 200
    assert client.get("/settings").status_code == 200


def test_import_without_input_is_rejected(db):
    resp = TestClient(app).post("/api/import", data={})
    assert resp.status_code == 400


def test_weekmenu_roundtrip_and_delete(db):
    r = _recipe(db, "Pasta", [_ing("pasta", 5)])
    client = TestClient(app)
    assert client.post("/api/weekmenu", json={"days": {"0": [r.id], "2": [r.id]}}).json()["ok"]
    assert 'value="%d" selected' % r.id in client.get("/weekmenu").text
    assert client.post(f"/recipe/{r.id}/delete", follow_redirects=False).status_code == 303
    assert db.get(Recipe, r.id) is None


def test_mealie_import_is_idempotent(db, monkeypatch):
    class FakeMealie:
        def __init__(self, url, token):
            pass

        async def list_slugs(self, client):
            return ["pasta", "leeg"]

        async def get_recipe(self, client, slug):
            if slug == "leeg":
                return {"id": "2", "name": "Leeg", "recipeIngredient": []}
            return {"id": "1", "name": "Pasta", "recipeYield": "4",
                    "recipeIngredient": [{"display": "250 g pasta", "food": {"name": "pasta"}}],
                    "recipeInstructions": [{"text": "Kook."}]}

        async def get_image(self, client, recipe_id):
            return None

    monkeypatch.setattr(routes, "MealieClient", FakeMealie)
    client = TestClient(app)
    first = client.post("/settings/mealie", data={"mealie_url": "http://mealie:9000", "mealie_token": "t"})
    assert "1 recepten geïmporteerd, 0 stonden er al, 1 mislukt" in first.text
    second = client.post("/settings/mealie", data={})
    assert "0 recepten geïmporteerd, 1 stonden er al" in second.text
    assert db.query(Recipe).count() == 1
    assert client.post("/settings/mealie", data={"mealie_url": "ftp://x"}).status_code == 200

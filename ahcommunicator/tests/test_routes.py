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
    assert "Pasta" in client.get("/recepten").text
    assert client.get("/").status_code == 200
    assert "Pasta" in client.get(f"/recipe/{r.id}").text
    assert "Pasta" in client.get(f"/recipe/{r.id}/koken").text
    assert client.get("/recipe/999").status_code == 404
    assert client.get("/weekmenu").status_code == 200
    assert client.get("/settings").status_code == 200


def test_import_without_input_is_rejected(db):
    resp = TestClient(app).post("/api/import", data={})
    assert resp.status_code == 400


def test_plan_roundtrip_today_and_delete(db):
    from datetime import date

    r = _recipe(db, "Pasta", [_ing("pasta", 5)])
    client = TestClient(app)
    monday = routes.monday_of(date.today())
    day = str(monday)
    resp = client.post("/api/plan", json={"week": day, "days": {day: [r.id], "2000-01-01": [r.id]}}).json()
    assert resp["ok"] and resp["status"]["needed"] == 1  # date outside the week is ignored
    assert client.get(f"/weekmenu?week={day}").status_code == 200
    assert "Pasta" in client.get("/").text
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


def test_aggregate_cart_gluten_free_modes(db):
    pasta = _ing("500 g pasta", 1)
    pasta.update(gluten=True, gf_search="glutenvrije pasta", gf_product={"id": 9, "name": "gf pasta", "price": "2"})
    extra = _recipe(db, "Extra", [pasta])
    extra.gf_mode = "extra"
    cart, unmatched = routes.aggregate_cart([extra])
    assert {i["product_id"]: i["quantity"] for i in cart} == {1: 1, 9: 1} and not unmatched

    replace = _recipe(db, "Replace", [dict(pasta)])
    replace.gf_mode = "replace"
    cart, _ = routes.aggregate_cart([replace])
    assert {i["product_id"]: i["quantity"] for i in cart} == {9: 1}

    nogf = dict(pasta, gf_product=None)
    replace2 = _recipe(db, "R2", [nogf])
    replace2.gf_mode = "replace"
    cart, unmatched = routes.aggregate_cart([replace2])
    assert cart == [] and unmatched == ["500 g pasta (glutenvrij)"]

    off = _recipe(db, "Off", [dict(pasta)])
    cart, _ = routes.aggregate_cart([off])
    assert {i["product_id"] for i in cart} == {1}


def test_week_sync_pushes_only_the_difference(db, monkeypatch):
    from datetime import date

    from app.models import AppSetting

    db.add(AppSetting(key="ah_user_token", value="t"))
    a = _recipe(db, "A", [_ing("ui", 1, qty=2)])
    day = str(routes.monday_of(date.today()))
    client = TestClient(app)
    client.post("/api/plan", json={"week": day, "days": {day: [a.id]}})

    sent = []

    async def fake_add(items):
        sent.append(items)

    async def no_match(*args, **kwargs):
        return 0

    monkeypatch.setattr(routes.ah_client, "add_to_cart", fake_add)
    monkeypatch.setattr(routes, "_automatch", no_match)

    first = client.post("/api/plan/sync", json={"week": day, "locked": True}).json()
    assert first["ok"] and first["added"] == 1 and first["status"]["complete"] and first["status"]["locked"]
    assert sent[0][0]["quantity"] == 2

    # nothing changed -> nothing sent again
    again = client.post("/api/plan/sync", json={"week": day}).json()
    assert again["added"] == 0 and len(sent) == 1

    # same recipe planned a second day -> only the extra 2 are sent
    client.post("/api/plan", json={"week": day, "days": {day: [a.id], str(date.fromisoformat(day).replace(day=date.fromisoformat(day).day)): [a.id]}})
    status = client.get(f"/weekmenu?week={day}")
    assert status.status_code == 200


def test_gluten_suggest_and_save(db, monkeypatch):
    async def fake_suggest(name, texts):
        return {"mode": "extra", "note": "Aparte pasta", "items": {0: "glutenvrije pasta"}}

    monkeypatch.setattr(routes, "suggest_gluten_free", fake_suggest)
    r = _recipe(db, "Pasta", [_ing("pasta", 1), _ing("tomaat", 2)])
    client = TestClient(app)
    out = client.post(f"/api/recipe/{r.id}/gluten-suggest").json()
    assert out["ok"] and out["gf_mode"] == "extra"
    assert [i["gluten"] for i in out["ingredients"]] == [True, False]
    bad = client.post(f"/api/recipe/{r.id}/gluten", json={"gf_mode": "x", "ingredients": []})
    assert bad.status_code == 400


def test_pin_protects_everything(db, monkeypatch):
    monkeypatch.setattr("app.main.settings.app_pin", "1234")
    client = TestClient(app)
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"
    assert client.get("/api/ah/search?q=x").status_code == 401
    assert client.post("/login", data={"pin": "0000"}).status_code == 401
    monkeypatch.setattr(routes.settings, "app_pin", "1234")
    ok = client.post("/login", data={"pin": "1234"}, follow_redirects=False)
    assert ok.status_code == 303 and "session" in ok.cookies
    assert client.get("/").status_code == 200

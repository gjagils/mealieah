import asyncio
import hmac
import io
import os
from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from PIL import Image, ImageOps
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clients.ah import ah_client, convert_ah_recipe
from app.clients.extractor import extract_recipe, fetch_url, suggest_gluten_free
from app.clients.mealie import MealieClient, convert_recipe
from app.config import settings
from app.database import get_db
from app.logging_config import logger
from app.models import AppSetting, CartPush, PlanEntry, Recipe

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

IMAGE_DIR = os.path.join("data", "images")
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
DAYS = ["Maandag", "Dinsdag", "Woensdag", "Donderdag", "Vrijdag", "Zaterdag", "Zondag"]
MONTHS = ["jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep", "okt", "nov", "dec"]


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def parse_week(week: str | None) -> date:
    try:
        return monday_of(date.fromisoformat(week)) if week else monday_of(date.today())
    except ValueError:
        return monday_of(date.today())


def day_label(d: date) -> str:
    return f"{DAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]}"


def _get_setting(db: Session, key: str) -> str:
    row = db.execute(select(AppSetting).where(AppSetting.key == key)).scalar_one_or_none()
    return row.value if row else ""


def _set_setting(db: Session, key: str, value: str) -> None:
    row = db.execute(select(AppSetting).where(AppSetting.key == key)).scalar_one_or_none()
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=key, value=value))
    db.commit()


def _get_recipe(db: Session, recipe_id: int) -> Recipe:
    recipe = db.get(Recipe, recipe_id)
    if not recipe:
        raise HTTPException(404, "Recept niet gevonden")
    return recipe


def _save_food_photo(recipe_id: int, data: bytes) -> bool:
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.thumbnail((1600, 1600), Image.LANCZOS)
        os.makedirs(IMAGE_DIR, exist_ok=True)
        img.save(os.path.join(IMAGE_DIR, f"{recipe_id}.jpg"), format="JPEG", quality=85)
        return True
    except Exception as e:
        logger.warning("Could not save food photo: %s", e)
        return False


# ── Pages ──────────────────────────────────────────────────────────────


@router.get("/", response_class=HTMLResponse)
async def today_page(request: Request, db: Session = Depends(get_db)):
    """Gezinsweergave: wat staat er vandaag en deze week op het menu."""
    monday = monday_of(date.today())
    entries = db.execute(
        select(PlanEntry).where(PlanEntry.date >= str(monday), PlanEntry.date <= str(monday + timedelta(days=6)))
    ).scalars().all()
    recipes = {r.id: r for r in db.execute(select(Recipe).where(Recipe.id.in_({e.recipe_id for e in entries}))).scalars()}
    days = []
    for i in range(7):
        d = monday + timedelta(days=i)
        planned = [recipes[e.recipe_id] for e in entries if e.date == str(d) and e.recipe_id in recipes]
        days.append({"label": day_label(d), "recipes": planned, "today": d == date.today()})
    return templates.TemplateResponse(request, "today.html", {"days": days})


@router.get("/recepten", response_class=HTMLResponse)
async def recipes_page(request: Request, db: Session = Depends(get_db)):
    recipes = db.execute(select(Recipe).order_by(Recipe.name)).scalars().all()
    return templates.TemplateResponse(
        request, "recipes.html",
        {"recipes": recipes, "has_api_key": bool(settings.anthropic_api_key)},
    )


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": ""})


@router.post("/login")
async def login(request: Request, pin: str = Form("")):
    from app.main import session_token

    if settings.app_pin and hmac.compare_digest(pin.strip(), settings.app_pin):
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie("session", session_token(), max_age=60 * 60 * 24 * 365,
                        httponly=True, samesite="lax")
        return resp
    await asyncio.sleep(1)  # slow down guessing
    return templates.TemplateResponse(request, "login.html", {"error": "Pincode klopt niet."}, status_code=401)


@router.get("/recipe/{recipe_id}", response_class=HTMLResponse)
async def recipe_detail(request: Request, recipe_id: int, db: Session = Depends(get_db)):
    recipe = _get_recipe(db, recipe_id)
    return templates.TemplateResponse(
        request, "recipe_detail.html", {"recipe": recipe,
            "ingredients": recipe.ingredients,
            "has_token": bool(_get_setting(db, "ah_refresh_token") or _get_setting(db, "ah_user_token")),
        },
    )


@router.get("/recipe/{recipe_id}/koken", response_class=HTMLResponse)
async def cook_mode(request: Request, recipe_id: int, db: Session = Depends(get_db)):
    recipe = _get_recipe(db, recipe_id)
    return templates.TemplateResponse(request, "cook.html", {"recipe": recipe})


class GlutenPayload(BaseModel):
    gf_mode: str
    gf_note: str = ""
    ingredients: list[dict]


@router.post("/api/recipe/{recipe_id}/gluten")
async def save_gluten(recipe_id: int, payload: GlutenPayload, db: Session = Depends(get_db)):
    if payload.gf_mode not in ("none", "extra", "replace"):
        return JSONResponse({"ok": False, "error": "Ongeldige glutenvrij-modus"}, status_code=400)
    recipe = _get_recipe(db, recipe_id)
    recipe.gf_mode = payload.gf_mode
    recipe.gf_note = payload.gf_note
    recipe.ingredients = payload.ingredients
    db.commit()
    return {"ok": True}


@router.post("/api/recipe/{recipe_id}/gluten-suggest")
async def gluten_suggest(recipe_id: int, db: Session = Depends(get_db)):
    """Let Claude mark gluten ingredients and propose a gluten-free alternative per ingredient."""
    recipe = _get_recipe(db, recipe_id)
    ingredients = recipe.ingredients
    try:
        result = await suggest_gluten_free(recipe.name, [i["text"] for i in ingredients])
    except Exception as e:
        logger.error("Gluten-free suggestion failed: %s", e)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    for idx, ing in enumerate(ingredients):
        ing["gluten"] = idx in result["items"]
        ing["gf_search"] = result["items"].get(idx, "")
        ing["gf_product"] = None
    recipe.ingredients = ingredients
    recipe.gf_mode = result["mode"] if result["items"] else "none"
    recipe.gf_note = result["note"]
    db.commit()
    return {"ok": True, "gf_mode": recipe.gf_mode, "gf_note": recipe.gf_note, "ingredients": ingredients}


@router.get("/image/{recipe_id}")
async def recipe_image(recipe_id: int):
    path = os.path.join(IMAGE_DIR, f"{int(recipe_id)}.jpg")
    if not os.path.exists(path):
        raise HTTPException(404)
    return FileResponse(path, headers={"Cache-Control": "public, max-age=86400"})


# ── Import (URL / tekst / foto's) ──────────────────────────────────────


@router.post("/api/import")
async def import_recipe(
    url: str = Form(""),
    text: str = Form(""),
    images: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
):
    url, text = url.strip(), text.strip()
    image_list: list[tuple[bytes, str]] = []
    for img in images:
        if not img.filename:
            continue
        if img.content_type not in ALLOWED_IMAGE_TYPES:
            return JSONResponse({"ok": False, "error": f"Ongeldig bestandstype: {img.content_type}"}, status_code=400)
        data = await img.read()
        if len(data) > 20 * 1024 * 1024:
            return JSONResponse({"ok": False, "error": "Afbeelding is te groot (max 20MB)."}, status_code=400)
        image_list.append((data, img.content_type))

    if not (url or text or image_list):
        return JSONResponse({"ok": False, "error": "Geef een URL, tekst of foto's op."}, status_code=400)

    try:
        image_url = ""
        if url:
            page_text, image_url = await fetch_url(url)
            text = f"{text}\n\n{page_text}" if text else page_text
        raw = await extract_recipe(text=text or None, images=image_list or None)
    except httpx.HTTPError as e:
        logger.error("Fetching %s failed: %s", url, e)
        return JSONResponse({"ok": False, "error": f"Website ophalen mislukt: {e}"}, status_code=502)
    except Exception as e:
        logger.error("Recipe import failed: %s", e)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    recipe = Recipe(
        name=raw["name"], description=raw["description"], servings=raw["servings"],
        total_time=raw["total_time"], source_url=url, image_url=image_url,
    )
    recipe.ingredients = raw["ingredients"]
    recipe.instructions = raw["instructions"]
    db.add(recipe)
    db.commit()

    idx = raw.get("food_photo_index")
    if isinstance(idx, int) and 0 <= idx < len(image_list):
        if _save_food_photo(recipe.id, image_list[idx][0]):
            recipe.image_url = f"/image/{recipe.id}"
            db.commit()

    logger.info("Imported recipe %s (id=%s)", recipe.name, recipe.id)
    return {"ok": True, "id": recipe.id}


@router.post("/recipe/{recipe_id}/delete")
async def delete_recipe(recipe_id: int, db: Session = Depends(get_db)):
    recipe = _get_recipe(db, recipe_id)
    for entry in db.execute(select(PlanEntry).where(PlanEntry.recipe_id == recipe_id)).scalars():
        db.delete(entry)
    db.delete(recipe)
    db.commit()
    path = os.path.join(IMAGE_DIR, f"{recipe_id}.jpg")
    if os.path.exists(path):
        os.remove(path)
    return RedirectResponse("/recepten", status_code=303)


# ── AH producten ───────────────────────────────────────────────────────


@router.get("/api/ah/search")
async def ah_search(q: str = Query(..., min_length=1)):
    try:
        return {"products": await ah_client.search_products(q, size=8)}
    except Exception as e:
        logger.error("AH search failed: %s", e)
        return {"products": [], "error": str(e)}


class IngredientsPayload(BaseModel):
    ingredients: list[dict]


@router.post("/api/recipe/{recipe_id}/ingredients")
async def save_ingredients(recipe_id: int, payload: IngredientsPayload, db: Session = Depends(get_db)):
    recipe = _get_recipe(db, recipe_id)
    recipe.ingredients = payload.ingredients
    db.commit()
    return {"ok": True}


async def _automatch(ingredients: list[dict], gluten_free: bool = False) -> int:
    """Fill in the top AH search hit for every ingredient without a product (and gf alternative)."""
    sem = asyncio.Semaphore(4)

    async def find(term: str) -> dict | None:
        async with sem:
            try:
                products = await ah_client.search_products(term, size=1)
            except Exception as e:
                logger.warning("AH search failed for %s: %s", term, e)
                return None
        return products[0] if products else None

    async def match(ing: dict) -> int:
        if ing.get("skip"):
            return 0
        n = 0
        if not ing.get("product") and ing.get("search"):
            product = await find(ing["search"])
            if product:
                ing["product"], n = product, n + 1
        if gluten_free and ing.get("gluten") and not ing.get("gf_product") and ing.get("gf_search"):
            product = await find(ing["gf_search"])
            if product:
                ing["gf_product"], n = product, n + 1
        return n

    return sum(await asyncio.gather(*(match(i) for i in ingredients)))


@router.post("/api/recipe/{recipe_id}/automatch")
async def automatch(recipe_id: int, db: Session = Depends(get_db)):
    recipe = _get_recipe(db, recipe_id)
    ingredients = recipe.ingredients
    matched = await _automatch(ingredients, recipe.gf_mode != "none")
    recipe.ingredients = ingredients
    db.commit()
    return {"ok": True, "matched": matched, "ingredients": ingredients}


# ── Weekmenu ───────────────────────────────────────────────────────────


def _locked_key(week_start: date) -> str:
    return f"locked:{week_start}"


def _week_recipes(db: Session, week_start: date) -> list[Recipe]:
    """Recipes planned in a week; a recipe planned twice appears twice (double quantities)."""
    entries = db.execute(
        select(PlanEntry).where(PlanEntry.date >= str(week_start), PlanEntry.date <= str(week_start + timedelta(days=6)))
    ).scalars().all()
    by_id = {r.id: r for r in db.execute(select(Recipe).where(Recipe.id.in_({e.recipe_id for e in entries}))).scalars()}
    return [by_id[e.recipe_id] for e in entries if e.recipe_id in by_id]


def _pushed(db: Session, week_start: date) -> dict[int, int]:
    rows = db.execute(select(CartPush).where(CartPush.week_start == str(week_start))).scalars()
    return {r.product_id: r.quantity for r in rows}


def week_status(db: Session, week_start: date) -> dict:
    """Compare what the week needs with what we already put on the AH list."""
    cart, unmatched = aggregate_cart(_week_recipes(db, week_start))
    pushed = _pushed(db, week_start)
    missing = [
        {"name": item["name"], "quantity": item["quantity"] - pushed.get(item["product_id"], 0)}
        for item in cart
        if item["quantity"] > pushed.get(item["product_id"], 0)
    ]
    return {
        "needed": len(cart),
        "missing": missing,
        "unmatched": unmatched,
        "complete": bool(cart) and not missing and not unmatched,
        "locked": _get_setting(db, _locked_key(week_start)) == "1",
    }


@router.get("/weekmenu", response_class=HTMLResponse)
async def weekmenu_page(request: Request, week: str | None = None, db: Session = Depends(get_db)):
    monday = parse_week(week)
    recipes = db.execute(select(Recipe).order_by(Recipe.name)).scalars().all()
    entries = db.execute(
        select(PlanEntry).where(PlanEntry.date >= str(monday), PlanEntry.date <= str(monday + timedelta(days=6)))
    ).scalars().all()
    plan: dict[str, list[int]] = {}
    for e in entries:
        plan.setdefault(e.date, []).append(e.recipe_id)
    days = [{"date": str(monday + timedelta(days=i)), "label": day_label(monday + timedelta(days=i))} for i in range(7)]
    return templates.TemplateResponse(
        request, "weekmenu.html",
        {
            "week": str(monday),
            "prev_week": str(monday - timedelta(days=7)),
            "next_week": str(monday + timedelta(days=7)),
            "recipes": [{"id": r.id, "name": r.name} for r in recipes],
            "days": days,
            "plan": plan,
            "status": week_status(db, monday),
            "has_token": bool(_get_setting(db, "ah_refresh_token") or _get_setting(db, "ah_user_token")),
        },
    )


class PlanPayload(BaseModel):
    week: str
    days: dict[str, list[int]]  # ISO date -> recipe ids


@router.post("/api/plan")
async def save_plan(payload: PlanPayload, db: Session = Depends(get_db)):
    monday = parse_week(payload.week)
    valid = {str(monday + timedelta(days=i)) for i in range(7)}
    for entry in db.execute(select(PlanEntry).where(PlanEntry.date.in_(valid))).scalars():
        db.delete(entry)
    for day, ids in payload.days.items():
        if day in valid:
            for rid in ids:
                if db.get(Recipe, rid):
                    db.add(PlanEntry(date=day, recipe_id=rid))
    db.commit()
    return {"ok": True, "status": week_status(db, monday)}


class WeekPayload(BaseModel):
    week: str
    locked: bool | None = None


@router.post("/api/plan/sync")
async def sync_week(payload: WeekPayload, db: Session = Depends(get_db)):
    """Put everything the week still needs on the AH list and report if the week is complete.

    With `locked` set, the week is also locked ("vastgezet") or unlocked.
    """
    monday = parse_week(payload.week)
    if payload.locked is False:
        _set_setting(db, _locked_key(monday), "0")
        return {"ok": True, "status": week_status(db, monday)}

    recipes = _week_recipes(db, monday)
    if not recipes:
        return {"ok": False, "error": "Er staan nog geen recepten in deze week."}
    for recipe in {r.id: r for r in recipes}.values():
        ingredients = recipe.ingredients
        if await _automatch(ingredients, recipe.gf_mode != "none"):
            recipe.ingredients = ingredients
    db.commit()

    cart, _ = aggregate_cart(recipes)
    pushed = _pushed(db, monday)
    delta = [
        {**item, "quantity": item["quantity"] - pushed.get(item["product_id"], 0)}
        for item in cart
        if item["quantity"] > pushed.get(item["product_id"], 0)
    ]
    added = 0
    if delta:
        access_token = _get_setting(db, "ah_user_token")
        refresh_token = _get_setting(db, "ah_refresh_token")
        if not access_token and not refresh_token:
            return {"ok": False, "error": "AH niet gekoppeld. Ga naar Instellingen."}

        def _save_tokens(new_access: str, new_refresh: str) -> None:
            _set_setting(db, "ah_user_token", new_access)
            _set_setting(db, "ah_refresh_token", new_refresh)

        ah_client.set_user_tokens(access_token, refresh_token, on_tokens_updated=_save_tokens)
        try:
            await ah_client.add_to_cart(delta)
        except Exception as e:
            logger.error("Failed to fill AH list for week %s: %s", monday, e)
            return {"ok": False, "error": str(e)}
        rows = {r.product_id: r for r in db.execute(select(CartPush).where(CartPush.week_start == str(monday))).scalars()}
        for item in delta:
            row = rows.get(item["product_id"])
            if row:
                row.quantity += item["quantity"]
            else:
                db.add(CartPush(week_start=str(monday), product_id=item["product_id"],
                                quantity=item["quantity"], name=item["name"]))
        db.commit()
        added = len(delta)

    if payload.locked:
        _set_setting(db, _locked_key(monday), "1")
    return {"ok": True, "added": added, "status": week_status(db, monday)}


# ── Boodschappenlijstje van AH ─────────────────────────────────────────


class CartPayload(BaseModel):
    recipe_ids: list[int]


def aggregate_cart(recipes: list[Recipe]) -> tuple[list[dict], list[str]]:
    """Merge ingredients over recipes. Returns (cart items, unmatched ingredient texts).

    Gluten-free handling per recipe (`gf_mode`) for ingredients marked `gluten`:
    "extra" buys the gluten-free product on top of the normal one (for 1 person),
    "replace" buys only the gluten-free product (for everyone).
    """
    cart: dict[int, dict] = {}
    unmatched: list[str] = []

    def add(product: dict, qty: int) -> None:
        if product["id"] in cart:
            cart[product["id"]]["quantity"] += qty
        else:
            cart[product["id"]] = {"product_id": product["id"], "quantity": qty, "name": product.get("name", "")}

    for recipe in recipes:
        for ing in recipe.ingredients:
            if ing.get("skip"):
                continue
            text = ing.get("text", "")
            qty = max(1, int(ing.get("quantity") or 1))
            product = ing.get("product")
            has_product = bool(product and product.get("id"))
            gf = ing.get("gf_product")
            has_gf = bool(gf and gf.get("id"))

            if ing.get("gluten") and recipe.gf_mode == "replace":
                if has_gf:
                    add(gf, qty)
                else:
                    unmatched.append(f"{text} (glutenvrij)")
                continue

            if has_product:
                add(product, qty)
            else:
                unmatched.append(text)
            if ing.get("gluten") and recipe.gf_mode == "extra":
                if has_gf:
                    add(gf, 1)
                else:
                    unmatched.append(f"{text} (glutenvrij)")
    return list(cart.values()), unmatched


@router.post("/api/cart/fill")
async def fill_cart(payload: CartPayload, db: Session = Depends(get_db)):
    recipes = [r for r in (db.get(Recipe, i) for i in dict.fromkeys(payload.recipe_ids)) if r]
    if not recipes:
        return {"ok": False, "error": "Geen recepten gekozen."}

    # Match whatever is still unmatched so a one-click flow works
    for recipe in recipes:
        ingredients = recipe.ingredients
        if await _automatch(ingredients, recipe.gf_mode != "none"):
            recipe.ingredients = ingredients
    db.commit()

    cart, unmatched = aggregate_cart(recipes)
    if not cart:
        return {"ok": False, "error": "Geen AH-producten gevonden voor deze ingrediënten."}

    access_token = _get_setting(db, "ah_user_token")
    refresh_token = _get_setting(db, "ah_refresh_token")
    if not access_token and not refresh_token:
        return {"ok": False, "error": "AH niet gekoppeld. Ga naar Instellingen."}

    def _save_tokens(new_access: str, new_refresh: str) -> None:
        _set_setting(db, "ah_user_token", new_access)
        _set_setting(db, "ah_refresh_token", new_refresh)

    ah_client.set_user_tokens(access_token, refresh_token, on_tokens_updated=_save_tokens)
    try:
        await ah_client.add_to_cart(cart)
    except Exception as e:
        logger.error("Failed to fill AH list: %s", e)
        return {"ok": False, "error": str(e)}
    return {"ok": True, "items_added": len(cart), "unmatched": unmatched}


# ── AH-recepten (Allerhande) ───────────────────────────────────────────


@router.get("/allerhande", response_class=HTMLResponse)
async def allerhande_page(request: Request, q: str = "", db: Session = Depends(get_db)):
    results, error = [], ""
    if q.strip():
        try:
            results = await ah_client.search_recipes(q.strip())
        except Exception as e:
            logger.error("Allerhande search failed: %s", e)
            error = f"Zoeken bij AH mislukt: {e}"
    saved = set(db.execute(select(Recipe.ah_recipe_id).where(Recipe.ah_recipe_id.is_not(None))).scalars())
    return templates.TemplateResponse(
        request, "allerhande.html", {"q": q, "results": results, "error": error, "saved": saved},
    )


@router.post("/api/allerhande/add")
async def allerhande_add(recipe_id: int = Form(...), db: Session = Depends(get_db)):
    existing = db.execute(select(Recipe).where(Recipe.ah_recipe_id == recipe_id)).scalar_one_or_none()
    if existing:
        return {"ok": True, "id": existing.id}
    try:
        data = convert_ah_recipe(await ah_client.get_recipe(recipe_id))
    except Exception as e:
        logger.error("Fetching Allerhande recipe %s failed: %s", recipe_id, e)
        return JSONResponse({"ok": False, "error": f"Recept ophalen mislukt: {e}"}, status_code=502)
    if not data["name"] or not data["ingredients"]:
        return JSONResponse({"ok": False, "error": "Dit recept bevat geen ingrediënten."}, status_code=422)

    recipe = Recipe(
        name=data["name"], description=data["description"], servings=data["servings"],
        total_time=data["total_time"], ah_recipe_id=recipe_id,
        source_url=f"https://www.ah.nl/allerhande/recept/R-R{recipe_id}",
    )
    recipe.ingredients = data["ingredients"]
    recipe.instructions = data["instructions"]
    db.add(recipe)
    db.commit()
    logger.info("Added Allerhande recipe %s (id=%s)", recipe.name, recipe.id)
    return {"ok": True, "id": recipe.id}


# ── Import uit Mealie ──────────────────────────────────────────────────


@router.post("/settings/mealie")
async def import_from_mealie(
    request: Request,
    mealie_url: str = Form(""),
    mealie_token: str = Form(""),
    db: Session = Depends(get_db),
):
    url = mealie_url.strip() or _get_setting(db, "mealie_url")
    token = mealie_token.strip() or _get_setting(db, "mealie_token")
    if not url:
        return _render_settings(request, db, mealie_error="Vul de URL van Mealie in.")
    if not url.startswith(("http://", "https://")):
        return _render_settings(request, db, mealie_error="De URL moet met http:// of https:// beginnen.")
    _set_setting(db, "mealie_url", url)
    _set_setting(db, "mealie_token", token)

    mealie = MealieClient(url, token)
    imported = skipped = failed = 0
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            slugs = await mealie.list_slugs(client)
            existing = set(db.execute(select(Recipe.mealie_slug).where(Recipe.mealie_slug.is_not(None))).scalars())
            for slug in slugs:
                if slug in existing:
                    skipped += 1
                    continue
                try:
                    full = await mealie.get_recipe(client, slug)
                    data = convert_recipe(full)
                    if not data["name"] or not data["ingredients"]:
                        failed += 1
                        continue
                    recipe = Recipe(
                        name=data["name"], description=data["description"], servings=data["servings"],
                        total_time=data["total_time"], source_url=data["source_url"], mealie_slug=slug,
                    )
                    recipe.ingredients = data["ingredients"]
                    recipe.instructions = data["instructions"]
                    db.add(recipe)
                    db.commit()
                    if full.get("id"):
                        image = await mealie.get_image(client, full["id"])
                        if image and _save_food_photo(recipe.id, image):
                            recipe.image_url = f"/image/{recipe.id}"
                            db.commit()
                    imported += 1
                except Exception as e:
                    db.rollback()
                    logger.warning("Importing Mealie recipe %s failed: %s", slug, e)
                    failed += 1
    except httpx.HTTPStatusError as e:
        msg = f"Mealie gaf een fout (HTTP {e.response.status_code}). Controleer URL en token."
        return _render_settings(request, db, mealie_error=msg)
    except httpx.HTTPError as e:
        return _render_settings(request, db, mealie_error=f"Mealie niet bereikbaar: {e}")

    msg = f"{imported} recepten geïmporteerd, {skipped} stonden er al" + (f", {failed} mislukt." if failed else ".")
    logger.info("Mealie import: %s", msg)
    return _render_settings(request, db, mealie_result=msg)


# ── Instellingen ───────────────────────────────────────────────────────


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, db: Session = Depends(get_db)):
    return _render_settings(request, db)


@router.post("/settings/ah-code")
async def ah_code_exchange(request: Request, callback_url: str = Form(""), db: Session = Depends(get_db)):
    raw = callback_url.strip()
    if not raw:
        return _render_settings(request, db, ah_login_error="Plak de URL uit je adresbalk.")
    try:
        code = parse_qs(urlparse(raw).query).get("code", [None])[0]
    except Exception:
        code = None
    code = code or raw
    try:
        data = await ah_client.exchange_code(code)
        _set_setting(db, "ah_user_token", data["access_token"])
        _set_setting(db, "ah_refresh_token", data["refresh_token"])
        return _render_settings(request, db, ah_login_success=True)
    except httpx.HTTPStatusError as e:
        msg = f"Code ongeldig of verlopen (HTTP {e.response.status_code}). Probeer opnieuw."
        return _render_settings(request, db, ah_login_error=msg)
    except Exception as e:
        return _render_settings(request, db, ah_login_error=f"Koppelen mislukt: {e}")


def _render_settings(
    request: Request,
    db: Session,
    ah_login_error: str = "",
    ah_login_success: bool = False,
    mealie_error: str = "",
    mealie_result: str = "",
):
    return templates.TemplateResponse(
        request, "settings.html", {"ah_token_set": bool(_get_setting(db, "ah_user_token")),
            "ah_refresh_set": bool(_get_setting(db, "ah_refresh_token")),
            "has_api_key": bool(settings.anthropic_api_key),
            "ah_login_url": ah_client.get_login_url(),
            "ah_login_error": ah_login_error,
            "ah_login_success": ah_login_success,
            "mealie_url": _get_setting(db, "mealie_url"),
            "mealie_token_set": bool(_get_setting(db, "mealie_token")),
            "mealie_error": mealie_error,
            "mealie_result": mealie_result,
        },
    )

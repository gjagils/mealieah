import asyncio
import io
import os
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from PIL import Image, ImageOps
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clients.ah import ah_client
from app.clients.extractor import extract_recipe, fetch_url
from app.clients.mealie import MealieClient, convert_recipe
from app.config import settings
from app.database import get_db
from app.logging_config import logger
from app.models import AppSetting, Recipe, WeekmenuEntry

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

IMAGE_DIR = os.path.join("data", "images")
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
DAYS = ["Maandag", "Dinsdag", "Woensdag", "Donderdag", "Vrijdag", "Zaterdag", "Zondag"]


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
async def index(request: Request, db: Session = Depends(get_db)):
    recipes = db.execute(select(Recipe).order_by(Recipe.created_at.desc())).scalars().all()
    return templates.TemplateResponse(
        request, "recipes.html", {"recipes": recipes, "has_api_key": bool(settings.anthropic_api_key)},
    )


@router.get("/recipe/{recipe_id}", response_class=HTMLResponse)
async def recipe_detail(request: Request, recipe_id: int, db: Session = Depends(get_db)):
    recipe = _get_recipe(db, recipe_id)
    return templates.TemplateResponse(
        request, "recipe_detail.html", {"recipe": recipe,
            "ingredients": recipe.ingredients,
            "has_token": bool(_get_setting(db, "ah_refresh_token") or _get_setting(db, "ah_user_token")),
        },
    )


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
    for entry in db.execute(select(WeekmenuEntry).where(WeekmenuEntry.recipe_id == recipe_id)).scalars():
        db.delete(entry)
    db.delete(recipe)
    db.commit()
    path = os.path.join(IMAGE_DIR, f"{recipe_id}.jpg")
    if os.path.exists(path):
        os.remove(path)
    return RedirectResponse("/", status_code=303)


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


async def _automatch(ingredients: list[dict]) -> int:
    """Fill in the top AH search hit for every ingredient without a product."""
    sem = asyncio.Semaphore(4)

    async def match(ing: dict) -> bool:
        if ing.get("skip") or ing.get("product") or not ing.get("search"):
            return False
        async with sem:
            try:
                products = await ah_client.search_products(ing["search"], size=1)
            except Exception as e:
                logger.warning("AH search failed for %s: %s", ing["search"], e)
                return False
        if products:
            ing["product"] = products[0]
            return True
        return False

    results = await asyncio.gather(*(match(i) for i in ingredients))
    return sum(results)


@router.post("/api/recipe/{recipe_id}/automatch")
async def automatch(recipe_id: int, db: Session = Depends(get_db)):
    recipe = _get_recipe(db, recipe_id)
    ingredients = recipe.ingredients
    matched = await _automatch(ingredients)
    recipe.ingredients = ingredients
    db.commit()
    return {"ok": True, "matched": matched, "ingredients": ingredients}


# ── Weekmenu ───────────────────────────────────────────────────────────


@router.get("/weekmenu", response_class=HTMLResponse)
async def weekmenu_page(request: Request, db: Session = Depends(get_db)):
    recipes = db.execute(select(Recipe).order_by(Recipe.name)).scalars().all()
    entries = db.execute(select(WeekmenuEntry)).scalars().all()
    by_day: dict[int, list[int]] = {}
    for e in entries:
        by_day.setdefault(e.day, []).append(e.recipe_id)
    return templates.TemplateResponse(
        request, "weekmenu.html", {"recipes": recipes,
            "days": list(enumerate(DAYS)),
            "by_day": by_day,
            "has_token": bool(_get_setting(db, "ah_refresh_token") or _get_setting(db, "ah_user_token")),
        },
    )


class WeekmenuPayload(BaseModel):
    days: dict[int, list[int]]  # day index -> recipe ids


@router.post("/api/weekmenu")
async def save_weekmenu(payload: WeekmenuPayload, db: Session = Depends(get_db)):
    for entry in db.execute(select(WeekmenuEntry)).scalars():
        db.delete(entry)
    for day, ids in payload.days.items():
        if 0 <= day <= 6:
            for rid in ids:
                db.add(WeekmenuEntry(day=day, recipe_id=rid))
    db.commit()
    return {"ok": True}


# ── Boodschappenlijstje van AH ─────────────────────────────────────────


class CartPayload(BaseModel):
    recipe_ids: list[int]


def aggregate_cart(recipes: list[Recipe]) -> tuple[list[dict], list[str]]:
    """Merge ingredients over recipes. Returns (cart items, unmatched ingredient texts)."""
    cart: dict[int, dict] = {}
    unmatched: list[str] = []
    for recipe in recipes:
        for ing in recipe.ingredients:
            if ing.get("skip"):
                continue
            product = ing.get("product")
            if not product or not product.get("id"):
                unmatched.append(ing.get("text", ""))
                continue
            qty = max(1, int(ing.get("quantity") or 1))
            if product["id"] in cart:
                cart[product["id"]]["quantity"] += qty
            else:
                cart[product["id"]] = {"product_id": product["id"], "quantity": qty, "name": product.get("name", "")}
    return list(cart.values()), unmatched


@router.post("/api/cart/fill")
async def fill_cart(payload: CartPayload, db: Session = Depends(get_db)):
    recipes = [r for r in (db.get(Recipe, i) for i in dict.fromkeys(payload.recipe_ids)) if r]
    if not recipes:
        return {"ok": False, "error": "Geen recepten gekozen."}

    # Match whatever is still unmatched so a one-click flow works
    for recipe in recipes:
        ingredients = recipe.ingredients
        if await _automatch(ingredients):
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

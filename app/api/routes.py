from datetime import date, timedelta

import httpx
from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clients.ah import ah_client
from app.clients.mealie import mealie_client
from app.database import get_db
from app.logging_config import logger, set_log_level
from app.models import AppSetting, IngredientMapping

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


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


# ── Pages ──────────────────────────────────────────────────────────────


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    logger.debug("Loading recipe list from Mealie")
    try:
        data = await mealie_client.get_recipes(per_page=100)
        recipes = data.get("items", []) if isinstance(data, dict) else data
    except Exception as e:
        logger.error("Failed to fetch recipes: %s", e)
        recipes = []
    return templates.TemplateResponse(
        "recipes.html", {"request": request, "recipes": recipes}
    )


@router.get("/recipe/{slug}", response_class=HTMLResponse)
async def recipe_detail(request: Request, slug: str, db: Session = Depends(get_db)):
    logger.debug("Loading recipe detail: %s", slug)
    try:
        recipe = await mealie_client.get_recipe(slug)
    except Exception as e:
        logger.error("Failed to fetch recipe %s: %s", slug, e)
        return templates.TemplateResponse(
            "error.html", {"request": request, "message": f"Recept niet gevonden: {e}"}
        )

    ingredients = recipe.get("recipeIngredient", [])

    # Load existing mappings for this recipe
    mappings_rows = db.execute(
        select(IngredientMapping).where(IngredientMapping.recipe_slug == slug)
    ).scalars().all()
    mappings = {m.ingredient_reference_id: m for m in mappings_rows}

    # Merge ingredients with their mappings
    enriched = []
    for ing in ingredients:
        ref_id = ing.get("referenceId", "")
        display = ing.get("display", ing.get("originalText", ing.get("note", "")))
        if not display:
            parts = []
            if ing.get("quantity"):
                parts.append(str(ing["quantity"]))
            if ing.get("unit", {}).get("name"):
                parts.append(ing["unit"]["name"])
            if ing.get("food", {}).get("name"):
                parts.append(ing["food"]["name"])
            if ing.get("note"):
                parts.append(ing["note"])
            display = " ".join(parts) if parts else "(onbekend)"

        mapping = mappings.get(ref_id)
        enriched.append({
            "reference_id": ref_id,
            "display": display,
            "mapping": mapping,
        })

    return templates.TemplateResponse(
        "recipe_detail.html",
        {
            "request": request,
            "recipe": recipe,
            "ingredients": enriched,
        },
    )


# ── Mealie Image Proxy ─────────────────────────────────────────────────


@router.get("/proxy/recipe-image/{recipe_id}")
async def proxy_recipe_image(recipe_id: str):
    import httpx
    url = f"{mealie_client.base_url}/api/media/recipes/{recipe_id}/images/min-original.webp"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=mealie_client._headers, timeout=10)
        if resp.status_code != 200:
            return Response(status_code=404)
        return Response(
            content=resp.content,
            media_type=resp.headers.get("content-type", "image/webp"),
            headers={"Cache-Control": "public, max-age=86400"},
        )


# ── AH Product Search (AJAX) ──────────────────────────────────────────


@router.get("/api/ah/search")
async def ah_search(q: str = Query(..., min_length=1)):
    logger.debug("AH product search: %s", q)
    try:
        products = await ah_client.search_products(q)
        return {"products": products}
    except Exception as e:
        logger.error("AH search failed: %s", e)
        return {"products": [], "error": str(e)}


# ── Mapping CRUD ───────────────────────────────────────────────────────


@router.post("/api/mapping")
async def save_mapping(
    recipe_slug: str = Form(...),
    recipe_name: str = Form(""),
    ingredient_reference_id: str = Form(...),
    ingredient_display: str = Form(...),
    status: str = Form(...),
    ah_product_id: int | None = Form(None),
    ah_product_name: str | None = Form(None),
    ah_product_image_url: str | None = Form(None),
    ah_product_unit_size: str | None = Form(None),
    ah_product_price: str | None = Form(None),
    ah_quantity: int = Form(1),
    db: Session = Depends(get_db),
):
    logger.info(
        "Saving mapping: recipe=%s ingredient=%s status=%s",
        recipe_slug, ingredient_display, status,
    )
    existing = db.execute(
        select(IngredientMapping).where(
            IngredientMapping.recipe_slug == recipe_slug,
            IngredientMapping.ingredient_reference_id == ingredient_reference_id,
        )
    ).scalar_one_or_none()

    if existing:
        existing.status = status
        existing.ingredient_display = ingredient_display
        existing.recipe_name = recipe_name
        existing.ah_product_id = ah_product_id
        existing.ah_product_name = ah_product_name
        existing.ah_product_image_url = ah_product_image_url
        existing.ah_product_unit_size = ah_product_unit_size
        existing.ah_product_price = ah_product_price
        existing.ah_quantity = ah_quantity
    else:
        db.add(IngredientMapping(
            recipe_slug=recipe_slug,
            recipe_name=recipe_name,
            ingredient_reference_id=ingredient_reference_id,
            ingredient_display=ingredient_display,
            status=status,
            ah_product_id=ah_product_id,
            ah_product_name=ah_product_name,
            ah_product_image_url=ah_product_image_url,
            ah_product_unit_size=ah_product_unit_size,
            ah_product_price=ah_product_price,
            ah_quantity=ah_quantity,
        ))
    db.commit()
    return {"ok": True}


@router.post("/api/mapping/delete")
async def delete_mapping(
    recipe_slug: str = Form(...),
    ingredient_reference_id: str = Form(...),
    db: Session = Depends(get_db),
):
    existing = db.execute(
        select(IngredientMapping).where(
            IngredientMapping.recipe_slug == recipe_slug,
            IngredientMapping.ingredient_reference_id == ingredient_reference_id,
        )
    ).scalar_one_or_none()
    if existing:
        db.delete(existing)
        db.commit()
        logger.info("Deleted mapping for %s / %s", recipe_slug, ingredient_reference_id)
    return {"ok": True}


# ── Meal Plan → Cart ──────────────────────────────────────────────────


@router.get("/mealplan", response_class=HTMLResponse)
async def mealplan_page(request: Request, db: Session = Depends(get_db)):
    today = date.today()
    start = today - timedelta(days=today.weekday())  # Monday
    end = start + timedelta(days=6)  # Sunday

    logger.debug("Loading meal plan %s to %s", start, end)
    try:
        plans = await mealie_client.get_mealplans(str(start), str(end))
    except Exception as e:
        logger.error("Failed to fetch meal plans: %s", e)
        plans = []

    # Collect all recipe slugs from the meal plan
    recipe_slugs = set()
    for plan in plans:
        recipe = plan.get("recipe") or {}
        slug = recipe.get("slug") or plan.get("recipeId", "")
        if slug:
            recipe_slugs.add(slug)

    # Load all mappings for those recipes
    all_items = []
    unmapped_items = []
    if recipe_slugs:
        mappings = db.execute(
            select(IngredientMapping).where(
                IngredientMapping.recipe_slug.in_(recipe_slugs)
            )
        ).scalars().all()
        for m in mappings:
            if m.status == "mapped" and m.ah_product_id:
                all_items.append(m)
            elif m.status == "unmapped":
                unmapped_items.append(m)

    has_token = bool(_get_setting(db, "ah_user_token"))

    return templates.TemplateResponse(
        "mealplan.html",
        {
            "request": request,
            "plans": plans,
            "start": str(start),
            "end": str(end),
            "cart_items": all_items,
            "unmapped_items": unmapped_items,
            "has_token": has_token,
        },
    )


@router.post("/api/cart/fill")
async def fill_cart(db: Session = Depends(get_db)):
    today = date.today()
    start = today - timedelta(days=today.weekday())
    end = start + timedelta(days=6)

    logger.info("Filling AH cart from meal plan %s to %s", start, end)

    plans = await mealie_client.get_mealplans(str(start), str(end))
    recipe_slugs = set()
    for plan in plans:
        recipe = plan.get("recipe") or {}
        slug = recipe.get("slug") or plan.get("recipeId", "")
        if slug:
            recipe_slugs.add(slug)

    if not recipe_slugs:
        return {"ok": False, "error": "Geen recepten in weekmenu"}

    mappings = db.execute(
        select(IngredientMapping).where(
            IngredientMapping.recipe_slug.in_(recipe_slugs),
            IngredientMapping.status == "mapped",
            IngredientMapping.ah_product_id.is_not(None),
        )
    ).scalars().all()

    if not mappings:
        return {"ok": False, "error": "Geen gemapte ingrediënten gevonden"}

    # Aggregate: same product across recipes → sum quantities
    cart: dict[int, dict] = {}
    for m in mappings:
        if m.ah_product_id in cart:
            cart[m.ah_product_id]["quantity"] += m.ah_quantity
        else:
            cart[m.ah_product_id] = {
                "product_id": m.ah_product_id,
                "quantity": m.ah_quantity,
                "name": m.ah_product_name,
            }

    access_token = _get_setting(db, "ah_user_token")
    refresh_token = _get_setting(db, "ah_refresh_token")
    if not access_token and not refresh_token:
        return {"ok": False, "error": "AH token niet ingesteld. Ga naar Instellingen."}

    def _save_tokens(new_access: str, new_refresh: str) -> None:
        _set_setting(db, "ah_user_token", new_access)
        _set_setting(db, "ah_refresh_token", new_refresh)

    ah_client.set_user_tokens(access_token, refresh_token, on_tokens_updated=_save_tokens)
    try:
        await ah_client.add_to_cart(list(cart.values()))
    except Exception as e:
        logger.error("Failed to fill cart: %s", e)
        return {"ok": False, "error": str(e)}

    return {"ok": True, "items_added": len(cart)}


# ── Settings ──────────────────────────────────────────────────────────


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, db: Session = Depends(get_db)):
    return await _render_settings(request, db)


@router.post("/settings/logging")
async def toggle_logging(
    verbose: str = Form("false"),
    db: Session = Depends(get_db),
):
    enabled = verbose == "true"
    _set_setting(db, "verbose_logging", str(enabled).lower())
    set_log_level("DEBUG" if enabled else "INFO")
    logger.info("Verbose logging %s", "enabled" if enabled else "disabled")
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/ah-login")
async def ah_login(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    db: Session = Depends(get_db),
):
    username = username.strip()
    password = password.strip()
    if not username or not password:
        return await _render_settings(request, db, ah_login_error="Vul e-mail en wachtwoord in.")

    try:
        data = await ah_client.login(username, password)
        _set_setting(db, "ah_user_token", data["access_token"])
        _set_setting(db, "ah_refresh_token", data["refresh_token"])
        logger.info("AH login successful for %s", username)
        return await _render_settings(request, db, ah_login_success=True)
    except httpx.HTTPStatusError as e:
        logger.error("AH login failed (HTTP %s): %s", e.response.status_code, e)
        if e.response.status_code == 401:
            msg = "Onjuist e-mailadres of wachtwoord."
        else:
            msg = f"AH login mislukt (HTTP {e.response.status_code})."
        return await _render_settings(request, db, ah_login_error=msg)
    except Exception as e:
        logger.error("AH login failed: %s", e)
        return await _render_settings(request, db, ah_login_error=f"Inloggen mislukt: {e}")


async def _render_settings(
    request: Request,
    db: Session,
    ah_login_error: str = "",
    ah_login_success: bool = False,
):
    verbose = _get_setting(db, "verbose_logging") == "true"
    ah_token = _get_setting(db, "ah_user_token")
    ah_refresh = _get_setting(db, "ah_refresh_token")
    mealie_ok = await mealie_client.health_check()
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "verbose_logging": verbose,
            "ah_token_set": bool(ah_token),
            "ah_refresh_set": bool(ah_refresh),
            "mealie_ok": mealie_ok,
            "ah_login_error": ah_login_error,
            "ah_login_success": ah_login_success,
        },
    )

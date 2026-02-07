from datetime import date, timedelta

import httpx
from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clients.ah import ah_client
from app.clients.mealie import mealie_client
from app.config import settings
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
async def index(request: Request, db: Session = Depends(get_db)):
    logger.debug("Loading recipe list from Mealie")
    try:
        data = await mealie_client.get_recipes(per_page=100)
        recipes = data.get("items", []) if isinstance(data, dict) else data
    except Exception as e:
        logger.error("Failed to fetch recipes: %s", e)
        recipes = []

    # Get mapping counts per recipe
    from sqlalchemy import case, func
    counts = db.execute(
        select(
            IngredientMapping.recipe_slug,
            func.count().label("total"),
            func.count(case(
                (IngredientMapping.status == "mapped", 1),
            )).label("mapped"),
            func.count(case(
                (IngredientMapping.status == "skipped", 1),
            )).label("skipped"),
        ).group_by(IngredientMapping.recipe_slug)
    ).all()
    mapping_stats = {row.recipe_slug: {"total": row.total, "mapped": row.mapped, "skipped": row.skipped} for row in counts}

    return templates.TemplateResponse(
        "recipes.html", {"request": request, "recipes": recipes, "mapping_stats": mapping_stats}
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
            "mealie_external_url": settings.mealie_external_url,
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


# ── Mapping Suggestions ───────────────────────────────────────────────


@router.get("/api/mapping/suggestions")
async def mapping_suggestions(
    ingredient_display: str = Query(...),
    recipe_slug: str = Query(...),
    db: Session = Depends(get_db),
):
    """Find existing mappings from other recipes for similar ingredients."""
    import re

    # Strip quantities and units to get the food keyword
    cleaned = re.sub(
        r"[\d.,]+\s*(g|kg|ml|l|cl|dl|el|tl|eetlepels?|theelepels?|stuks?|stuk|snuf|snufje|takjes?|tenen?|blaadjes?|plakjes?|schijfjes?|blikjes?|zakjes?|potjes?)\b",
        "",
        ingredient_display,
        flags=re.IGNORECASE,
    ).strip()
    # Remove leading/trailing punctuation and whitespace
    cleaned = re.sub(r"^[\s,.-]+|[\s,.-]+$", "", cleaned)

    if not cleaned or len(cleaned) < 2:
        return {"suggestions": []}

    # Search for mapped/skipped ingredients in OTHER recipes with similar display text
    keywords = [w for w in cleaned.split() if len(w) >= 2]
    if not keywords:
        return {"suggestions": []}

    # Find mappings from other recipes where display contains the keywords
    query = select(IngredientMapping).where(
        IngredientMapping.recipe_slug != recipe_slug,
        IngredientMapping.status.in_(["mapped", "skipped"]),
    )

    results = db.execute(query).scalars().all()

    # Score and filter matches by keyword overlap
    suggestions = []
    seen_products: set[int | None] = set()
    for m in results:
        m_display_lower = m.ingredient_display.lower()
        matched_keywords = sum(1 for kw in keywords if kw.lower() in m_display_lower)
        if matched_keywords == 0:
            continue

        # Deduplicate: skip if we've already seen this product
        if m.status == "mapped" and m.ah_product_id in seen_products:
            continue
        if m.status == "skipped" and None in seen_products:
            continue

        score = matched_keywords / len(keywords)
        if score >= 0.5:  # At least half of keywords must match
            suggestions.append({
                "status": m.status,
                "recipe_name": m.recipe_name,
                "ingredient_display": m.ingredient_display,
                "ah_product_id": m.ah_product_id,
                "ah_product_name": m.ah_product_name,
                "ah_product_image_url": m.ah_product_image_url,
                "ah_product_unit_size": m.ah_product_unit_size,
                "ah_product_price": m.ah_product_price,
                "score": score,
            })
            if m.status == "mapped":
                seen_products.add(m.ah_product_id)
            else:
                seen_products.add(None)

    # Sort by score descending, then mapped before skipped
    suggestions.sort(key=lambda s: (-s["score"], s["status"] != "mapped"))

    return {"suggestions": suggestions[:5]}


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
    # Next upcoming Monday (or today if it's Monday)
    days_until_monday = (7 - today.weekday()) % 7
    if days_until_monday == 0 and today.weekday() == 0:
        monday = today  # today is Monday
    else:
        monday = today + timedelta(days=days_until_monday)
    friday = monday + timedelta(days=4)

    logger.debug("Loading meal plan %s to %s", monday, friday)
    try:
        plans = await mealie_client.get_mealplans(str(monday), str(friday))
    except Exception as e:
        logger.error("Failed to fetch meal plans: %s", e)
        plans = []

    # Group plans by date
    plans_by_date: dict[str, list] = {}
    for plan in plans:
        d = plan.get("date", "")
        plans_by_date.setdefault(d, []).append(plan)

    # Build weekday structure
    day_names = ["Ma", "Di", "Wo", "Do", "Vr"]
    all_recipe_slugs: set[str] = set()
    weekdays = []
    for i in range(5):
        day_date = monday + timedelta(days=i)
        day_plans = plans_by_date.get(str(day_date), [])
        recipes = []
        for plan in day_plans:
            recipe = plan.get("recipe") or {}
            slug = recipe.get("slug", "")
            if slug:
                all_recipe_slugs.add(slug)
                recipes.append({"slug": slug, "name": recipe.get("name", ""), "id": recipe.get("id", "")})
        weekdays.append({"name": day_names[i], "date": str(day_date), "recipes": recipes})

    # Get mapping stats per recipe
    from sqlalchemy import case, func
    mapping_stats: dict[str, dict] = {}
    if all_recipe_slugs:
        counts = db.execute(
            select(
                IngredientMapping.recipe_slug,
                func.count().label("total"),
                func.count(case((IngredientMapping.status == "mapped", 1))).label("mapped"),
                func.count(case((IngredientMapping.status == "skipped", 1))).label("skipped"),
            ).where(
                IngredientMapping.recipe_slug.in_(all_recipe_slugs)
            ).group_by(IngredientMapping.recipe_slug)
        ).all()
        mapping_stats = {r.recipe_slug: {"total": r.total, "mapped": r.mapped, "skipped": r.skipped} for r in counts}

    # Determine status per day: ready (green), needs_mapping (orange), empty (blue)
    for day in weekdays:
        if not day["recipes"]:
            day["status"] = "empty"
        else:
            all_done = True
            for recipe in day["recipes"]:
                stats = mapping_stats.get(recipe["slug"])
                if not stats or (stats["total"] - stats["mapped"] - stats["skipped"]) > 0:
                    all_done = False
                    break
            day["status"] = "ready" if all_done else "needs_mapping"

    # Cart items and unmapped for shopping list
    all_items = []
    unmapped_items = []
    if all_recipe_slugs:
        mappings = db.execute(
            select(IngredientMapping).where(
                IngredientMapping.recipe_slug.in_(all_recipe_slugs)
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
            "weekdays": weekdays,
            "cart_items": all_items,
            "unmapped_items": unmapped_items,
            "has_token": has_token,
        },
    )


@router.post("/api/cart/fill")
async def fill_cart(db: Session = Depends(get_db)):
    today = date.today()
    days_until_monday = (7 - today.weekday()) % 7
    start = today if today.weekday() == 0 else today + timedelta(days=days_until_monday)
    end = start + timedelta(days=4)

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


# ── Recipe Scanner ────────────────────────────────────────────────────


@router.get("/scan", response_class=HTMLResponse)
async def scan_page(request: Request):
    has_key = bool(settings.anthropic_api_key)
    return templates.TemplateResponse(
        "scan.html",
        {
            "request": request,
            "has_api_key": has_key,
            "mealie_external_url": settings.mealie_external_url,
        },
    )


@router.post("/api/scan")
async def scan_recipe(images: list[UploadFile] = File(...)):
    """Process one or more recipe photos with Claude Vision."""
    from app.clients.recipe_scanner import scan_recipe_images

    if not settings.anthropic_api_key:
        return JSONResponse(
            {"ok": False, "error": "ANTHROPIC_API_KEY is niet ingesteld."},
            status_code=400,
        )

    allowed_types = {"image/jpeg", "image/png", "image/webp", "image/gif"}
    image_list: list[tuple[bytes, str]] = []

    for img in images:
        if img.content_type not in allowed_types:
            return JSONResponse(
                {"ok": False, "error": f"Ongeldig bestandstype: {img.content_type}"},
                status_code=400,
            )
        data = await img.read()
        if len(data) > 20 * 1024 * 1024:
            return JSONResponse(
                {"ok": False, "error": "Afbeelding is te groot (max 20MB)."},
                status_code=400,
            )
        image_list.append((data, img.content_type))

    try:
        recipe = await scan_recipe_images(image_list)
        return {"ok": True, "recipe": recipe, "image_count": len(image_list)}
    except Exception as e:
        logger.error("Recipe scan failed: %s", e)
        return JSONResponse(
            {"ok": False, "error": str(e)}, status_code=500
        )


@router.post("/api/scan/fix-photo")
async def fix_photo_rotation(image: UploadFile = File(...)):
    """Rotate a photo based on EXIF and return corrected JPEG."""
    data = await image.read()
    corrected, content_type = _fix_image_for_mealie(data)
    return Response(content=corrected, media_type=content_type)


def _fix_image_for_mealie(image_data: bytes) -> tuple[bytes, str]:
    """Auto-rotate image based on EXIF, resize for Mealie, return as JPEG."""
    import io

    from PIL import Image, ImageOps

    img = Image.open(io.BytesIO(image_data))
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    # Resize to reasonable size for a recipe cover photo
    img.thumbnail((1600, 1600), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue(), "image/jpeg"


@router.post("/api/scan/save")
async def save_scanned_recipe(
    recipe_json: str = Form(...),
    food_photo: UploadFile | None = File(None),
):
    """Save a scanned recipe to Mealie, optionally with a food photo."""
    import json as json_mod

    recipe_data = json_mod.loads(recipe_json)
    name = recipe_data.get("name", "").strip()

    if not name:
        return JSONResponse(
            {"ok": False, "error": "Receptnaam is verplicht."}, status_code=400
        )

    try:
        import uuid

        # Step 1: Create recipe in Mealie (returns slug)
        created = await mealie_client.create_recipe(name)
        slug = created if isinstance(created, str) else created.get("slug", created)
        logger.info("Created recipe in Mealie: %s", slug)

        # Step 2: Update with ingredients and instructions
        import re

        ingredients = []
        for ing in recipe_data.get("ingredients", []):
            # Try to parse "250g kipfilet" → quantity=250, unit=g, food=kipfilet
            match = re.match(
                r"^([\d.,/½¼¾⅓⅔]+)\s*(g|kg|ml|l|cl|dl|el|tl|eetlepels?|theelepels?|stuks?|stuk|blikjes?|zakjes?|potjes?)?\s*(.+)$",
                ing.strip(),
                re.IGNORECASE,
            )
            if match:
                qty_str = match.group(1).replace(",", ".").replace("½", "0.5").replace("¼", "0.25").replace("¾", "0.75")
                try:
                    qty = float(qty_str)
                except ValueError:
                    qty = None
                unit_name = match.group(2) or ""
                food_name = match.group(3).strip().rstrip(",.")
                ingredient = {
                    "referenceId": str(uuid.uuid4()),
                    "quantity": qty,
                    "unit": {"name": unit_name} if unit_name else None,
                    "food": {"name": food_name},
                    "note": "",
                    "originalText": ing,
                    "display": ing,
                }
            else:
                ingredient = {
                    "referenceId": str(uuid.uuid4()),
                    "quantity": None,
                    "unit": None,
                    "food": {"name": ing.strip()},
                    "note": "",
                    "originalText": ing,
                    "display": ing,
                }
            ingredients.append(ingredient)

        instructions = [
            {
                "id": str(uuid.uuid4()),
                "title": "",
                "text": step,
                "ingredientReferences": [],
            }
            for step in recipe_data.get("instructions", [])
        ]

        update_data = {
            "name": name,
            "description": recipe_data.get("description", ""),
            "recipeYield": recipe_data.get("recipe_yield", ""),
            "totalTime": recipe_data.get("total_time", ""),
            "recipeIngredient": ingredients,
            "recipeInstructions": instructions,
        }

        await mealie_client.update_recipe(slug, update_data)
        logger.info("Updated recipe %s with ingredients and instructions", slug)

        # Step 3: Upload food photo if provided (with EXIF rotation fix)
        if food_photo and food_photo.size:
            photo_data = await food_photo.read()
            content_type = food_photo.content_type
            try:
                photo_data, content_type = _fix_image_for_mealie(photo_data)
                await mealie_client.upload_recipe_image(slug, photo_data, content_type)
                logger.info("Uploaded food photo for %s", slug)
            except Exception as img_err:
                logger.warning("Failed to upload recipe image: %s", img_err)

        return {"ok": True, "slug": slug}
    except httpx.HTTPStatusError as e:
        body = e.response.text[:300] if e.response else ""
        logger.error("Mealie API error saving recipe: %s — %s", e, body)
        return JSONResponse(
            {"ok": False, "error": f"Mealie fout (HTTP {e.response.status_code}): {body}"}, status_code=500
        )
    except Exception as e:
        logger.error("Failed to save recipe to Mealie: %s", e)
        return JSONResponse(
            {"ok": False, "error": str(e)}, status_code=500
        )


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


@router.post("/settings/ah-code")
async def ah_code_exchange(
    request: Request,
    callback_url: str = Form(""),
    db: Session = Depends(get_db),
):
    raw = callback_url.strip()
    if not raw:
        return await _render_settings(request, db, ah_login_error="Plak de URL uit je adresbalk.")

    # Extract code from callback URL (appie://login-exit?code=XXXXX)
    from urllib.parse import parse_qs, urlparse
    try:
        parsed = urlparse(raw)
        code = parse_qs(parsed.query).get("code", [None])[0]
    except Exception:
        code = None

    # If it's not a URL, treat the whole input as the code
    if not code:
        code = raw

    try:
        data = await ah_client.exchange_code(code)
        _set_setting(db, "ah_user_token", data["access_token"])
        _set_setting(db, "ah_refresh_token", data["refresh_token"])
        logger.info("AH account gekoppeld via OAuth2 code")
        return await _render_settings(request, db, ah_login_success=True)
    except httpx.HTTPStatusError as e:
        logger.error("AH code exchange failed (HTTP %s): %s", e.response.status_code, e)
        msg = f"Code ongeldig of verlopen (HTTP {e.response.status_code}). Probeer opnieuw."
        return await _render_settings(request, db, ah_login_error=msg)
    except Exception as e:
        logger.error("AH code exchange failed: %s", e)
        return await _render_settings(request, db, ah_login_error=f"Koppelen mislukt: {e}")


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
            "ah_login_url": ah_client.get_login_url(),
            "ah_login_error": ah_login_error,
            "ah_login_success": ah_login_success,
        },
    )

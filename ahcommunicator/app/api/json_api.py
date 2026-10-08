"""JSON endpoints used by the native iOS app."""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import routes
from app.clients.ah import ah_client
from app.config import settings
from app.database import get_db
from app.logging_config import logger
from app.models import PlanEntry, Recipe

router = APIRouter(prefix="/api")


def _summary(r: Recipe) -> dict:
    return {
        "id": r.id, "name": r.name, "servings": r.servings, "total_time": r.total_time,
        "image_url": r.image_url, "gf_mode": r.gf_mode,
    }


class LoginPayload(BaseModel):
    pin: str = ""


@router.post("/login")
async def api_login(payload: LoginPayload):
    import hmac

    from app.main import session_token

    if not settings.app_pin:
        return {"ok": True, "token": ""}
    if hmac.compare_digest(payload.pin.strip(), settings.app_pin):
        return {"ok": True, "token": session_token()}
    return JSONResponse({"ok": False, "error": "Pincode klopt niet."}, status_code=401)


@router.get("/recipes")
async def api_recipes(q: str = "", db: Session = Depends(get_db)):
    query = select(Recipe).order_by(Recipe.name)
    if q.strip():
        query = query.where(Recipe.name.ilike(f"%{q.strip()}%"))
    return {"recipes": [_summary(r) for r in db.execute(query).scalars()]}


@router.get("/recipes/{recipe_id}")
async def api_recipe(recipe_id: int, db: Session = Depends(get_db)):
    r = db.get(Recipe, recipe_id)
    if not r:
        raise HTTPException(404, "Recept niet gevonden")
    return {
        **_summary(r),
        "description": r.description,
        "source_url": r.source_url,
        "gf_note": r.gf_note,
        "instructions": r.instructions,
        "ingredients": [
            {
                "text": i["text"], "skip": bool(i.get("skip")), "gluten": bool(i.get("gluten")),
                "gf_search": i.get("gf_search", ""),
                "product": (i.get("product") or {}).get("name"),
                "gf_product": (i.get("gf_product") or {}).get("name"),
            }
            for i in r.ingredients
        ],
    }


@router.get("/week")
async def api_week(week: str | None = None, db: Session = Depends(get_db)):
    monday = routes.parse_week(week)
    entries = db.execute(
        select(PlanEntry).where(PlanEntry.date >= str(monday), PlanEntry.date <= str(monday + timedelta(days=6)))
    ).scalars().all()
    recipes = {r.id: r for r in db.execute(select(Recipe).where(Recipe.id.in_({e.recipe_id for e in entries}))).scalars()}
    days = []
    for i in range(7):
        d = monday + timedelta(days=i)
        days.append({
            "date": str(d), "label": routes.day_label(d), "today": d == date.today(),
            "recipes": [_summary(recipes[e.recipe_id]) for e in entries if e.date == str(d) and e.recipe_id in recipes],
        })
    return {
        "week": str(monday), "prev_week": str(monday - timedelta(days=7)),
        "next_week": str(monday + timedelta(days=7)),
        "days": days, "status": routes.week_status(db, monday),
    }


@router.get("/allerhande/search")
async def api_allerhande_search(q: str = Query(..., min_length=1), db: Session = Depends(get_db)):
    try:
        results = await ah_client.search_recipes(q.strip())
    except Exception as e:
        logger.error("Allerhande search failed: %s", e)
        return JSONResponse({"ok": False, "error": f"Zoeken bij AH mislukt: {e}"}, status_code=502)
    saved = set(db.execute(select(Recipe.ah_recipe_id).where(Recipe.ah_recipe_id.is_not(None))).scalars())
    return {"ok": True, "results": [{**r, "saved": r["id"] in saved} for r in results]}

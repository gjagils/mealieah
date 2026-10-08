import hashlib
import hmac

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.json_api import router as json_router
from app.api.routes import router
from app.config import settings
from app.database import Base, engine
from app.logging_config import setup_logging

setup_logging()
Base.metadata.create_all(engine)

app = FastAPI(title="AH Recepten", version="0.2.0")

PUBLIC_PREFIXES = ("/login", "/api/login", "/static", "/image")


def session_token() -> str:
    return hmac.new(settings.app_pin.encode(), b"ahrecepten-session", hashlib.sha256).hexdigest()


@app.middleware("http")
async def require_pin(request: Request, call_next):
    if settings.app_pin and not request.url.path.startswith(PUBLIC_PREFIXES):
        supplied = request.cookies.get("session", "")
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            supplied = auth[7:].strip()
        if not hmac.compare_digest(supplied, session_token()):
            if request.url.path.startswith("/api/"):
                from fastapi.responses import JSONResponse

                return JSONResponse({"ok": False, "error": "Niet ingelogd"}, status_code=401)
            return RedirectResponse("/login", status_code=303)
    return await call_next(request)


app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(json_router)
app.include_router(router)

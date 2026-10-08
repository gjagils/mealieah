from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.database import Base, engine
from app.logging_config import setup_logging

setup_logging()
Base.metadata.create_all(engine)

app = FastAPI(title="AH Recepten", version="0.1.0")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(router)

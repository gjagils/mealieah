import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

if settings.database_url.startswith("sqlite:///"):
    path = settings.database_url.removeprefix("sqlite:///")
    if os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False}
    if settings.database_url.startswith("sqlite")
    else {},
)
SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

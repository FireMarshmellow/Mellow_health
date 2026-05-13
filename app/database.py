import os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

_env_url = os.environ.get("DATABASE_URL", "")

if _env_url.startswith(("postgresql", "postgres")):
    # Docker / production: use the env var as-is (points to Postgres container)
    DATABASE_URL = _env_url
else:
    # Local development: always resolve to the project-root SQLite file so the
    # path is correct regardless of which directory uvicorn or the importer runs from.
    _project_root = Path(__file__).resolve().parent.parent
    DATABASE_URL = f"sqlite:///{_project_root / 'mellow_health.db'}"

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

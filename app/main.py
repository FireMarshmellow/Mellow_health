from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from sqlalchemy import text

from database import engine
from models import Base
from routers.cardio import router as cardio_router
from routers.strength import router as strength_router
from routers.plans import router as plans_router

Base.metadata.create_all(bind=engine)

# Add plan_workout_id to strength_workouts if the table predates this column.
# Use separate connections: PostgreSQL aborts the transaction on a failed SELECT,
# so the ALTER TABLE must run in a fresh connection.
try:
    with engine.connect() as _c:
        _c.execute(text("SELECT plan_workout_id FROM strength_workouts LIMIT 1"))
except Exception:
    with engine.connect() as _c:
        _c.execute(text("ALTER TABLE strength_workouts ADD COLUMN plan_workout_id INTEGER"))
        _c.commit()

app = FastAPI(title="Mellow Health")

app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(cardio_router)
app.include_router(strength_router)
app.include_router(plans_router)


@app.get("/")
def root():
    return RedirectResponse(url="/summary")

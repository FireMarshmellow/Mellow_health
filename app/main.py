from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from routers.cardio import router as cardio_router

app = FastAPI(title="Mellow Health")

app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(cardio_router)


@app.get("/")
def root():
    return RedirectResponse(url="/summary")

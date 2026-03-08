from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.web.dashboard import router as dashboard_router

app = FastAPI(title=settings.APP_NAME)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(dashboard_router)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "env": settings.APP_ENV,
    }


@app.get("/")
def root():
    return {
        "message": "Validator Dashboard API is running",
        "health": "/health",
        "dashboard": "/dashboard",
        "alerts": "/dashboard/alerts",
        "snapshots": "/dashboard/snapshots",
    }

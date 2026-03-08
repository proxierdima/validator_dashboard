from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.web.dashboard import router as dashboard_router
from app.routes.dashboard_rewards import router as rewards_router

app = FastAPI(title=settings.APP_NAME)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(dashboard_router)
app.include_router(rewards_router)


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
        "rewards": "/dashboard/rewards",
        "alerts": "/dashboard/alerts",
        "public_rpc": "/dashboard/public-rpc",
        "snapshots": "/dashboard/snapshots",
    }

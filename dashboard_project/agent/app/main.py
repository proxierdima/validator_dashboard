from fastapi import FastAPI
from app.routes import router
from app.config import AGENT_NAME

app = FastAPI(title=AGENT_NAME)

app.include_router(router)

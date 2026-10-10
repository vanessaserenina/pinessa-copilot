import logging

from fastapi import FastAPI

from app.api.health import router as health_router
from app.core.config import settings

logging.basicConfig(level=logging.INFO)

app = FastAPI(title=settings.app_name)
app.include_router(health_router, prefix="/api")
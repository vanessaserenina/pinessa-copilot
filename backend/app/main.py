import logging
import uuid

from fastapi import FastAPI, Request

from app.api.audit_events import router as audit_router
from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.api.inventory import router as inventory_router
from app.api.users import router as users_router
from app.core.config import settings
from app.api.boms import router as boms_router
from app.api.products import router as products_router
logging.basicConfig(level=logging.INFO)

app = FastAPI(title=settings.app_name)


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request.state.request_id = uuid.uuid4().hex
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


app.include_router(health_router, prefix="/api")
app.include_router(auth_router, prefix="/api")
app.include_router(users_router, prefix="/api")
app.include_router(audit_router, prefix="/api")
app.include_router(products_router, prefix="/api")
app.include_router(boms_router, prefix="/api")
app.include_router(inventory_router, prefix="/api")

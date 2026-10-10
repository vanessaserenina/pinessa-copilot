"""Import every module's models here so Alembic can discover all tables."""
from app.modules.products import models as products_models  # noqa: F401
"""Import every module's models here so Alembic can discover all tables."""
from app.modules.audit import models as audit_models  # noqa: F401
from app.modules.auth import models as auth_models  # noqa: F401
from app.modules.products import models as products_models  # noqa: F401
from app.modules.inventory import models as inventory_models  # noqa: F401
from app.modules.orders import models as order_models  # noqa: F401
from app.modules.purchasing import models as purchasing_models  # noqa: F401

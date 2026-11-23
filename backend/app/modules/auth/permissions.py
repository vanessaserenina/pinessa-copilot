"""Role -> permissions. Each later stage adds its own permissions here."""

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "admin": frozenset({"users:manage", "audit:read", "products:read", "boms:manage",
                        "inventory:receive", "inventory:reserve", "orders:manage", "purchasing:manage",
                        "purchasing:read", "purchasing:approve", "production:manage", "deliveries:manage"}),
    "sales": frozenset({"products:read", "inventory:reserve", "orders:manage"}),
    "purchasing": frozenset({"products:read", "purchasing:read", "purchasing:manage"}),
    "warehouse": frozenset({"products:read", "inventory:receive", "inventory:reserve", "production:manage",
                            "deliveries:manage"}),
    "approver": frozenset({"audit:read", "products:read", "purchasing:read", "purchasing:approve"}),
}

ROLE_DESCRIPTIONS: dict[str, str] = {
    "admin": "Administrator: manages users and BOMs, and can read the audit log",
    "sales": "Sales: order intake and customer orders",
    "purchasing": "Purchasing: requisitions and purchase orders",
    "warehouse": "Warehouse and production: stock, assembly jobs, deliveries",
    "approver": "Approver: approves purchases and other gated actions",
}

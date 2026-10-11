"""Role -> permissions. Each later stage adds its own permissions here."""

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "admin": frozenset({"users:manage", "audit:read"}),
    "sales": frozenset(),
    "purchasing": frozenset(),
    "warehouse": frozenset(),
    "approver": frozenset({"audit:read"}),
}

ROLE_DESCRIPTIONS: dict[str, str] = {
    "admin": "Administrator: manages users and can read the audit log",
    "sales": "Sales: order intake and customer orders",
    "purchasing": "Purchasing: requisitions and purchase orders",
    "warehouse": "Warehouse and production: stock, assembly jobs, deliveries",
    "approver": "Approver: approves purchases and other gated actions",
}
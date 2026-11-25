"""Optional browser gate using a local model and installed Chromium/Playwright."""
import os
import secrets
import socket
import subprocess
import threading
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.database import get_db
from app.modules.auth.security import create_access_token
from app.main import app
from app.modules.inventory.models import Inventory
from app.modules.orders.models import Order
from app.modules.audit.models import AuditEvent
from sqlalchemy import select
from sqlalchemy.orm import Session


@pytest.mark.skipif(not os.environ.get("PINESSA_PLAYWRIGHT"), reason="Optional live browser gate")
@pytest.mark.parametrize("scenario", ["fractional", "missing", "shortage"])
def test_sales_browser_confirmation(migrated_db, new_product, make_user, tmp_path, monkeypatch, scenario):
    monkeypatch.setattr(settings, "order_source_storage", tmp_path)
    db = migrated_db
    product = new_product(unit="case")
    product.sku = "CLIP-B"
    product.name = "Blue clips"
    product.attributes = {"colour": "blue"}
    shortage = scenario == "shortage"
    db.add(Inventory(product_id=product.id, available=2 if shortage else 10))
    password = secrets.token_urlsafe()
    user = make_user("sales", password=password)
    db.commit()
    env = {**os.environ, "PINESSA_TEST_EMAIL": user.email, "PINESSA_TEST_PASSWORD": password,
           "PINESSA_QUANTITY_SCENARIO": scenario}
    if shortage:
        warehouse = make_user("warehouse")
        db.commit()
        env["PINESSA_TEST_WAREHOUSE_TOKEN"] = create_access_token(warehouse.id)
    root = Path(__file__).resolve().parents[3]
    server_app = FastAPI()
    server_app.include_router(app.router)
    server_app.mount("/", StaticFiles(directory=root / "frontend/dist", html=True))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        env["PINESSA_TEST_URL"] = f"http://127.0.0.1:{sock.getsockname()[1]}"
        def browser_db():
            with Session(db.get_bind()) as session:
                yield session

        server_app.dependency_overrides[get_db] = browser_db
        server = uvicorn.Server(uvicorn.Config(server_app, log_level="warning"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        try:
            subprocess.run(["node", str(root / "e2e/proposal-review.cjs")], env=env, check=True, timeout=320)
            db.expire_all()
            order = db.scalars(select(Order)).one()
            assert order.customer_po_number == "TEST-BROWSER-CORRECTED"
            expected_quantity = 8 if shortage else 3
            assert order.status == "ready_for_delivery" and order.items[0].quantity == expected_quantity
            audit = db.scalars(select(AuditEvent).where(AuditEvent.action == "ai_extraction.confirmed")).one()
            assert audit.detail["order_id"] == order.id
            assert audit.detail["reviewed"]["lines"][0]["quantity"] == expected_quantity
            assert db.get(Inventory, product.id).available == (0 if shortage else 7)
        finally:
            server.should_exit = True
            thread.join(8)



@pytest.fixture()
def db(migrated_db):
    return migrated_db

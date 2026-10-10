import pytest
from sqlalchemy.orm import Session

from app.core.database import engine


@pytest.fixture()
def db():
    """A session whose changes are always rolled back, so tests never leave data behind."""
    connection = engine.connect()
    outer = connection.begin()
    session = Session(
        bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        connection.close()
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.modules.auth.models import User
from app.modules.auth.security import DUMMY_HASH, verify_password


def normalise_email(email: str) -> str:
    return email.strip().lower()


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    """Return the user if email and password are right and the account is active."""
    user = db.scalar(
        select(User).options(joinedload(User.role)).where(User.email == normalise_email(email))
    )
    if user is None:
        verify_password(password, DUMMY_HASH)
        return None
    password_ok = verify_password(password, user.password_hash)
    if not password_ok or not user.is_active:
        return None
    return user


def user_snapshot(user: User) -> dict:
    """Safe description of a user for the audit log. Never includes the password hash."""
    return {
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role.name,
        "is_active": user.is_active,
        "is_synthetic": user.is_synthetic,
    }
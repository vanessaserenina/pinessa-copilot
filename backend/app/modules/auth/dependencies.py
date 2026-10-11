from fastapi import Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.modules.audit.service import record_audit_event
from app.modules.auth.models import User
from app.modules.auth.permissions import ROLE_PERMISSIONS
from app.modules.auth.security import decode_access_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def get_request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def get_current_user(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> User:
    unauthorised = HTTPException(
        status_code=401, detail="Not authenticated", headers={"WWW-Authenticate": "Bearer"}
    )
    user_id = decode_access_token(token)
    if user_id is None:
        raise unauthorised
    user = db.scalar(select(User).options(joinedload(User.role)).where(User.id == user_id))
    if user is None or not user.is_active:
        raise unauthorised
    return user


def require_permission(permission: str):
    """Dependency factory: the endpoint is only open to roles that hold this permission."""

    def checker(
        request: Request,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> User:
        if permission in ROLE_PERMISSIONS.get(user.role.name, frozenset()):
            return user
        record_audit_event(
            db,
            action="permission.denied",
            entity_type="permission",
            actor_user_id=user.id,
            detail={"permission": permission, "method": request.method, "path": request.url.path},
            request_id=get_request_id(request),
        )
        db.commit()
        raise HTTPException(status_code=403, detail="You do not have permission to do this.")

    return checker
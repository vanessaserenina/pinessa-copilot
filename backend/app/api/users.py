from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.modules.audit.service import record_audit_event
from app.modules.auth.dependencies import get_request_id, require_permission
from app.modules.auth.models import Role, User
from app.modules.auth.schemas import UserCreate, UserOut
from app.modules.auth.security import hash_password
from app.modules.auth.service import user_snapshot

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserOut])
def list_users(
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("users:manage")),
) -> list[UserOut]:
    users = db.scalars(select(User).options(joinedload(User.role)).order_by(User.id)).all()
    return [UserOut.from_user(user) for user in users]


@router.post("", response_model=UserOut, status_code=201)
def create_user(
    payload: UserCreate,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("users:manage")),
) -> UserOut:
    role = db.scalar(select(Role).where(Role.name == payload.role))
    if role is None:
        raise HTTPException(status_code=422, detail=f"Unknown role '{payload.role}'")
    user = User(
        email=payload.email,
        full_name=payload.full_name.strip(),
        password_hash=hash_password(payload.password),
        role=role,
        is_synthetic=payload.is_synthetic,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="A user with this email already exists.")
    record_audit_event(
        db,
        action="user.created",
        entity_type="user",
        entity_id=user.id,
        actor_user_id=actor.id,
        after=user_snapshot(user),
        request_id=get_request_id(request),
    )
    db.commit()
    return UserOut.from_user(user)
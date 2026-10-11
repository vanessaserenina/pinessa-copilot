from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.audit.service import record_audit_event
from app.modules.auth.dependencies import get_current_user, get_request_id
from app.modules.auth.models import User
from app.modules.auth.schemas import Token, UserOut
from app.modules.auth.security import create_access_token
from app.modules.auth.service import authenticate_user, normalise_email

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=Token)
def login(
    request: Request,
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
) -> Token:
    user = authenticate_user(db, form.username, form.password)
    if user is None:
        record_audit_event(
            db,
            action="auth.login_failed",
            entity_type="user",
            detail={"email": normalise_email(form.username)[:254]},
            request_id=get_request_id(request),
        )
        db.commit()  # keep the audit row even though the request fails
        raise HTTPException(
            status_code=401,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    record_audit_event(
        db,
        action="auth.login",
        entity_type="user",
        entity_id=user.id,
        actor_user_id=user.id,
        request_id=get_request_id(request),
    )
    db.commit()
    return Token(access_token=create_access_token(user.id))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.from_user(user)
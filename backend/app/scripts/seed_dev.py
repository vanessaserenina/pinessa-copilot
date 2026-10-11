"""Create the roles and one synthetic user per role (DEVELOPMENT ONLY).

    python -m app.scripts.seed_dev

Users are named <role>@pinessa-test.example and share the password in DEV_SEED_PASSWORD.
Safe to run repeatedly: existing roles and users are left untouched.
"""
from __future__ import annotations

import sys

from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.modules.audit.service import record_audit_event
from app.modules.auth.models import Role, User
from app.modules.auth.permissions import ROLE_DESCRIPTIONS, ROLE_PERMISSIONS
from app.modules.auth.security import hash_password
from app.modules.auth.service import user_snapshot


def main() -> int:
    password = settings.dev_seed_password
    if not password or len(password) < 12:
        print("Set DEV_SEED_PASSWORD in .env (at least 12 characters, dev only) and try again.")
        return 1

    with SessionLocal() as session:
        roles: dict[str, Role] = {}
        for name in ROLE_PERMISSIONS:
            role = session.scalar(select(Role).where(Role.name == name))
            if role is None:
                role = Role(name=name, description=ROLE_DESCRIPTIONS[name])
                session.add(role)
                session.flush()
                print(f"role created : {name}")
            roles[name] = role

        for name, role in roles.items():
            email = f"{name}@pinessa-test.example"
            if session.scalar(select(User.id).where(User.email == email)) is not None:
                print(f"user exists  : {email}")
                continue
            user = User(
                email=email,
                full_name=f"TEST {name.capitalize()} (SYNTHETIC)",
                password_hash=hash_password(password),
                role=role,
                is_synthetic=True,
            )
            session.add(user)
            session.flush()
            record_audit_event(
                session,
                action="user.created",
                entity_type="user",
                entity_id=user.id,
                after=user_snapshot(user),
                detail={"source": "seed_dev"},
            )
            print(f"user created : {email}")
        session.commit()
    print("Done. These are synthetic development accounts only.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.auth import hash_password, normalize_username, verify_password  # noqa: E402
from app.auth_models import OperatorAccount, OperatorRole, OperatorRoleGrant  # noqa: E402
from app.database import session_scope  # noqa: E402


def main() -> int:
    raw_username = os.getenv("TIT_BOOTSTRAP_USERNAME")
    password = os.getenv("TIT_BOOTSTRAP_PASSWORD")
    if not raw_username or not password:
        print(
            "TIT_BOOTSTRAP_USERNAME and TIT_BOOTSTRAP_PASSWORD are required.",
            file=sys.stderr,
        )
        return 2
    if len(password) < 12:
        print("TIT_BOOTSTRAP_PASSWORD must contain at least 12 characters.", file=sys.stderr)
        return 2

    username = normalize_username(raw_username)
    created = False
    with session_scope() as db:
        account = db.scalar(select(OperatorAccount).where(OperatorAccount.username == username))
        if account is None:
            account = OperatorAccount(
                operator_id=str(uuid4()),
                username=username,
                display_name=raw_username.strip(),
                password_hash=hash_password(password),
                is_active=True,
            )
            db.add(account)
            db.flush()
            created = True
        elif not account.is_active:
            print("Bootstrap operator exists but is disabled; no changes were made.", file=sys.stderr)
            return 3
        elif not verify_password(password, account.password_hash):
            print(
                "Bootstrap operator already exists; this command does not rotate its password.",
                file=sys.stderr,
            )
            return 3

        existing = {
            item.role: item
            for item in db.scalars(
                select(OperatorRoleGrant).where(OperatorRoleGrant.operator_id == account.operator_id)
            ).all()
        }
        for role in OperatorRole:
            grant = existing.get(role.value)
            if grant is None:
                db.add(
                    OperatorRoleGrant(
                        grant_id=str(uuid4()),
                        operator_id=account.operator_id,
                        role=role.value,
                    )
                )
            elif grant.revoked_at is not None:
                grant.revoked_at = None

    state = "created" if created else "already ready"
    print("Bootstrap operator {}: {}; all operator roles are active.".format(username, state))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

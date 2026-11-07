from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import User
from ..security import (_load_user, audit, current_user, flash, hash_password,
                        password_problem, session_stamp, verify_csrf,
                        verify_password)
from ..web import render

router = APIRouter()


def _safe_next(nxt: str | None) -> str:
    return nxt if nxt and nxt.startswith("/") and not nxt.startswith("//") else "/"


@router.get("/login")
def login_form(request: Request, next: str = "/", db: Session = Depends(get_db)):
    if _load_user(request, db):
        return RedirectResponse(_safe_next(next), 303)
    return render(request, "login.html", next=_safe_next(next))


@router.post("/login", dependencies=[Depends(verify_csrf)])
def login(request: Request, username: str = Form(...), password: str = Form(...),
          next: str = Form("/"), db: Session = Depends(get_db)):
    now = datetime.now()
    user = db.scalar(select(User).where(func.lower(User.username) == username.strip().lower()))
    error = "Usuário ou senha incorretos."

    if user and user.locked_until and user.locked_until > now:
        mins = int((user.locked_until - now).total_seconds() // 60) + 1
        error = f"Acesso bloqueado por excesso de tentativas. Tente de novo em {mins} min."
    elif verify_password(password, user.password_hash if user else None) and user and user.is_active:
        user.failed_logins = 0
        user.locked_until = None
        user.last_login_at = now
        request.session.clear()
        request.session.update(uid=user.id, stamp=session_stamp(user))
        audit(db, request, "login", user=user)
        db.commit()
        return RedirectResponse(_safe_next(next), 303)
    elif user:
        user.failed_logins += 1
        if user.failed_logins >= settings.LOGIN_MAX_ATTEMPTS:
            user.locked_until = now + timedelta(minutes=settings.LOGIN_LOCK_MINUTES)
            user.failed_logins = 0
        audit(db, request, "login_falhou", target=user.username, user=user)
        db.commit()

    return render(request, "login.html", status_code=401, error=error,
                  username=username, next=_safe_next(next))


@router.post("/logout", dependencies=[Depends(verify_csrf)])
def logout(request: Request, db: Session = Depends(get_db)):
    user = _load_user(request, db)
    if user:
        audit(db, request, "logout", user=user)
        db.commit()
    request.session.clear()
    return RedirectResponse("/login", 303)


@router.get("/conta/senha")
def password_form(request: Request, user: User = Depends(current_user)):
    return render(request, "password.html")


@router.post("/conta/senha", dependencies=[Depends(verify_csrf)])
def change_password(request: Request, current: str = Form(...), new: str = Form(...),
                    confirm: str = Form(...), user: User = Depends(current_user),
                    db: Session = Depends(get_db)):
    error = None
    if not verify_password(current, user.password_hash):
        error = "A senha atual está incorreta."
    elif new != confirm:
        error = "A nova senha e a confirmação são diferentes."
    elif new == current:
        error = "A nova senha precisa ser diferente da atual."
    else:
        error = password_problem(new)
    if error:
        return render(request, "password.html", status_code=400, error=error)

    user.password_hash = hash_password(new)
    user.must_change_password = False
    request.session["stamp"] = session_stamp(user)
    audit(db, request, "senha_alterada")
    db.commit()
    flash(request, "Senha alterada.")
    return RedirectResponse("/", 303)

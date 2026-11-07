import re

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User
from ..security import (admin_user, audit, flash, hash_password,
                        password_problem, verify_csrf)
from ..web import render

router = APIRouter(prefix="/usuarios")
USERNAME_RE = re.compile(r"^[a-zA-Z0-9._-]{3,64}$")


def _active_admins(db: Session) -> int:
    return db.scalar(select(func.count(User.id)).where(User.is_admin, User.is_active)) or 0


@router.get("")
def user_list(request: Request, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    users = db.scalars(select(User).order_by(User.is_active.desc(), User.username)).all()
    return render(request, "users.html", users=users)


@router.get("/novo")
def user_new_form(request: Request, admin: User = Depends(admin_user)):
    return render(request, "user_form.html", u=None, form={"is_admin": False, "is_active": True})


@router.post("/novo", dependencies=[Depends(verify_csrf)])
def user_create(request: Request, username: str = Form(...), full_name: str = Form(""),
                password: str = Form(...), is_admin: str = Form(""),
                admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    username = username.strip()
    form = {"username": username, "full_name": full_name, "is_admin": bool(is_admin), "is_active": True}
    error = None
    if not USERNAME_RE.match(username):
        error = "O usuário deve ter de 3 a 64 caracteres: letras, números, ponto, hífen ou sublinhado."
    elif db.scalar(select(User).where(func.lower(User.username) == username.lower())):
        error = "Já existe um usuário com esse nome."
    else:
        error = password_problem(password)
    if error:
        return render(request, "user_form.html", 400, u=None, form=form, error=error)

    u = User(username=username, full_name=full_name.strip(), password_hash=hash_password(password),
             is_admin=bool(is_admin), is_active=True, must_change_password=True)
    db.add(u)
    db.flush()
    audit(db, request, "usuario_criado", target=f"usuario:{u.username}",
          detail="admin" if u.is_admin else None)
    db.commit()
    flash(request, f"Usuário {u.username} criado. Ele vai definir uma nova senha no primeiro acesso.")
    return RedirectResponse("/usuarios", 303)


def _get(db: Session, uid: int) -> User:
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404, "Usuário não encontrado.")
    return u


@router.get("/{uid}")
def user_edit_form(request: Request, uid: int, admin: User = Depends(admin_user),
                   db: Session = Depends(get_db)):
    u = _get(db, uid)
    return render(request, "user_form.html", u=u, form={
        "username": u.username, "full_name": u.full_name, "is_admin": u.is_admin, "is_active": u.is_active})


@router.post("/{uid}", dependencies=[Depends(verify_csrf)])
def user_update(request: Request, uid: int, full_name: str = Form(""), is_admin: str = Form(""),
                is_active: str = Form(""), password: str = Form(""),
                admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    u = _get(db, uid)
    form = {"username": u.username, "full_name": full_name, "is_admin": bool(is_admin),
            "is_active": bool(is_active)}
    losing_admin = u.is_admin and u.is_active and (not is_admin or not is_active)
    if losing_admin and _active_admins(db) <= 1:
        return render(request, "user_form.html", 400, u=u, form=form,
                      error="Este é o único administrador ativo. Promova outro usuário antes.")
    if password and (err := password_problem(password)):
        return render(request, "user_form.html", 400, u=u, form=form, error=err)

    changes = []
    if u.full_name != full_name.strip():
        changes.append("nome")
    if u.is_admin != bool(is_admin):
        changes.append("admin" if is_admin else "sem admin")
    if u.is_active != bool(is_active):
        changes.append("ativado" if is_active else "desativado")
    u.full_name, u.is_admin, u.is_active = full_name.strip(), bool(is_admin), bool(is_active)
    if password:
        u.password_hash = hash_password(password)
        u.must_change_password = u.id != admin.id
        u.failed_logins, u.locked_until = 0, None
        changes.append("senha redefinida")
    if is_active:
        u.locked_until = None
    audit(db, request, "usuario_editado", target=f"usuario:{u.username}", detail=", ".join(changes) or None)
    db.commit()
    if u.id == admin.id and password:
        from ..security import session_stamp
        request.session["stamp"] = session_stamp(u)
    flash(request, f"Usuário {u.username} atualizado.")
    return RedirectResponse("/usuarios", 303)

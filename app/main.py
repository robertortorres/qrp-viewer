import logging
import time
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from .config import settings
from .db import Base, engine
from .models import User
from .routers import auth, patients, users
from .security import LoginRequired, PasswordChangeRequired, flash, hash_password
from .web import BASE, render

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("qrp")


def _wait_for_db(tries: int = 30) -> None:
    for i in range(tries):
        try:
            with engine.connect() as c:
                c.execute(text("select 1"))
            return
        except OperationalError:
            log.info("aguardando o banco de dados (%s/%s)...", i + 1, tries)
            time.sleep(2)
    raise RuntimeError("Banco de dados indisponível.")


def _bootstrap_admin(db) -> None:
    if db.scalar(select(func.count(User.id))):
        return
    pw = settings.ADMIN_PASSWORD
    if not pw:
        log.warning("Nenhum usuário cadastrado e ADMIN_PASSWORD vazio: defina-o no .env.")
        return
    db.add(User(username=settings.ADMIN_USERNAME, full_name="Administrador",
                password_hash=hash_password(pw), is_admin=True, must_change_password=True))
    log.info("Usuário administrador '%s' criado.", settings.ADMIN_USERNAME)


def _init_db() -> None:
    """Cria tabelas e o admin inicial; o lock evita corrida entre workers."""
    with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            conn.execute(text("select pg_advisory_xact_lock(724001)"))
        Base.metadata.create_all(conn)
        with Session(bind=conn) as db:
            _bootstrap_admin(db)
            db.flush()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    _wait_for_db()
    _init_db()
    yield


app = FastAPI(title=settings.APP_NAME, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY, session_cookie="qrp_session",
                   max_age=settings.SESSION_MAX_AGE, same_site="lax", https_only=settings.COOKIE_SECURE)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    if resp.headers.get("content-type", "").startswith("text/html"):
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; frame-src 'self'; object-src 'self'; "
            "frame-ancestors 'self'; base-uri 'self'; form-action 'self'")
    return resp


app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
app.include_router(auth.router)
app.include_router(patients.router)
app.include_router(users.router)


@app.get("/healthz", include_in_schema=False)
def healthz():
    with engine.connect() as c:
        c.execute(text("select 1"))
    return {"status": "ok"}


def _wants_json(request: Request) -> bool:
    return "application/json" in request.headers.get("accept", "")


@app.exception_handler(LoginRequired)
async def _login_required(request: Request, exc: LoginRequired):
    if _wants_json(request):
        return JSONResponse({"detail": "Sessão expirada. Entre novamente."}, 401)
    nxt = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return RedirectResponse(f"/login?next={quote(nxt)}", 303)


@app.exception_handler(PasswordChangeRequired)
async def _pw_required(request: Request, exc: PasswordChangeRequired):
    flash(request, "Defina uma nova senha para continuar.", "info")
    return RedirectResponse("/conta/senha", 303)


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException):
    detail = exc.detail if isinstance(exc.detail, str) else "Erro na requisição."
    if exc.status_code == 404 and detail == "Not Found":
        detail = "Página não encontrada."
    if _wants_json(request):
        return JSONResponse({"detail": detail}, exc.status_code)
    return render(request, "error.html", exc.status_code, status=exc.status_code, detail=detail)


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError):
    detail = "Dados do formulário incompletos ou inválidos."
    if _wants_json(request):
        return JSONResponse({"detail": detail}, 422)
    return render(request, "error.html", 422, status=422, detail=detail)

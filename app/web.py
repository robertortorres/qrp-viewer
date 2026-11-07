"""Utilitários compartilhados pelas rotas: templates e arquivos em disco."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from .config import settings
from .security import csrf_token

BASE = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE / "templates"))


def _fmt_date(v: date | datetime | None) -> str:
    return v.strftime("%d/%m/%Y") if v else "—"


def _fmt_dt(v: datetime | None) -> str:
    return v.strftime("%d/%m/%Y %H:%M") if v else "—"


def _fmt_num(v: float | None, digits: int = 2) -> str:
    return "—" if v is None else f"{v:.{digits}f}".replace(".", ",")


templates.env.filters.update(d=_fmt_date, dt=_fmt_dt, num=_fmt_num)
templates.env.globals["APP_NAME"] = settings.APP_NAME


def render(request: Request, name: str, status_code: int = 200, **ctx):
    ctx.setdefault("user", getattr(request.state, "user", None))
    ctx["csrf"] = csrf_token(request)
    ctx["flashes"] = request.session.pop("flash", [])
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


# ------------------------------------------------------------- arquivos ---

def exam_dir(exam_id: int) -> Path:
    d = settings.DATA_DIR / "exams" / f"{exam_id // 1000:04d}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def qrp_path(exam_id: int) -> Path:
    return exam_dir(exam_id) / f"{exam_id}.qrp"


def pdf_path(exam_id: int) -> Path:
    return exam_dir(exam_id) / f"{exam_id}.pdf"


def write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)

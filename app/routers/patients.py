from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime

from fastapi import (APIRouter, Depends, File, Form, HTTPException, Request,
                     UploadFile)
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import qrp
from ..config import settings
from ..db import get_db
from ..models import Exam, Patient, User, normalize_name
from ..security import admin_user, audit, current_user, flash, verify_csrf
from ..web import pdf_path, qrp_path, render, write_atomic

log = logging.getLogger("qrp")
router = APIRouter()
PAGE_SIZE = 50


# ------------------------------------------------------------ pacientes ---

@router.get("/")
def patient_list(request: Request, q: str = "", page: int = 1,
                 user: User = Depends(current_user), db: Session = Depends(get_db)):
    stats = (select(Exam.patient_id, func.count(Exam.id).label("n"),
                    func.max(Exam.exam_datetime).label("last"))
             .group_by(Exam.patient_id).subquery())
    stmt = (select(Patient, func.coalesce(stats.c.n, 0), stats.c.last)
            .outerjoin(stats, stats.c.patient_id == Patient.id))

    q = q.strip()
    if q:
        dm = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", q)
        if dm:
            d, m, y = dm.groups()
            stmt = stmt.where(Patient.birth_date == datetime(int(y), int(m), int(d)).date())
        else:
            for term in normalize_name(q).split():
                stmt = stmt.where(Patient.name_normalized.contains(term))

    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    page = max(page, 1)
    rows = db.execute(stmt.order_by(stats.c.last.desc().nulls_last(), Patient.name)
                      .limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE)).all()
    return render(request, "patients.html", rows=rows, q=q, page=page,
                  pages=max(1, -(-total // PAGE_SIZE)), total=total)


def _get_patient(db: Session, pid: int) -> Patient:
    p = db.get(Patient, pid)
    if not p:
        raise HTTPException(404, "Paciente não encontrado.")
    return p


@router.get("/pacientes/{pid}")
def patient_detail(request: Request, pid: int, user: User = Depends(current_user),
                   db: Session = Depends(get_db)):
    p = _get_patient(db, pid)
    # agrupa exames do mesmo dia (normalmente OD + OE)
    groups: dict[str, list[Exam]] = {}
    for e in p.exams:
        key = e.exam_datetime.strftime("%d/%m/%Y") if e.exam_datetime else "Sem data"
        groups.setdefault(key, []).append(e)
    for exams in groups.values():
        exams.sort(key=lambda e: (e.eye or "ZZ") != "OD")
    return render(request, "patient.html", p=p, groups=groups)


@router.get("/pacientes/{pid}/editar")
def patient_edit_form(request: Request, pid: int, user: User = Depends(current_user),
                      db: Session = Depends(get_db)):
    p = _get_patient(db, pid)
    return render(request, "patient_edit.html", p=p, form={
        "name": p.name, "birth_date": p.birth_date.isoformat() if p.birth_date else "",
        "notes": p.notes})


@router.post("/pacientes/{pid}/editar", dependencies=[Depends(verify_csrf)])
def patient_edit(request: Request, pid: int, name: str = Form(...), birth_date: str = Form(""),
                 notes: str = Form(""), merge: str = Form(""),
                 user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _get_patient(db, pid)
    form = {"name": name, "birth_date": birth_date, "notes": notes}
    name = re.sub(r"\s+", " ", name).strip()
    if not name:
        return render(request, "patient_edit.html", 400, p=p, form=form, error="Informe o nome.")
    try:
        bd = datetime.strptime(birth_date, "%Y-%m-%d").date() if birth_date else None
    except ValueError:
        return render(request, "patient_edit.html", 400, p=p, form=form, error="Data de nascimento inválida.")

    norm = normalize_name(name)
    other = db.scalar(select(Patient).where(Patient.id != p.id, Patient.name_normalized == norm,
                                            Patient.birth_date == bd)) if bd else None
    if other:
        if not merge:
            return render(request, "patient_edit.html", 409, p=p, form=form, conflict=other)
        if not user.is_admin:
            return render(request, "patient_edit.html", 403, p=p, form=form, conflict=other,
                          error="Só administradores podem unir cadastros.")
        moved = len(p.exams)
        for e in list(p.exams):
            e.patient_id = other.id
        if notes.strip() and notes.strip() not in (other.notes or ""):
            other.notes = ((other.notes or "") + "\n\n" + notes.strip()).strip()
        db.flush()
        db.delete(p)
        audit(db, request, "paciente_unido", target=f"paciente:{other.id}",
              detail=f"paciente {pid} ({p.name}) unido; {moved} exame(s) movido(s)")
        db.commit()
        flash(request, f"Cadastros unidos. {moved} exame(s) movido(s) para este paciente.")
        return RedirectResponse(f"/pacientes/{other.id}", 303)

    p.name, p.name_normalized, p.birth_date, p.notes = name, norm, bd, notes.strip()
    audit(db, request, "paciente_editado", target=f"paciente:{p.id}")
    db.commit()
    flash(request, "Cadastro salvo.")
    return RedirectResponse(f"/pacientes/{p.id}", 303)


@router.post("/pacientes/{pid}/excluir", dependencies=[Depends(verify_csrf)])
def patient_delete(request: Request, pid: int, user: User = Depends(admin_user),
                   db: Session = Depends(get_db)):
    p = _get_patient(db, pid)
    ids = [e.id for e in p.exams]
    audit(db, request, "paciente_excluido", target=f"paciente:{pid}",
          detail=f"{p.name}; {len(ids)} exame(s)")
    db.delete(p)
    db.commit()
    for eid in ids:
        _remove_files(eid)
    flash(request, f"Paciente {p.name} excluído.")
    return RedirectResponse("/", 303)


# --------------------------------------------------------------- upload ---

@router.get("/importar")
def upload_form(request: Request, user: User = Depends(current_user)):
    return render(request, "upload.html", max_mb=settings.MAX_UPLOAD_MB)


def _find_or_create_patient(db: Session, info: qrp.QRPData) -> tuple[Patient, bool]:
    norm = normalize_name(info.patient_name)
    stmt = select(Patient).where(Patient.name_normalized == norm)
    stmt = stmt.where(Patient.birth_date == info.birth_date) if info.birth_date \
        else stmt.where(Patient.birth_date.is_(None))
    p = db.scalar(stmt)
    if p:
        return p, False
    p = Patient(name=re.sub(r"\s+", " ", info.patient_name).strip(), name_normalized=norm,
                birth_date=info.birth_date, notes="")
    db.add(p)
    db.flush()
    return p, True


def import_one(db: Session, request: Request, user: User, filename: str, data: bytes) -> dict:
    res: dict = {"file": filename}
    if not data:
        return res | {"status": "error", "message": "Arquivo vazio."}
    if len(data) > settings.MAX_UPLOAD_MB * 1024 * 1024:
        return res | {"status": "error", "message": f"Arquivo maior que {settings.MAX_UPLOAD_MB} MB."}

    digest = hashlib.sha256(data).hexdigest()
    dup = db.scalar(select(Exam).where(Exam.sha256 == digest))
    if dup:
        return res | {"status": "duplicate", "message": "Este exame já foi importado.",
                      "patient": dup.patient.name, "url": f"/exames/{dup.id}"}
    try:
        info = qrp.parse_qrp(data)
    except qrp.QRPError as exc:
        return res | {"status": "error", "message": f"Não parece um arquivo QRP válido: {exc}"}

    for attempt in range(2):  # repete uma vez se outro upload criar o mesmo paciente
        try:
            patient, created = _find_or_create_patient(db, info)
            exam = Exam(
                patient_id=patient.id, exam_datetime=info.exam_datetime, eye=info.eye,
                age_at_exam=info.age, responsible=info.responsible, clinic=info.clinic,
                program=info.program, strategy=info.strategy, md=info.md, psd=info.psd,
                fixation_loss=info.fixation_loss, false_positive=info.false_positive,
                false_negative=info.false_negative, raw_fields=info.fields,
                original_filename=filename[:255], sha256=digest, qrp_size=len(data),
                uploaded_by_id=user.id, pdf_status="pending",
            )
            db.add(exam)
            db.flush()
            break
        except IntegrityError:
            db.rollback()
            dup = db.scalar(select(Exam).where(Exam.sha256 == digest))
            if dup:
                return res | {"status": "duplicate", "message": "Este exame já foi importado.",
                              "patient": dup.patient.name, "url": f"/exames/{dup.id}"}
            if attempt:
                raise

    write_atomic(qrp_path(exam.id), data)
    _convert(exam, data)
    audit(db, request, "exame_importado", target=f"exame:{exam.id}",
          detail=f"{filename}; paciente {patient.id}")
    db.commit()
    return res | {
        "status": "ok" if exam.pdf_status == "ok" else "error",
        "message": ("Paciente criado." if created else "Adicionado ao cadastro existente.")
        if exam.pdf_status == "ok" else f"Dados importados, mas o PDF falhou: {exam.pdf_error}",
        "patient": patient.name, "patient_url": f"/pacientes/{patient.id}",
        "eye": exam.eye_label, "date": exam.exam_datetime.strftime("%d/%m/%Y %H:%M")
        if exam.exam_datetime else None, "url": f"/exames/{exam.id}",
    }


def _convert(exam: Exam, data: bytes) -> None:
    try:
        title = f"{exam.patient.name} - {exam.eye or ''} - " \
                f"{exam.exam_datetime:%d/%m/%Y}" if exam.exam_datetime else None
        write_atomic(pdf_path(exam.id), qrp.convert_to_pdf(data, title))
        exam.pdf_status, exam.pdf_error = "ok", None
    except Exception as exc:  # noqa: BLE001 - registra qualquer falha de conversão
        log.exception("falha na conversão do exame %s", exam.id)
        exam.pdf_status, exam.pdf_error = "error", str(exc)[:1000]


@router.post("/importar", dependencies=[Depends(verify_csrf)])
async def upload(request: Request, files: list[UploadFile] = File(...),
                 user: User = Depends(current_user), db: Session = Depends(get_db)):
    from starlette.concurrency import run_in_threadpool

    results = []
    for f in files:
        data = await f.read(settings.MAX_UPLOAD_MB * 1024 * 1024 + 1)
        results.append(await run_in_threadpool(import_one, db, request, user,
                                               f.filename or "exame.qrp", data))
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"results": results})
    return render(request, "upload.html", max_mb=settings.MAX_UPLOAD_MB, results=results)


# --------------------------------------------------------------- exames ---

def _get_exam(db: Session, eid: int) -> Exam:
    e = db.get(Exam, eid)
    if not e:
        raise HTTPException(404, "Exame não encontrado.")
    return e


def _download_name(e: Exam, ext: str) -> str:
    base = normalize_name(e.patient.name).replace(" ", "_")
    when = e.exam_datetime.strftime("%Y-%m-%d_%H%M") if e.exam_datetime else f"exame{e.id}"
    return f"{base}_{e.eye or 'olho'}_{when}.{ext}"


@router.get("/exames/{eid}")
def exam_detail(request: Request, eid: int, user: User = Depends(current_user),
                db: Session = Depends(get_db)):
    e = _get_exam(db, eid)
    siblings = [x for x in e.patient.exams if x.id != e.id and x.exam_datetime and e.exam_datetime
                and x.exam_datetime.date() == e.exam_datetime.date()]
    audit(db, request, "exame_visualizado", target=f"exame:{e.id}")
    db.commit()
    return render(request, "exam.html", e=e, p=e.patient, siblings=siblings)


@router.get("/exames/{eid}/pdf")
def exam_pdf(request: Request, eid: int, download: int = 0,
             user: User = Depends(current_user), db: Session = Depends(get_db)):
    e = _get_exam(db, eid)
    path = pdf_path(e.id)
    if e.pdf_status != "ok" or not path.exists():
        raise HTTPException(404, "O PDF deste exame não está disponível.")
    if download:
        audit(db, request, "pdf_baixado", target=f"exame:{e.id}")
        db.commit()
    return FileResponse(path, media_type="application/pdf",
                        filename=_download_name(e, "pdf"),
                        content_disposition_type="attachment" if download else "inline",
                        headers={"Cache-Control": "private, no-store"})


@router.get("/exames/{eid}/qrp")
def exam_qrp(request: Request, eid: int, user: User = Depends(current_user),
             db: Session = Depends(get_db)):
    e = _get_exam(db, eid)
    audit(db, request, "qrp_baixado", target=f"exame:{e.id}")
    db.commit()
    return FileResponse(qrp_path(e.id), media_type="application/octet-stream",
                        filename=_download_name(e, "qrp"),
                        headers={"Cache-Control": "private, no-store"})


@router.post("/exames/{eid}/reconverter", dependencies=[Depends(verify_csrf)])
def exam_reconvert(request: Request, eid: int, user: User = Depends(current_user),
                   db: Session = Depends(get_db)):
    e = _get_exam(db, eid)
    _convert(e, qrp_path(e.id).read_bytes())
    audit(db, request, "pdf_reconvertido", target=f"exame:{e.id}", detail=e.pdf_status)
    db.commit()
    flash(request, "PDF gerado novamente." if e.pdf_status == "ok"
          else f"A conversão falhou: {e.pdf_error}", "ok" if e.pdf_status == "ok" else "error")
    return RedirectResponse(f"/exames/{e.id}", 303)


def _remove_files(eid: int) -> None:
    for path in (qrp_path(eid), pdf_path(eid)):
        path.unlink(missing_ok=True)


@router.post("/exames/{eid}/excluir", dependencies=[Depends(verify_csrf)])
def exam_delete(request: Request, eid: int, user: User = Depends(admin_user),
                db: Session = Depends(get_db)):
    e = _get_exam(db, eid)
    pid = e.patient_id
    audit(db, request, "exame_excluido", target=f"exame:{eid}",
          detail=f"{e.patient.name}; {e.eye}; {e.exam_datetime}; {e.original_filename}")
    db.delete(e)
    db.commit()
    _remove_files(eid)
    flash(request, "Exame excluído.")
    return RedirectResponse(f"/pacientes/{pid}", 303)


# ------------------------------------------------------------ auditoria ---

@router.get("/auditoria")
def audit_view(request: Request, page: int = 1, q: str = "",
               user: User = Depends(admin_user), db: Session = Depends(get_db)):
    from ..models import AuditLog
    stmt = select(AuditLog)
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(AuditLog.username.ilike(like), AuditLog.action.ilike(like),
                              AuditLog.target.ilike(like), AuditLog.detail.ilike(like)))
    page = max(page, 1)
    rows = db.scalars(stmt.order_by(AuditLog.id.desc()).limit(100).offset((page - 1) * 100)).all()
    return render(request, "audit.html", rows=rows, page=page, q=q, has_more=len(rows) == 100)

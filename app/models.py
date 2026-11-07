from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime

from sqlalchemy import (JSON, BigInteger, Boolean, Date, DateTime, Float,
                        ForeignKey, Integer, String, Text, UniqueConstraint,
                        func)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def normalize_name(name: str) -> str:
    """Nome em maiúsculas, sem acentos e espaços repetidos (para deduplicar)."""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().upper()


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(160), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Patient(Base):
    __tablename__ = "patients"
    __table_args__ = (UniqueConstraint("name_normalized", "birth_date", name="uq_patient_identity"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    name_normalized: Mapped[str] = mapped_column(String(200), index=True)
    birth_date: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    exams: Mapped[list[Exam]] = relationship(
        back_populates="patient", cascade="all, delete-orphan",
        order_by="desc(Exam.exam_datetime)")

    def age_on(self, ref: date | None = None) -> int | None:
        if not self.birth_date:
            return None
        ref = ref or date.today()
        b = self.birth_date
        return ref.year - b.year - ((ref.month, ref.day) < (b.month, b.day))


class Exam(Base):
    __tablename__ = "exams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"), index=True)
    exam_datetime: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    eye: Mapped[str | None] = mapped_column(String(2))          # OD / OE
    age_at_exam: Mapped[int | None] = mapped_column(Integer)
    responsible: Mapped[str | None] = mapped_column(String(200))
    clinic: Mapped[str | None] = mapped_column(String(200))
    program: Mapped[str | None] = mapped_column(String(100))
    strategy: Mapped[str | None] = mapped_column(String(100))
    md: Mapped[float | None] = mapped_column(Float)
    psd: Mapped[float | None] = mapped_column(Float)
    fixation_loss: Mapped[str | None] = mapped_column(String(20))
    false_positive: Mapped[str | None] = mapped_column(String(20))
    false_negative: Mapped[str | None] = mapped_column(String(20))
    raw_fields: Mapped[dict] = mapped_column(JSON, default=dict)

    original_filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    qrp_size: Mapped[int] = mapped_column(BigInteger, default=0)
    pdf_status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/ok/error
    pdf_error: Mapped[str | None] = mapped_column(Text)

    uploaded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    patient: Mapped[Patient] = relationship(back_populates="exams")
    uploaded_by: Mapped[User | None] = relationship()

    @property
    def eye_label(self) -> str:
        return {"OD": "Olho direito", "OE": "Olho esquerdo"}.get(self.eye or "", "Olho não informado")

    @staticmethod
    def _ratio(v: str | None) -> float | None:
        try:
            a, b = (v or "").split("/")
            return int(a) / int(b) if int(b) else None
        except ValueError:
            return None

    @property
    def reliability_flags(self) -> list[str]:
        """Critérios usuais de confiabilidade (perda de fixação > 20%, FP/FN > 33%)."""
        flags = []
        r = self._ratio(self.fixation_loss)
        if r is not None and r > 0.20:
            flags.append("Perda de fixação acima de 20%")
        r = self._ratio(self.false_positive)
        if r is not None and r > 0.33:
            flags.append("Falso positivo acima de 33%")
        r = self._ratio(self.false_negative)
        if r is not None and r > 0.33:
            flags.append("Falso negativo acima de 33%")
        return flags


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    username: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(40), index=True)
    target: Mapped[str | None] = mapped_column(String(80))
    detail: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(String(64))

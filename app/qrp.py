"""Leitura e conversão de arquivos QRP (perímetro Eyetec).

Formato observado: um cabeçalho proprietário curto seguido de um EMF
(Enhanced Metafile do Windows) com o relatório impresso. Os textos do
relatório ficam em registros EMR_EXTTEXTOUTW, com coordenadas, o que
permite extrair os campos pareando cada rótulo ("Paciente:") com o
valor à sua direita na mesma linha.

A conversão para PDF é feita pelo LibreOffice (headless) a partir do
EMF extraído, e depois a página é reescalada para preencher o A4.
"""
from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

EMR_HEADER = 1
EMR_EOF = 14
EMR_EXTTEXTOUTW = 84
EMF_SIGNATURE = b" EMF"

A4_W, A4_H = 595.276, 841.89

# O LibreOffice não gosta de muitas instâncias simultâneas.
_convert_slots = threading.BoundedSemaphore(int(os.getenv("CONVERT_CONCURRENCY", "2")))


class QRPError(ValueError):
    """Arquivo não reconhecido como QRP válido."""


@dataclass
class TextItem:
    x: int
    y: int
    text: str


@dataclass
class QRPData:
    patient_name: str | None = None
    birth_date: date | None = None
    age: int | None = None
    exam_datetime: datetime | None = None
    eye: str | None = None            # "OD" | "OE"
    responsible: str | None = None
    clinic: str | None = None
    program: str | None = None
    strategy: str | None = None
    md: float | None = None
    psd: float | None = None
    fixation_loss: str | None = None
    false_positive: str | None = None
    false_negative: str | None = None
    foveal_threshold: str | None = None
    duration: str | None = None
    fields: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------- EMF ---

def find_emf(data: bytes) -> tuple[int, int]:
    """Retorna (offset, tamanho) do EMF embutido no QRP."""
    pos = data.find(EMF_SIGNATURE)
    while pos != -1:
        start = pos - 0x28
        if start >= 0 and len(data) >= start + 0x34:
            rtype, rsize = struct.unpack_from("<II", data, start)
            nbytes = struct.unpack_from("<I", data, start + 0x30)[0]
            if rtype == EMR_HEADER and rsize >= 88 and 0 < nbytes <= len(data) - start:
                return start, nbytes
        pos = data.find(EMF_SIGNATURE, pos + 1)
    raise QRPError("Não encontrei o relatório (EMF) dentro do arquivo.")


def extract_emf(data: bytes) -> bytes:
    start, size = find_emf(data)
    return data[start:start + size]


def iter_texts(emf: bytes) -> list[TextItem]:
    items: list[TextItem] = []
    p, end = 0, len(emf)
    while p + 8 <= end:
        rtype, rsize = struct.unpack_from("<II", emf, p)
        if rsize < 8 or p + rsize > end:
            break
        if rtype == EMR_EXTTEXTOUTW and rsize >= 76:
            x, y = struct.unpack_from("<ii", emf, p + 36)
            nchars, off = struct.unpack_from("<II", emf, p + 44)
            raw = emf[p + off:p + off + 2 * nchars]
            text = raw.decode("utf-16le", "replace").strip()
            if text:
                items.append(TextItem(x, y, text))
        if rtype == EMR_EOF:
            break
        p += rsize
    return items


# ------------------------------------------------------------ parsing ---

def _norm_label(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]+", " ", s.lower()).strip()


def pair_fields(items: list[TextItem], y_tol: int = 4) -> dict[str, str]:
    """Pareia cada rótulo terminado em ':' com o texto mais próximo à direita."""
    labels = [t for t in items if t.text.endswith(":")]
    values = [t for t in items if not t.text.endswith(":")]
    out: dict[str, str] = {}
    for lab in labels:
        # o próximo rótulo na mesma linha limita a busca
        limit = min((o.x for o in labels if o is not lab and abs(o.y - lab.y) <= y_tol and o.x > lab.x),
                    default=10 ** 9)
        cands = [v for v in values if abs(v.y - lab.y) <= y_tol and lab.x < v.x < limit]
        key = _norm_label(lab.text)
        if cands and key not in out:
            out[key] = min(cands, key=lambda v: v.x).text
    return out


def _parse_date(s: str | None) -> date | None:
    try:
        return datetime.strptime(s.strip(), "%d/%m/%Y").date() if s else None
    except ValueError:
        return None


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            pass
    return None


def _parse_float(s: str | None) -> float | None:
    try:
        return float(s.replace(",", ".")) if s else None
    except ValueError:
        return None


def _parse_int(s: str | None) -> int | None:
    m = re.search(r"\d+", s or "")
    return int(m.group()) if m else None


def _parse_eye(s: str | None) -> str | None:
    n = _norm_label(s or "")
    if "direit" in n:
        return "OD"
    if "esquerd" in n:
        return "OE"
    return None


def parse_qrp(data: bytes) -> QRPData:
    emf = extract_emf(data)
    f = pair_fields(iter_texts(emf))
    g = f.get
    q = QRPData(
        patient_name=(g("paciente") or "").strip() or None,
        birth_date=_parse_date(g("data nascimento")),
        age=_parse_int(g("idade")),
        exam_datetime=_parse_dt(g("data e hora")),
        eye=_parse_eye(g("olho")),
        responsible=g("responsavel"),
        clinic=g("clinica"),
        program=g("programa"),
        strategy=g("estrategia"),
        md=_parse_float(g("md")),
        psd=_parse_float(g("psd")),
        fixation_loss=g("perda de fixacao"),
        false_positive=g("falso positivo"),
        false_negative=g("falso negativo"),
        foveal_threshold=g("limiar foveal"),
        duration=g("duracao"),
        fields=f,
    )
    if not q.patient_name:
        raise QRPError("O arquivo não traz o nome do paciente.")
    return q


# ---------------------------------------------------------- conversão ---

def _fit_to_a4(src: Path, dst: Path, margin: float = 14.0) -> None:
    """Recorta a área desenhada e a centraliza/escala no A4."""
    import pypdf
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(src))
    try:
        page = doc[0]
        w, h = page.get_size()
        sc = 2
        img = page.render(scale=sc).to_pil().convert("L")
        bbox = img.point(lambda v: 255 if v < 245 else 0).getbbox()
    finally:
        doc.close()
    if not bbox:
        shutil.copyfile(src, dst)
        return
    x0, y0, x1, y1 = bbox
    left, right = x0 / sc, x1 / sc
    bottom, top = h - y1 / sc, h - y0 / sc
    bw, bh = right - left, top - bottom
    s = min((A4_W - 2 * margin) / bw, (A4_H - 2 * margin) / bh)
    tx = (A4_W - bw * s) / 2 - left * s
    ty = A4_H - margin - bh * s - bottom * s

    reader = pypdf.PdfReader(str(src))
    writer = pypdf.PdfWriter()
    pg = reader.pages[0]
    pg.add_transformation(pypdf.Transformation().scale(s, s).translate(tx, ty))
    box = pypdf.generic.RectangleObject([0, 0, A4_W, A4_H])
    pg.mediabox = box
    pg.cropbox = box
    writer.add_page(pg)
    writer.add_metadata({"/Producer": "QRP Viewer"})
    with open(dst, "wb") as fh:
        writer.write(fh)


def convert_to_pdf(data: bytes, title: str | None = None, timeout: int = 120) -> bytes:
    """Converte o conteúdo de um QRP em PDF A4."""
    emf = extract_emf(data)
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise RuntimeError("LibreOffice (soffice) não está instalado.")
    with _convert_slots, tempfile.TemporaryDirectory(prefix="qrp_") as tmp:
        tmpd = Path(tmp)
        (tmpd / "exam.emf").write_bytes(emf)
        cmd = [
            soffice,
            f"-env:UserInstallation=file://{tmpd / 'profile'}",
            "--headless", "--norestore", "--nologo", "--nolockcheck",
            "--convert-to", "pdf:draw_pdf_Export",
            "--outdir", str(tmpd), str(tmpd / "exam.emf"),
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout,
                              env={**os.environ, "HOME": str(tmpd)})
        raw = tmpd / "exam.pdf"
        if proc.returncode != 0 or not raw.exists():
            msg = (proc.stderr or proc.stdout or b"").decode(errors="replace")[-500:]
            raise RuntimeError(f"Falha na conversão pelo LibreOffice: {msg}")
        out = tmpd / "final.pdf"
        _fit_to_a4(raw, out)
        if title:
            _set_title(out, title)
        return out.read_bytes()


def _set_title(path: Path, title: str) -> None:
    import pypdf
    r = pypdf.PdfReader(str(path))
    w = pypdf.PdfWriter(clone_from=r)
    w.add_metadata({"/Title": title})
    with open(path, "wb") as fh:
        w.write(fh)


if __name__ == "__main__":  # uso: python -m app.qrp arquivo.qrp [saida.pdf]
    import json
    import sys

    blob = Path(sys.argv[1]).read_bytes()
    info = parse_qrp(blob)
    print(json.dumps({k: str(v) for k, v in info.__dict__.items() if k != "fields"},
                     indent=2, ensure_ascii=False))
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_bytes(convert_to_pdf(blob, info.patient_name))
        print("PDF gravado em", sys.argv[2])

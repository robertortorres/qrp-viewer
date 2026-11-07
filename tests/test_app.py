"""Testes de ponta a ponta.

Precisam de um PostgreSQL (TEST_DATABASE_URL) e de um arquivo QRP real
em QRP_SAMPLE — que NÃO deve ser versionado (contém dados de paciente).

    TEST_DATABASE_URL=postgresql+psycopg://qrp:qrp@localhost:5432/qrp_test \
    QRP_SAMPLE=/caminho/exame.qrp pytest -v
"""
import os
import re
import tempfile
from pathlib import Path

import pytest

DB = os.getenv("TEST_DATABASE_URL")
SAMPLE = os.getenv("QRP_SAMPLE")
pytestmark = pytest.mark.skipif(not DB, reason="defina TEST_DATABASE_URL")

if DB:
    os.environ.update(DATABASE_URL=DB, DATA_DIR=tempfile.mkdtemp(), SECRET_KEY="test",
                      COOKIE_SECURE="false", ADMIN_USERNAME="admin", ADMIN_PASSWORD="senha-inicial")


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from app import models  # noqa: F401 - registra as tabelas no metadata
    from app.db import Base, engine
    Base.metadata.drop_all(engine)
    from app.main import app
    with TestClient(app) as c:
        yield c


def csrf(c, url="/login"):
    return re.search(r'name="csrf_token" value="([^"]+)"', c.get(url).text).group(1)


def login(c, user, pw):
    return c.post("/login", data={"username": user, "password": pw, "csrf_token": csrf(c)},
                  follow_redirects=False)


def test_requires_login(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")


def test_csrf_enforced(client):
    r = client.post("/login", data={"username": "admin", "password": "x", "csrf_token": "bad"})
    assert r.status_code == 400


def test_admin_first_login_forces_password_change(client):
    assert login(client, "admin", "errada").status_code == 401
    r = login(client, "admin", "senha-inicial")
    assert r.status_code == 303
    r = client.get("/", follow_redirects=False)
    assert r.headers["location"] == "/conta/senha"
    r = client.post("/conta/senha", data={"current": "senha-inicial", "new": "nova-senha-123",
                                          "confirm": "nova-senha-123",
                                          "csrf_token": csrf(client, "/conta/senha")},
                    follow_redirects=False)
    assert r.status_code == 303
    assert client.get("/").status_code == 200


@pytest.mark.skipif(not SAMPLE, reason="defina QRP_SAMPLE")
def test_import_and_pdf(client):
    data = Path(SAMPLE).read_bytes()
    tok = csrf(client, "/importar")
    r = client.post("/importar", files={"files": ("exame.QRP", data)},
                    headers={"X-CSRF-Token": tok, "Accept": "application/json"})
    res = r.json()["results"][0]
    assert res["status"] == "ok", res
    exam_url = res["url"]

    # mesmo arquivo de novo → duplicado
    r = client.post("/importar", files={"files": ("copia.QRP", data)},
                    headers={"X-CSRF-Token": tok, "Accept": "application/json"})
    assert r.json()["results"][0]["status"] == "duplicate"

    # arquivo inválido
    r = client.post("/importar", files={"files": ("x.QRP", b"lixo" * 100)},
                    headers={"X-CSRF-Token": tok, "Accept": "application/json"})
    assert r.json()["results"][0]["status"] == "error"

    assert client.get(exam_url).status_code == 200
    pdf = client.get(exam_url + "/pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert "inline" in pdf.headers["content-disposition"]
    dl = client.get(exam_url + "/pdf?download=1")
    assert "attachment" in dl.headers["content-disposition"]
    assert client.get(exam_url + "/qrp").content == data

    page = client.get(res["patient_url"]).text
    assert res["patient"] in page


def test_user_management_and_permissions(client):
    tok = csrf(client, "/usuarios/novo")
    r = client.post("/usuarios/novo", data={"username": "recepcao", "full_name": "Recepção",
                                            "password": "temporaria1", "csrf_token": tok},
                    follow_redirects=False)
    assert r.status_code == 303

    # não pode remover o último admin
    from app.db import SessionLocal
    from app.models import User
    with SessionLocal() as db:
        admin_id = db.query(User).filter_by(username="admin").one().id
    r = client.post(f"/usuarios/{admin_id}", data={"full_name": "x", "is_active": "1",
                                                   "csrf_token": csrf(client, f"/usuarios/{admin_id}")})
    assert r.status_code == 400

    client.post("/logout", data={"csrf_token": csrf(client, "/")})
    assert login(client, "recepcao", "temporaria1").status_code == 303
    client.post("/conta/senha", data={"current": "temporaria1", "new": "minha-senha-1",
                                      "confirm": "minha-senha-1",
                                      "csrf_token": csrf(client, "/conta/senha")})
    assert client.get("/").status_code == 200
    assert client.get("/usuarios").status_code == 403
    assert client.get("/auditoria").status_code == 403

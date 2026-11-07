import os
import secrets
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on", "sim"}


class Settings:
    APP_NAME = os.getenv("APP_NAME", "QRP Viewer")
    DATABASE_URL = os.getenv(
        "DATABASE_URL", "postgresql+psycopg://qrp:qrp@db:5432/qrp"
    )
    DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
    SECRET_KEY = os.getenv("SECRET_KEY") or ""
    # Atrás do Nginx Proxy Manager com HTTPS: deixe True.
    COOKIE_SECURE = _bool("COOKIE_SECURE", True)
    SESSION_MAX_AGE = int(os.getenv("SESSION_MAX_AGE", str(8 * 3600)))
    MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "50"))
    ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
    LOGIN_MAX_ATTEMPTS = int(os.getenv("LOGIN_MAX_ATTEMPTS", "5"))
    LOGIN_LOCK_MINUTES = int(os.getenv("LOGIN_LOCK_MINUTES", "15"))


settings = Settings()

if not settings.SECRET_KEY:
    # Sem SECRET_KEY as sessões caem a cada reinício; avisa e segue.
    settings.SECRET_KEY = secrets.token_urlsafe(48)
    print("AVISO: SECRET_KEY não definida; usando uma chave temporária.", flush=True)

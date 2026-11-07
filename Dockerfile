FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data

# LibreOffice Draw (sem interface) converte o EMF do QRP em PDF.
# O laudo Eyetec usa Tahoma; docker/fonts.conf a substitui pela Inter.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libreoffice-draw-nogui fonts-inter fonts-dejavu-core fontconfig \
 && rm -rf /var/lib/apt/lists/*
COPY docker/fonts.conf /etc/fonts/conf.d/60-tahoma.conf
RUN fc-cache -f

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

RUN useradd --system --uid 10001 --home-dir /app qrp \
 && mkdir -p /data && chown qrp:qrp /data
USER qrp

EXPOSE 8000
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status==200 else 1)"

# --proxy-headers: confia no X-Forwarded-For/Proto do Nginx Proxy Manager
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--workers", "2"]

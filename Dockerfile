# Dockerfile del validador DOI2BIB.
#
# Imagen base: Playwright oficial "latest" (Chromium + dependencias ya
# instaladas en /ms-playwright). Todo corre DENTRO del contenedor: la
# navegación es headless y jamás abre una ventana en el host.
FROM mcr.microsoft.com/playwright:latest

ENV PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    DJANGO_SETTINGS_MODULE=config.settings

# Ubuntu noble marca el python del sistema como externally-managed (PEP 668):
# usamos un venv propio.
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    # No-op si la versión de pip coincide con la imagen; descarga el build
    # correcto si la imagen "latest" quedó desincronizada.
    && playwright install chromium

COPY . .

RUN mkdir -p /app/data /app/media

EXPOSE 8000

ENTRYPOINT ["/bin/bash", "entrypoint.sh"]

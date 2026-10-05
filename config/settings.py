"""
Configuración Django del validador DOI2BIB.

Herramienta LOCAL mono-usuario empaquetada en Docker (imagen base
mcr.microsoft.com/playwright:latest). Todo lo sensible se configura por
variables de entorno (ver .env.example); no hay secretos hardcodeados.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "local-no-secreto-validador-doi2bib")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "referencias",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(os.environ.get("DB_PATH", BASE_DIR / "data" / "db.sqlite3")),
    }
}

AUTH_PASSWORD_VALIDATORS = []

LANGUAGE_CODE = "es"
TIME_ZONE = "America/Santiago"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"

_MEDIA = Path(os.environ.get("MEDIA_ROOT", BASE_DIR / "media"))
_MEDIA.mkdir(parents=True, exist_ok=True)
MEDIA_ROOT = _MEDIA
MEDIA_URL = "media/"

_DB = Path(str(DATABASES["default"]["NAME"]))
_DB.parent.mkdir(parents=True, exist_ok=True)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Tamaño máximo del .bib subido: 20 MB (bibliografías grandes con abstracts).
DATA_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024

# ---------------------------------------------------------------- config pipe
BONSAI_BASE_URL = os.environ.get("BONSAI_BASE_URL", "http://host.docker.internal:4687/v1")
BONSAI_MODEL = os.environ.get("BONSAI_MODEL", "ternary-bonsai-8b")
BONSAI_TIMEOUT = int(os.environ.get("BONSAI_TIMEOUT", "180"))

DOI2BIB_TABS = int(os.environ.get("DOI2BIB_TABS", "10"))
DOI2BIB_MIN_INTERVALO = float(os.environ.get("DOI2BIB_MIN_INTERVALO", "1.0"))
DOI2BIB_REINTENTOS = int(os.environ.get("DOI2BIB_REINTENTOS", "2"))
DOI2BIB_PAUSA_RATE_LIMIT = float(os.environ.get("DOI2BIB_PAUSA_RATE_LIMIT", "60"))

BUSCADOR_TABS = int(os.environ.get("BUSCADOR_TABS", "2"))
BUSCADOR_DELAY = float(os.environ.get("BUSCADOR_DELAY", "2.5"))
CANDIDATOS_A_VERIFICAR = int(os.environ.get("CANDIDATOS_A_VERIFICAR", "4"))

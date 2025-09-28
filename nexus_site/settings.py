from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = 'dev-secret-key-change-me'
DEBUG = True
ALLOWED_HOSTS = ['*']

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'grimorio.apps.GrimorioConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'nexus_site.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'nexus_site.wsgi.application'

# DATABASE via dj-database-url (Postgres se houver DATABASE_URL; senão, SQLite)
import dj_database_url
from urllib.parse import urlparse

db_url = os.getenv("DATABASE_URL", "").strip()

if db_url:
    scheme = urlparse(db_url).scheme.lower()
    is_pg = scheme.startswith("postgres")
    DATABASES = {
        "default": dj_database_url.parse(
            db_url,
            conn_max_age=600,
            ssl_require=is_pg and bool(os.getenv("RENDER")),
        )
    }
else:
    # Fallback 100% SQLite (sem sslmode)
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

AUTH_PASSWORD_VALIDATORS = []

LANGUAGE_CODE = 'pt-br'
TIME_ZONE = 'America/Sao_Paulo'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATICFILES_DIRS = []

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ==== Deploy helpers (Render) ====
import os
from pathlib import Path
BASE_DIR = Path(__file__).resolve().parent.parent

# DEBUG/SECRET_KEY a partir de env (usa defaults seguros p/ dev)
DEBUG = os.getenv("DEBUG", "False").lower() == "true" or globals().get("DEBUG", False)
SECRET_KEY = os.getenv("SECRET_KEY", globals().get("SECRET_KEY", "dev-unsafe-change-me"))

# ALLOWED_HOSTS dinâmico: pega do próprio settings (se houver) + Render
_render_host = []
_render_external_url = os.getenv("RENDER_EXTERNAL_URL")
if _render_external_url:
    # ex: https://meuapp.onrender.com -> host = meuapp.onrender.com
    _render_host = [_render_external_url.split("://", 1)[-1]]
# Se já havia ALLOWED_HOSTS definido acima, preserva e acrescenta:
ALLOWED_HOSTS = list(set(globals().get("ALLOWED_HOSTS", []) + _render_host + ["localhost", "127.0.0.1"]))

# CSRF_TRUSTED_ORIGINS coerente com hosts
CSRF_TRUSTED_ORIGINS = []
if _render_external_url:
    CSRF_TRUSTED_ORIGINS = [_render_external_url]
else:
    # útil se você usar domínio próprio depois
    CSRF_TRUSTED_ORIGINS = [f"https://{h}" for h in ALLOWED_HOSTS if "." in h]

# STATIC
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

# WhiteNoise logo após SecurityMiddleware
try:
    MIDDLEWARE  # noqa
except NameError:
    MIDDLEWARE = []
if "django.middleware.security.SecurityMiddleware" not in MIDDLEWARE:
    MIDDLEWARE.insert(0, "django.middleware.security.SecurityMiddleware")
if "whitenoise.middleware.WhiteNoiseMiddleware" not in MIDDLEWARE:
    sec_idx = MIDDLEWARE.index("django.middleware.security.SecurityMiddleware")
    MIDDLEWARE.insert(sec_idx + 1, "whitenoise.middleware.WhiteNoiseMiddleware")

# DATABASE via dj-database-url (Postgres no Render; SQLite como fallback)
import dj_database_url  # noqa: E402
DATABASES = {
    "default": dj_database_url.config(
        default=os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
        conn_max_age=600,
        ssl_require=bool(os.getenv("RENDER")),
    )
}

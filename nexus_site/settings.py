from pathlib import Path
import os
from urllib.parse import urlparse

# Dependências de DB (instale via requirements.txt)
import dj_database_url

# =========================
# Caminhos base
# =========================
BASE_DIR = Path(__file__).resolve().parent.parent

# =========================
# Segurança / Debug
# =========================
# Defina SECRET_KEY nas variáveis de ambiente em produção
SECRET_KEY = os.getenv("SECRET_KEY", "dev-unsafe-change-me")

# DEBUG por env (padrão False em produção)
DEBUG = os.getenv("DEBUG", "False").lower() == "true"

# ALLOWED_HOSTS dinâmico:
# - inclui o host do Render (RENDER_EXTERNAL_URL)
# - inclui localhost/127.0.0.1 para dev
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "").strip()
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]
if RENDER_EXTERNAL_URL:
    host = RENDER_EXTERNAL_URL.split("://", 1)[-1]
    if host:
        ALLOWED_HOSTS.append(host)

# CSRF_TRUSTED_ORIGINS coerente com hosts (https)
CSRF_TRUSTED_ORIGINS = []
if RENDER_EXTERNAL_URL:
    # Ex.: https://meuapp.onrender.com
    CSRF_TRUSTED_ORIGINS = [RENDER_EXTERNAL_URL]
else:
    # Caso você adicione domínio próprio no futuro
    CSRF_TRUSTED_ORIGINS = [f"https://{h}" for h in ALLOWED_HOSTS if "." in h]

# Em proxies (Render), ajuda a detectar HTTPS corretamente
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# =========================
# Apps
# =========================
INSTALLED_APPS = [
    # Django
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # App do projeto
    "grimorio",
]

# =========================
# Middlewares
# =========================
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # WhiteNoise logo após SecurityMiddleware
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# =========================
# URLs / WSGI
# =========================
ROOT_URLCONF = "nexus_site.urls"
WSGI_APPLICATION = "nexus_site.wsgi.application"

# =========================
# Templates
# =========================
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        # Se você também tiver um diretório de templates na raiz, adicione aqui:
        "DIRS": [
            # BASE_DIR / "templates",
        ],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# =========================
# Banco de Dados
# - Usa Postgres se DATABASE_URL existir
# - Fallback segura para SQLite (sem sslmode)
# =========================
db_url = os.getenv("DATABASE_URL", "").strip()
if db_url:
    scheme = urlparse(db_url).scheme.lower()
    is_pg = scheme.startswith("postgres")

    # (debug opcional) deixe ligado se quiser ver nos logs do Render
    print(
        f"[DB] DATABASE_URL detectado (scheme={scheme}) -> usando dj_database_url.parse; "
        f"ssl_require={'on' if (is_pg and bool(os.getenv('RENDER'))) else 'off'}"
    )

    DATABASES = {
        "default": dj_database_url.parse(
            db_url,
            conn_max_age=600,
            ssl_require=is_pg and bool(os.getenv("RENDER")),
        )
    }
else:
    print("[DB] DATABASE_URL não definido -> usando SQLite local (db.sqlite3)")
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

# =========================
# Senhas / Autenticação
# =========================
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# =========================
# Localização / Tempo
# =========================
LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Maceio"
USE_I18N = True
USE_TZ = True

# =========================
# Arquivos estáticos
# =========================
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Se você tiver assets “globais” fora dos apps, adicione aqui:
# STATICFILES_DIRS = [ BASE_DIR / "static" ]

# WhiteNoise: arquivos comprimidos + cache busting
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

# =========================
# Arquivos de mídia (se vier a usar)
# =========================
# MEDIA_URL = "/media/"
# MEDIA_ROOT = BASE_DIR / "media"

# =========================
# Padrão de chave primária
# =========================
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# =========================
# Logging básico (opcional, útil no Render)
# =========================
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {"class": "logging.StreamHandler"},
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO" if not DEBUG else "DEBUG",
    },
}

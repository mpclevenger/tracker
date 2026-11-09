import os
import sys
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

# Buildpacks run `collectstatic` at build time, often without config vars; a placeholder key is fine there.
_BUILD_TIME = "collectstatic" in sys.argv or "pytest" in sys.modules
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY") or ("build-or-test-only-key" if _BUILD_TIME else "")
if not SECRET_KEY:
    raise RuntimeError("DJANGO_SECRET_KEY must be set")
DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"
ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if h.strip()
]
CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()
]

INSTALLED_APPS = [
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
    "tracker",
    "meetings",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.auth.middleware.ActorMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.actor",
                "core.context_processors.modules",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": dj_database_url.config(
        default="sqlite:///build.sqlite3" if _BUILD_TIME else "postgres://tracker:tracker@localhost:5432/tracker",
        conn_max_age=60,
        ssl_require=os.environ.get("DATABASE_SSL_REQUIRE", "0") == "1",
    )
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "America/New_York"
USE_I18N = False
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
# Hashed filenames need collectstatic (done in the Docker build); skip them in DEBUG and tests.
_USE_MANIFEST = not DEBUG and "pytest" not in sys.modules
STORAGES = {
    "staticfiles": {
        "BACKEND": (
            "whitenoise.storage.CompressedManifestStaticFilesStorage"
            if _USE_MANIFEST
            else "django.contrib.staticfiles.storage.StaticFilesStorage"
        ),
    },
}

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

from django.contrib.messages import constants as message_constants

MESSAGE_TAGS = {message_constants.ERROR: "danger"}

# --- Application settings -------------------------------------------------

# open: every request is Tech Risk (build/test env). dev: user switcher. sso: core/auth/sso.py hooks.
AUTH_MODE = os.environ.get("AUTH_MODE") or ("dev" if "pytest" in sys.modules else "open")
if AUTH_MODE not in ("open", "dev", "sso"):
    raise RuntimeError(f"AUTH_MODE must be open, dev or sso (got {AUTH_MODE!r})")
DEV_DEFAULT_EMP_ID = os.environ.get("DEV_DEFAULT_EMP_ID", "")
OPEN_MODE_EMP_ID = os.environ.get("OPEN_MODE_EMP_ID", "")
OPEN_MODE_DISPLAY_NAME = os.environ.get("OPEN_MODE_DISPLAY_NAME", "")
# sso mode: trusted headers injected by the entitlement add-on (see core/auth/sso.py)
SSO_EMP_ID_HEADER = os.environ.get("SSO_EMP_ID_HEADER", "")
SSO_GROUPS_HEADER = os.environ.get("SSO_GROUPS_HEADER", "")
# e.g. SSO_GROUP_ROLE_MAP="APP-RiskTracker-TechRisk=tech_risk,APP-RiskTracker-Auditor=auditor"
SSO_GROUP_ROLE_MAP = dict(
    pair.split("=", 1) for pair in os.environ.get("SSO_GROUP_ROLE_MAP", "").split(",") if "=" in pair
)

# Calendar-day thresholds, evaluated in America/New_York. Overridable per key in ThresholdConfig.
SITE_URL = os.environ.get("SITE_URL", "").rstrip("/")  # used in copied reminder links
CHECKIN_INTERVAL_DAYS = 14  # owners are asked to check in every two weeks

RAG_THRESHOLD_DEFAULTS = {
    "amber_key_milestone_slip_days": 30,
    "amber_go_live_window_days": 90,
    "amber_stale_days": 30,
    "red_key_milestone_slip_days": 60,
    "red_go_live_slip_days": 30,
    "red_go_live_window_days": 60,
    "red_stale_days": 60,
    "early_warning_stale_days": 21,
}

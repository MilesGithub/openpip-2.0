from pathlib import Path
from urllib.parse import urlsplit
from datetime import timedelta
import environ

env = environ.Env()

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Read .env before any env() calls so DATABASE_URL etc. are available
environ.Env.read_env(BASE_DIR.parent / ".env", overwrite=False)

SECRET_KEY = env("SECRET_KEY", default="django-insecure-dev-key-change-in-production")

DEBUG = False

# The address the site is reached at, e.g. https://openpip.example.org or
# https://openpip.example.org/openpip. Host checks, trusted origins, the URL
# prefix and HTTPS enforcement (prod.py) all follow from it; empty means a local
# install at http://localhost.
PUBLIC_URL = env("PUBLIC_URL", default="").rstrip("/")
PUBLIC_URL_PARTS = urlsplit(PUBLIC_URL) if PUBLIC_URL else None

# An explicit, non-empty ALLOWED_HOSTS or CSRF_TRUSTED_ORIGINS wins; compose
# passes them through as empty strings when unset, which also means "derive".
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[]) or [
    "localhost",
    "127.0.0.1",
    *(
        [PUBLIC_URL_PARTS.hostname]
        if PUBLIC_URL_PARTS and PUBLIC_URL_PARTS.hostname
        else []
    ),
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "drf_spectacular",
    "corsheaders",
    "django_filters",
    "core",
    "proteins",
    "interactions",
    "datasets",
    "admin_panel",
    "psicquic",
    "sharing",
    "graph",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "openpip.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
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

WSGI_APPLICATION = "openpip.wsgi.application"

DATABASES = {
    "default": env.db("DATABASE_URL", default="sqlite:///dev.db"),
}

AUTH_USER_MODEL = "core.User"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# Relative, so Django prefixes it with FORCE_SCRIPT_NAME (from PUBLIC_URL);
# "/static/" would send the browser outside the app's path.
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "mediafiles"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.CursorPagination",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "PAGE_SIZE": 50,
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    # Anonymous limits are per IP, and a whole university sits behind one NAT,
    # so the browsing rate has to be generous: a single search page fires a
    # dozen requests and several people share an address. These are sized to
    # stop scripted hammering, not to police normal use.
    #
    # psicquic is deliberately tighter — its callers are machines, and a full
    # crawl of the database at maxResults=200 is ~615 requests, which still
    # finishes inside 11 minutes at this rate.
    "DEFAULT_THROTTLE_RATES": {
        "anon": "600/min",
        "user": "1200/min",
        "psicquic": "60/min",
        "security_answer": "10/hour",
        "security_answer_account": "10/hour",
    },
}

# DRF throttling counts through the cache, so with gunicorn's 3 workers a
# local-memory cache would give each worker its own counter and triple every
# limit. Redis is already a hard dependency here (Celery), and Django ships a
# Redis backend, so no new package.
CACHES = {
    "default": (
        {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": env("REDIS_URL", default="redis://localhost:6379/0"),
        }
        if env("REDIS_URL", default="")
        else {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
    )
}

SPECTACULAR_SETTINGS = {
    "TITLE": "openPIP API",
    "DESCRIPTION": (
        "Protein-protein interaction database API. "
        "All read endpoints are public and require no authentication. "
        "PSICQUIC-compliant query endpoint available at /psicquic/rest/query."
    ),
    "VERSION": "2.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "LICENSE": {"name": "MIT"},
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=1),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
}

# Deliberately open: the API serves public data, and login tokens travel in a
# header, not a cookie, so another site's page cannot act as a signed-in user.
CORS_ALLOW_ALL_ORIGINS = True
CORS_URLS_REGEX = r"^/(api|psicquic)/.*$"
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[]) or (
    [f"{PUBLIC_URL_PARTS.scheme}://{PUBLIC_URL_PARTS.netloc}"]
    if PUBLIC_URL_PARTS
    else []
)

# openPIP sends no mail: account recovery runs on security questions, so there
# is no EMAIL_* configuration to get wrong. Anything added here that does need
# to send will have to set the mail backend up first.

# Celery
CELERY_BROKER_URL = env("REDIS_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = env("REDIS_URL", default="redis://localhost:6379/0")
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_TRACK_STARTED = True
CELERY_RESULT_EXPIRES = 3600  # 1 hour

# The typed relationship graph (backend/graph). Off by default: while it is off
# its commands refuse to write. The app is installed and migrated either way,
# so the migration state never depends on the switch.
TYPED_GRAPH_ENABLED = env.bool("TYPED_GRAPH_ENABLED", default=False)

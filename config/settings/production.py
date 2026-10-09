"""
Production settings. Works on any host (VPS, Docker) and on Vercel.

On Vercel the deployment hostnames are added to ALLOWED_HOSTS and
CSRF_TRUSTED_ORIGINS automatically from the VERCEL_* system variables, and the
database settings suit a pooled Postgres connection (e.g. Neon via the Vercel
Marketplace).
"""

import os

from .base import *  # noqa: F401,F403
from .base import ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS, DATABASES, config

DEBUG = False

# ---------------------------------------------------------------------------
# Hosts
# ---------------------------------------------------------------------------
# Vercel exposes the production domain, the branch URL and the per-deployment
# URL (hostname only, no scheme). Accept them so the project works before a
# custom domain is attached and on preview deployments.
_vercel_hosts = [
    os.environ.get(name, "").strip() for name in ("VERCEL_PROJECT_PRODUCTION_URL", "VERCEL_BRANCH_URL", "VERCEL_URL")
]
for _host in filter(None, _vercel_hosts):
    if _host not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_host)
    _origin = f"https://{_host}"
    if _origin not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(_origin)

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
# Pooled connections (Neon "-pooler" hosts, PgBouncer, Supabase pooler) run in
# transaction mode, which does not support server-side cursors.
DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = config("DB_DISABLE_SERVER_SIDE_CURSORS", default=True, cast=bool)

# ---------------------------------------------------------------------------
# HTTPS and cookies
# ---------------------------------------------------------------------------
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = config("SECURE_SSL_REDIRECT", default=True, cast=bool)
SECURE_HSTS_SECONDS = config("SECURE_HSTS_SECONDS", default=31536000, cast=int)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = "DENY"

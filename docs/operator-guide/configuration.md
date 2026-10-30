# Configuration reference

openPIP is configured through environment variables, read when each container
starts, plus a few build-time variables baked into the web interface. This
page lists every variable the code reads. A test in the backend suite fails
if a variable is added to the code without being listed here, or listed here
without being read.

## Where values come from

When you run openPIP with Docker Compose (the supported way), Compose fills in
`${...}` placeholders in `docker-compose.yml` and `docker-compose.prod.yml`
from a file named **`.env` in the repository root**. Start from the example:

```bash
cp .env.example .env
```

The Django settings also read that root `.env` directly when you run
`manage.py` on the host, outside Docker. Variables already set in the
environment take precedence over the file. The `backend/.env.example` file is
not read by anything.

## The one setting most deployments need

**`PUBLIC_URL`** is the address people use to reach your portal, without a
trailing slash: `https://openpip.example.org`, or
`https://www.example.org/openpip` for a portal under a path. Everything
deployment-specific follows from it:

| Derived from `PUBLIC_URL` | Example for `https://www.example.org/openpip` |
|---|---|
| The path the site is served under | `/openpip` |
| Allowed host names | `www.example.org` (plus `localhost` and `127.0.0.1`) |
| Origins trusted for Django admin forms | `https://www.example.org` |
| HTTPS enforcement (redirect, secure cookies, HSTS) | on, because the address starts with `https://` |
| The documentation site's address, and the address in its examples | `https://www.example.org/openpip/docs/` |
| The admin panel's starting **Site URL** | `https://www.example.org/openpip/`, set on first start |

Leave it **unset** to run openPIP on your own computer at
`http://localhost:8080`, with HTTPS enforcement off. Changing it means
rebuilding the images (`up -d --build`), because the web interface and
documentation take their paths from it when they are built.

## Backend variables

Read by `backend/openpip/settings/`.

| Variable | Default when unset | What it controls |
|---|---|---|
| `PUBLIC_URL` | empty: a local install | See [above](#the-one-setting-most-deployments-need). Also passed to the frontend image when it is built. |
| `SECRET_KEY` | `django-insecure-dev-key-change-in-production` | Signs sessions, password-reset tokens and **every login token (JWT)**. Anyone who knows it can mint an administrator token. Production settings refuse to start if it begins with `django-insecure`. |
| `DATABASE_URL` | `sqlite:///dev.db` | Database connection, as a URL: `postgres://USER:PASSWORD@HOST:5432/NAME`. Compose sets it for you from `DB_PASSWORD`. |
| `REDIS_URL` | unset | Redis connection, used as the Celery job queue and as the cache that stores rate-limit counters. Compose sets it to `redis://redis:6379/0`. Without it, rate limits are counted separately in each worker process. |
| `ALLOWED_HOSTS` | derived from `PUBLIC_URL` (development allows any host) | Optional override: comma-separated host names the site may be reached by. Set it only if the site has more names than `PUBLIC_URL`'s. |
| `CSRF_TRUSTED_ORIGINS` | derived from `PUBLIC_URL` | Optional override: comma-separated origins trusted for form posts to the Django admin. |
| `TYPED_GRAPH_ENABLED` | off | Turns on the typed relationship graph, which is still being built and shows visitors nothing yet. With it on, staff also see the typed graph's catalogue and licensing records in the Django admin, as their Django permissions allow: data sources, datasets, publications, releases, licence snapshots, due-diligence records and approval decisions, with releases and approval decisions read-only, beside the graph's registries, also read-only. While it is off, the Django admin shows none of them, and `manage.py graph_sync_registry`, which loads the registries, refuses to run. `1`, `true`, `yes` or `on` turns it on. |

### Variables used only by Docker Compose

| Variable | Default when unset | What it controls |
|---|---|---|
| `DB_PASSWORD` | `openpip_dev` | Password of the `openpip` Postgres user. Postgres applies it **only when the database volume is first created**. Changing it later needs an `ALTER USER` as well (see [Security checklist](#security-checklist)). |
| `DJANGO_SETTINGS_MODULE` | set by the compose files | `openpip.settings.dev` in `docker-compose.yml`, `openpip.settings.prod` in `docker-compose.prod.yml`. Do not set it in `.env`. |

## Web interface build variables

Read by Vite when the web interface is **built**: in the Docker image, from
`PUBLIC_URL`; with `npm run dev`, from `frontend/.env.development`.

| Variable | Default when unset | What it controls |
|---|---|---|
| `VITE_BASE` | the path of `PUBLIC_URL`, or `/` | URL path the interface is served under. Normally left unset: the build derives it from `PUBLIC_URL`. |
| `VITE_API_BASE_URL` | `<VITE_BASE>api` | Where the interface sends API requests. Leave unset in production. Development sets `/api`, which the dev server proxies to the backend on port 8001. |
| `VITE_USE_MSW` | mock API **on** in development | Development only: any value except `false` replaces the real API with built-in mock data. `frontend/.env.development` sets `false`. |

## Fixed settings

Browser requests to `/api/` and `/psicquic/` are accepted from any origin
(CORS). This is deliberate: the data is public, and login tokens are sent in a
header, not as cookies, so another site cannot act for a signed-in user.

These are set in code, not by environment variables.

| Setting | Value | Where |
|---|---|---|
| Login token lifetime | access 1 hour, refresh 7 days; refresh tokens rotate on use | `settings/base.py` `SIMPLE_JWT` |
| Rate limits | anonymous 600/min, signed-in 1200/min, PSICQUIC 60/min, security-question answers 10/hour per caller and 10/hour per email address | `settings/base.py` `DEFAULT_THROTTLE_RATES` |
| HTTPS (production) | when `PUBLIC_URL` is `https://`: HTTPS redirect, secure cookies, HSTS 1 hour; trusts `X-Forwarded-Proto: https` from the proxy | `settings/prod.py` |
| Client address (production) | the last address in `X-Forwarded-For`, which your HTTPS proxy must set | `settings/prod.py` `NUM_PROXIES` |
| Upload size limit | 500 MB per request | `frontend/nginx.conf` `client_max_body_size` |
| Media files | `backend/mediafiles/` (development); the `media` volume (production) | `settings/base.py` `MEDIA_ROOT` |

## Generating a secret key

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(50))"
```

Put the output in `.env` as `SECRET_KEY=...`. Changing the key signs everyone
out, because every existing login token becomes invalid.

## Security checklist

Before exposing a deployment:

- [ ] `SECRET_KEY` is a newly generated value, not the example.
- [ ] `DB_PASSWORD` is set to a strong value **before the first start**.
- [ ] `PUBLIC_URL` is your site's `https://` address, and `ALLOWED_HOSTS` and
      `CSRF_TRUSTED_ORIGINS` are unset or name only your domain.
- [ ] No port is published on a public interface. With both compose files,
      `docker compose ps` shows no port for `db` and `redis`, and only
      `127.0.0.1:` for `frontend` and `backend`.


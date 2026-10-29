# Contributing to openPIP

Thank you for helping. This guide covers setting up a development copy,
the checks every change must pass, and how changes are proposed.

## Setting up

You need Docker with Compose 2.24.4 or later, Python 3.12, and Node.js 20.19+
(or 22.12+).

```bash
git clone https://github.com/Helmy-Lab/openpip-2.0.git
cd openpip-2.0
cp .env.example .env
echo 'DATABASE_URL=postgres://openpip:openpip_dev@localhost:5432/openpip' >> .env

docker compose up -d            # Postgres, Redis, the dev API on :8001, the worker

cd frontend && npm ci && npm run dev    # http://localhost:5173
```

`DATABASE_URL` in `.env` lets tools you run on the host, such as the test
suite and `manage.py`, reach the Postgres container. Without it, they silently
fall back to a local SQLite file.

Turn on the repository's git hook, which lints staged files:

```bash
git config core.hooksPath scripts/git-hooks
```

For the backend tools on the host:

```bash
cd backend
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

## Checks every change must pass

Run these before opening a pull request. Reviewers run them too.

### Backend

```bash
cd backend
venv/bin/pytest                         # needs the Postgres container running
venv/bin/ruff check .
venv/bin/black --check .
venv/bin/python manage.py makemigrations --check --dry-run
```

pytest creates and discards its own test database. Parity tests, which compare
against the original openPIP, are excluded by default. Run them with
`pytest -m parity`.

pytest's header shows the database engine (`database: postgresql` or
`database: sqlite`). Without `DATABASE_URL` it runs on SQLite, says so at the
end, and skips the tests marked `postgres`.

### Frontend

```bash
cd frontend
npx vitest run       # npm run test starts watch mode
npm run lint
npm run build        # type-checks, then builds
```

On a busy machine some tests can overrun their 5 s limit. CI runs one file at
a time and allows 20 s per test:
`npx vitest run --no-file-parallelism --testTimeout=20000`.

### Documentation

The documentation site lives in `docs/`, with `mkdocs.yml` at the repository
root.

```bash
python3 -m venv .venv-docs
.venv-docs/bin/pip install -r docs/requirements.txt
.venv-docs/bin/mkdocs serve             # preview at http://127.0.0.1:8000
.venv-docs/bin/mkdocs build --strict    # must pass: fails on broken links
```

Two backend tests keep the docs honest: every environment variable the code
reads must appear in the configuration reference, and every API endpoint
the server routes must have a heading in the API reference (and the
reverse). If you add, rename or remove either, update the docs in the same
change, or `pytest` fails.

Write docs for what the code does now. Check each claim against the code or a
running portal, and use real responses in examples.

### Continuous integration

Every push and pull request runs these checks in GitHub Actions
(`.github/workflows/ci.yml`):

- the backend tests on PostgreSQL 16 and Redis 7, then ruff, black and the
  migration check;
- the frontend lint, Vitest (one file at a time, 20 s per test) and build;
- `mkdocs build --strict`.

A CI run stops if the tests would use any database other than PostgreSQL, or
if it selects the parity tests. On a fork, enable workflows once in the
repository's Actions tab.

## Making a change

- **Branch** from `main`: `feature/<name>`, `fix/<name>`, `docs/<name>` or
  `migrate/<area>`. Do not push to `main` directly. Changes reach it through
  pull requests.
- **Commits** follow [Conventional Commits](https://www.conventionalcommits.org/):
  `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`, `migrate:`, in the
  imperative mood (`fix: keep the score when an interaction is reused`).
- **Tests**: a bug fix comes with a test that fails without the fix. Tests live
  next to the code they cover. Keep test fixtures under 1 MB.
- **Migrations** are never edited once merged. Fix a mistake with a new
  migration.
- **Style**: Python is formatted by black at line length 88, with type hints on
  public functions. TypeScript follows the ESLint and Prettier configuration,
  with types on API responses.

## Scope

openPIP 2.0 is being built in phases. The first phase reproduces the original
openPIP's behaviour, and anything beyond that is planned with the project
mentors. Before starting a new feature, open an issue to discuss it.

## Reporting problems

Open an issue describing what you did, what you expected, and what happened.
Report security problems privately to the maintainers rather than in a public
issue.

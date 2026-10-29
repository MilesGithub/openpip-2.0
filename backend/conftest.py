import os
from pathlib import Path

import pytest


@pytest.fixture
def api_client():
    from rest_framework.test import APIClient

    return APIClient()


@pytest.fixture
def admin_user(db):
    from core.models import User

    return User.objects.create_superuser("admin", "admin@example.com", "adminpass123")


@pytest.fixture
def regular_user(db):
    from core.models import User

    return User.objects.create_user("testuser", "test@example.com", "testpass123")


@pytest.fixture
def auth_client(api_client, admin_user):
    from rest_framework_simplejwt.tokens import RefreshToken

    refresh = RefreshToken.for_user(admin_user)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {str(refresh.access_token)}")
    return api_client


@pytest.fixture
def user_auth_client(api_client, regular_user):
    from rest_framework_simplejwt.tokens import RefreshToken

    refresh = RefreshToken.for_user(regular_user)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {str(refresh.access_token)}")
    return api_client


# ── Which database the tests run on ─────────────────────────────────────────
# Without DATABASE_URL the settings fall back silently to SQLite, which accepts
# code that PostgreSQL rejects. A local run may still use it, and says so; a CI
# run stops instead.


def _in_ci() -> bool:
    # GitHub Actions, like most CI services, sets CI=true.
    return os.environ.get("CI", "").strip().lower() in {"1", "true", "yes"}


def _database_vendor() -> str:
    try:
        from django.db import connection

        return connection.vendor
    except Exception as exc:  # e.g. the database driver failed to load
        return f"unknown ({type(exc).__name__}: {exc})"


def _hint(vendor: str) -> str:
    # The fallback is SQLite; any other vendor already names its own cause.
    if vendor == "sqlite":
        return " Set DATABASE_URL (see CONTRIBUTING.md)."
    return ""


def pytest_report_header(config):
    return f"database: {_database_vendor()}"


def pytest_sessionstart(session):
    vendor = _database_vendor()
    if _in_ci() and vendor != "postgresql":
        pytest.exit(
            f"CI runs the tests on PostgreSQL, but the database is {vendor}."
            + _hint(vendor),
            returncode=pytest.ExitCode.USAGE_ERROR,
        )


def pytest_terminal_summary(terminalreporter):
    vendor = _database_vendor()
    if vendor != "postgresql":
        terminalreporter.write_sep("=", "not PostgreSQL", yellow=True)
        terminalreporter.write_line(
            f"These tests ran on {vendor}, not PostgreSQL, so any tests marked "
            "postgres were skipped." + _hint(vendor)
        )


def pytest_runtest_setup(item):
    if item.get_closest_marker("postgres"):
        vendor = _database_vendor()
        if vendor != "postgresql":
            pytest.skip("needs PostgreSQL." + _hint(vendor))


# Parity tests read whatever database is configured, call the original openPIP
# site, and one of them writes the site settings. Every test under tests/parity
# counts as one, marked or not, so pytest.ini deselects it; and a CI run that
# selects any stops before one runs.
PARITY_DIR = Path(__file__).parent / "tests" / "parity"


@pytest.hookimpl(tryfirst=True)  # before pytest.ini's -m deselects by marker
def pytest_collection_modifyitems(items):
    for item in items:
        if PARITY_DIR in item.path.parents:
            item.add_marker(pytest.mark.parity)


def pytest_collection_finish(session):
    if not _in_ci():
        return
    parity = [item for item in session.items if item.get_closest_marker("parity")]
    if parity:
        pytest.exit(
            f"CI does not run the parity tests ({len(parity)} selected).",
            returncode=pytest.ExitCode.USAGE_ERROR,
        )

"""The published docs must match the code they describe.

Each test compares something the code defines with the page that documents it,
in both directions, so a variable or endpoint cannot be added, renamed or
removed without the docs failing here first.
"""

import os
import re
from pathlib import Path

import pytest
from django.conf import settings

REPO = Path(settings.BASE_DIR).parent
DOCS = REPO / "docs"

# Containers only mount backend/, so these run from a full checkout. CI always
# has one, so there they run, and fail if docs/ is missing, instead of skipping.
IN_CI = os.environ.get("CI", "").strip().lower() in {"1", "true", "yes"}
pytestmark = pytest.mark.skipif(
    not DOCS.is_dir() and not IN_CI, reason="needs the full repository checkout"
)


def documented_names(page: str) -> set[str]:
    """Names in the first column of the page's tables: | `NAME` | ..."""
    text = (DOCS / page).read_text()
    return set(re.findall(r"^\| `([A-Z][A-Z0-9_]+)` \|", text, re.MULTILINE))


def test_every_environment_variable_is_documented_and_real():
    settings_src = "".join(
        p.read_text() for p in (REPO / "backend/openpip/settings").glob("*.py")
    )
    backend = set(re.findall(r"env(?:\.\w+)?\(\s*\"([A-Z][A-Z0-9_]+)\"", settings_src))

    compose_src = "".join(p.read_text() for p in REPO.glob("docker-compose*.yml"))
    compose = set(re.findall(r"\$\{([A-Z][A-Z0-9_]+)", compose_src))
    compose |= {"DJANGO_SETTINGS_MODULE"}  # set literally by both compose files

    frontend_src = "".join(
        p.read_text()
        for p in [REPO / "frontend/vite.config.ts", *REPO.glob("frontend/src/**/*.ts*")]
    )
    frontend = set(
        re.findall(r"(?:import\.meta\.env|\benv)\.(VITE_[A-Z0-9_]+)", frontend_src)
    )

    in_code = backend | compose | frontend
    documented = documented_names("operator-guide/configuration.md")
    assert in_code - documented == set(), "read by the code but not documented"
    assert documented - in_code == set(), "documented but not read by the code"


# The router's API root lists the other sharing routes and is not an interface
# anyone builds on; Django admin and media files are not API endpoints.
UNDOCUMENTED_ROUTES = {("GET", "/api/")}
HTTP_METHODS = ("get", "post", "put", "patch", "delete")


def _served_endpoints() -> set[tuple[str, str]]:
    from django.urls import URLResolver, get_resolver

    def walk(patterns, prefix=""):
        for p in patterns:
            if isinstance(p, URLResolver):
                yield from walk(p.url_patterns, prefix + str(p.pattern))
            else:
                yield prefix + str(p.pattern), p.callback

    found = set()
    for raw, callback in walk(get_resolver().url_patterns):
        path = re.sub(r"\(\?P<(\w+)>[^)]*\)", r"{\1}", raw)
        path = re.sub(r"<(?:\w+:)?(\w+)>", r"{\1}", path)
        path = "/" + path.replace("^", "").replace("$", "")
        if "{format}" in path or path.startswith(("/django-admin", "/media")):
            continue
        actions = getattr(callback, "actions", None)
        view = getattr(callback, "view_class", None) or getattr(callback, "cls", None)
        if actions:
            methods = actions
        elif view is not None:
            methods = [m for m in HTTP_METHODS if hasattr(view, m)]
        else:
            methods = []
        # DRF adds "head" to a viewset's actions on its first request, so
        # only the methods the reference documents are compared.
        found |= {(m.upper(), path) for m in methods if m.lower() in HTTP_METHODS}
    return found - UNDOCUMENTED_ROUTES


def _documented_endpoints() -> set[tuple[str, str]]:
    heading = re.compile(
        r"^#{2,4} (GET|POST|PUT|PATCH|DELETE) (/\S+)\s*$", re.MULTILINE
    )
    pages = [*(DOCS / "developer/api").glob("*.md"), DOCS / "developer/psicquic.md"]
    return {(m, p) for page in pages for m, p in heading.findall(page.read_text())}


def test_every_endpoint_is_documented_and_real():
    served, documented = _served_endpoints(), _documented_endpoints()
    assert sorted(served - documented) == [], "served but not documented"
    assert sorted(documented - served) == [], "documented but not served"

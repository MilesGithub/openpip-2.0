"""GET /api/graph/v1/status (spec §7.1): its body in both switch states, what
it reads, and each features key against the routes that are served, so a key
that says a group is served while no route does fails here, and so does a new
route whose key was not flipped.
"""

import pytest

from graph.api.limits import (
    API_VERSION,
    MAX_RELEASES,
    Q_MAX_CHARS,
    RESOLVE_MAX_CANDIDATES,
    RESOLVE_MAX_TERMS,
)
from graph.registry import REGISTRY_VERSION
from graph.tests.api_helpers import BASE, STATUS, typed_routes

# Each key of features, and the prefixes of the paths that serve its group.
GROUPS = {
    "resolve": ("resolve",),
    "entities": ("entities/",),
    "network": ("network",),
    "relationships": ("relationships/",),
    "browsers": ("browsers",),
    "datasets": ("datasets", "releases/"),
    "summary": ("summary",),
    "exports": ("exports/",),
}


def limits(settings) -> dict:
    rates = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
    return {
        "q_max_chars": Q_MAX_CHARS,
        "resolve_max_terms": RESOLVE_MAX_TERMS,
        "resolve_max_candidates": RESOLVE_MAX_CANDIDATES,
        "max_releases": MAX_RELEASES,
        "throttles": {
            "graph_read": rates["graph_read"],
            "graph_resolve": rates["graph_resolve"],
        },
    }


def test_while_off_it_says_so_and_reads_no_table(
    db, settings, visitor_client, django_assert_num_queries
):
    settings.TYPED_GRAPH_ENABLED = False
    with django_assert_num_queries(0):
        response = visitor_client.get(STATUS)
    assert response.status_code == 200
    assert response.json() == {
        "typed_graph": False,
        "api_version": API_VERSION,
        "registry_version": {"code": REGISTRY_VERSION, "database": None},
        "features": dict.fromkeys(GROUPS, False),
        "limits": limits(settings),
    }


def test_while_on_before_any_sync_the_database_version_is_null(
    switch_on, settings, visitor_client, django_assert_num_queries
):
    with django_assert_num_queries(1):
        body = visitor_client.get(STATUS).json()
    assert body["typed_graph"] is True
    assert body["api_version"] == API_VERSION
    assert body["registry_version"] == {"code": REGISTRY_VERSION, "database": None}
    assert body["limits"] == limits(settings)
    assert set(body) == {
        "typed_graph",
        "api_version",
        "registry_version",
        "features",
        "limits",
    }


def test_after_the_sync_both_registry_versions_show(
    registry, visitor_client, django_assert_num_queries
):
    with django_assert_num_queries(1):
        body = visitor_client.get(STATUS).json()
    assert body["registry_version"] == {
        "code": REGISTRY_VERSION,
        "database": REGISTRY_VERSION,
    }


@pytest.mark.parametrize("on,queries", [(False, 0), (True, 1)])
def test_a_real_token_costs_nothing_since_status_reads_none(
    on, queries, settings, user_auth_client, django_assert_num_queries
):
    settings.TYPED_GRAPH_ENABLED = on
    with django_assert_num_queries(queries):
        response = user_auth_client.get(STATUS)
    assert response.status_code == 200


@pytest.mark.parametrize("on", (False, True))
def test_an_unknown_parameter_is_400_in_both_states(on, db, settings, visitor_client):
    settings.TYPED_GRAPH_ENABLED = on
    response = visitor_client.get(f"{STATUS}?foo=1")
    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Unknown parameter 'foo'. This route takes no parameters."
    )


def test_each_feature_key_says_whether_its_routes_are_served(switch_on, visitor_client):
    features = visitor_client.get(STATUS).json()["features"]
    served = {path.removeprefix(BASE) for _, path, _ in typed_routes()}
    assert set(features) == set(GROUPS)
    for key, prefixes in GROUPS.items():
        assert features[key] == any(path.startswith(prefixes) for path in served), key

"""The base view of /api/graph/v1/ (graph.api.base; spec §2, §3 and §8),
through the served routes: every route subclasses it, the switch, the schema,
tokens, the 400 body, Vary, the trailing slash, format and the throttles. The
throttle classes no route carries yet are tested on views no URL serves.

Rates are patched on the class that reads them, as THROTTLE_RATES is bound
when the class is defined; each test starts with empty counters
(conftest.fresh_throttles).
"""

import ast
import inspect
import json
import re
from pathlib import Path

import pytest
from django.apps import apps
from django.core.cache import cache
from django.http import QueryDict
from drf_spectacular.drainage import GENERATOR_STATS
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle
from rest_framework_simplejwt.tokens import RefreshToken

from core.throttling import EveryCallerRateThrottle
from graph.api import throttling
from graph.api.base import TypedGraphView
from graph.api.params import MAX_PROBLEMS, Params
from graph.api.serializers import ParameterErrorSerializer
from graph.api.throttling import (
    GraphNetworkBudgetThrottle,
    GraphNetworkThrottle,
    GraphReadThrottle,
    GraphResolveThrottle,
)
from graph.api.views import StatusView
from graph.models import ApprovalScope, EntityStatus
from graph.tests.api_helpers import (
    BASE,
    ENTITY,
    EVIDENCE,
    NETWORK,
    PATHS,
    RELATIONSHIP,
    REQUIRED,
    RESOLVE,
    SAMPLES,
    STATUS,
    VOCABULARIES,
    entity_url,
    public,
    published,
    reachable,
    resolve_url,
    typed_routes,
)
from graph.tests.factories import EntityFactory

ROUTES = list(typed_routes())
ROUTE_IDS = [f"{method} {path}" for method, path, _ in ROUTES]
# The routes that read a token.
AUTHENTICATED = [path for _, path, _ in ROUTES if path != STATUS]
SCHEMA = "/api/schema/"
NOT_FOUND = {"detail": "Not found."}
BAD_TOKEN = "Bearer not-a-token"


def view_of(path):
    return next(view for _, served, view in ROUTES if served == path)


def history(scope) -> list:
    """What this scope has counted for the test client's address."""
    return cache.get(f"throttle_{scope}_127.0.0.1", [])


# ── Every route is the base view's ──────────────────────────────────────────


def test_the_walk_finds_the_served_routes():
    # Parametrizing over an empty list is a skip, not a failure: this keeps a
    # wrong prefix from turning the switch's acceptance test into a skip.
    assert PATHS <= {path for _, path, _ in ROUTES}


def test_every_api_graph_route_subclasses_the_base_view():
    assert [view for _, _, view in ROUTES if not issubclass(view, TypedGraphView)] == []
    assert [path for _, path, view in ROUTES if view.answers_while_off] == [STATUS]


def test_the_api_resolves_only_through_a_release_set():
    # None means every entity, which fails open for the next view that forgets
    # the arguments.
    files = sorted((Path(apps.get_app_config("graph").path) / "api").glob("*.py"))
    calls = 0
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "attr", getattr(node.func, "id", None))
            if name in ("resolve", "resolve_many"):
                calls += 1
                given = {keyword.arg for keyword in node.keywords}
                assert {"releases", "label_releases"} <= given, (path, node.lineno)
    assert calls


# ── The switch ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("method,path,view", ROUTES, ids=ROUTE_IDS)
def test_with_the_switch_off_every_route_but_status_is_404(
    method, path, view, db, settings, visitor_client, django_assert_num_queries
):
    settings.TYPED_GRAPH_ENABLED = False
    # The gate answers before a value of the path is read.
    url = re.sub(r"\{[^}]+\}", "1", path)
    with django_assert_num_queries(0):
        response = visitor_client.generic(method, url)
    if path == STATUS:
        assert response.status_code == 200
        assert response.json()["typed_graph"] is False
    else:
        assert response.status_code == 404
        assert response.json() == NOT_FOUND


@pytest.mark.parametrize(
    "method,query,headers",
    [
        ("GET", "", {"HTTP_ACCEPT": "text/html"}),
        ("GET", "", {"HTTP_ACCEPT": "application/xml"}),
        ("GET", "?format=api", {}),
        ("GET", "?format=api", {"HTTP_ACCEPT": "text/html"}),
        ("GET", "?format=xml", {}),
        ("GET", "", {"HTTP_AUTHORIZATION": BAD_TOKEN}),
        ("POST", "", {}),
        ("OPTIONS", "", {}),
    ],
)
def test_the_off_404_is_json_whatever_is_asked(
    method, query, headers, db, settings, visitor_client, django_assert_num_queries
):
    settings.TYPED_GRAPH_ENABLED = False
    with django_assert_num_queries(0):
        response = visitor_client.generic(method, f"{VOCABULARIES}{query}", **headers)
    assert response.status_code == 404
    assert response["Content-Type"] == "application/json"
    assert response.json() == NOT_FOUND


# ── The schema ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("on", (False, True))
def test_the_schema_is_served_whatever_the_switch(on, db, settings, visitor_client):
    settings.TYPED_GRAPH_ENABLED = on
    response = visitor_client.get(SCHEMA, {"format": "json"})
    assert response.status_code == 200
    assert PATHS <= set(response.json()["paths"])
    # The legacy plain APIViews already produce errors, and a substring test
    # for StatusView would hit the legacy AsyncImportStatusView.
    names = "|".join(cls.__name__ for cls in TypedGraphView.__subclasses__())
    typed = re.compile(rf"\[(?:{names})\b|/api/graph/")
    stats = (*GENERATOR_STATS._warn_cache, *GENERATOR_STATS._error_cache)
    assert not [message for message in stats if typed.search(message)]


@pytest.mark.parametrize("method,path,view", ROUTES, ids=ROUTE_IDS)
def test_the_schema_parameters_are_the_allowed_ones(
    method, path, view, db, visitor_client
):
    schema = visitor_client.get(SCHEMA, {"format": "json"}).json()
    operation = schema["paths"][path][method.lower()]
    assert {
        parameter["name"]
        for parameter in operation.get("parameters", [])
        if parameter["in"] == "query"
    } == set(view.allowed_params)
    assert set(operation["responses"]) == {"200", "400"}


# ── Tokens ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", AUTHENTICATED)
def test_a_bad_bearer_token_is_401_only_while_on(path, db, settings, visitor_client):
    visitor_client.credentials(HTTP_AUTHORIZATION=BAD_TOKEN)
    settings.TYPED_GRAPH_ENABLED = True
    assert visitor_client.get(reachable(path)).status_code == 401
    settings.TYPED_GRAPH_ENABLED = False
    assert visitor_client.get(reachable(path)).status_code == 404


@pytest.mark.parametrize("on,queries", [(False, 0), (True, 1)])
def test_a_bad_bearer_token_still_gets_status(
    on, queries, db, settings, visitor_client, django_assert_num_queries
):
    settings.TYPED_GRAPH_ENABLED = on
    visitor_client.credentials(HTTP_AUTHORIZATION=BAD_TOKEN)
    # The one query is RegistryState: no user row is read.
    with django_assert_num_queries(queries):
        response = visitor_client.get(STATUS)
    assert response.status_code == 200


def test_only_status_authenticates_nobody():
    assert [path for _, path, view in ROUTES if not view.authentication_classes] == [
        STATUS
    ]


def test_a_real_staff_token_sees_what_staff_see(demo, visitor_client):
    # force_authenticate swaps in DRF's own authenticator, so it would hide a
    # view that authenticates nobody.
    term = {"q": "SYN-TF1"}
    page = entity_url(demo.entity("SYN-TF1"))
    assert visitor_client.get(RESOLVE, term).json()["results"][0]["status"] == (
        "unmatched"
    )
    assert visitor_client.get(page).status_code == 404
    token = RefreshToken.for_user(demo.alice).access_token
    visitor_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    assert visitor_client.get(RESOLVE, term).json()["results"][0]["status"] == (
        "resolved"
    )
    assert visitor_client.get(page).status_code == 200


def test_a_logged_in_non_staff_user_is_a_visitor(demo, user_auth_client):
    body = user_auth_client.get(RESOLVE, {"q": "SYN-TF1"}).json()
    assert body["results"][0]["status"] == "unmatched"


# ── Parameters and errors ───────────────────────────────────────────────────


@pytest.mark.parametrize("name", ("foo", "browser"))
@pytest.mark.parametrize("path", sorted(SAMPLES))
def test_an_unknown_parameter_is_400_with_the_allowed_names(
    path, name, switch_on, visitor_client
):
    allowed = list(view_of(path).allowed_params)
    listed = "This route takes no parameters."
    if allowed:
        listed = f"Allowed: {', '.join(allowed)}."
    query = "&".join(filter(None, (REQUIRED.get(path), f"{name}=x")))
    response = visitor_client.get(f"{reachable(path)}?{query}")
    assert response.status_code == 400
    assert response.json() == {
        "detail": f"Unknown parameter '{name}'. {listed}",
        "errors": [
            {"parameter": name, "value": None, "allowed": allowed, "limit": None}
        ],
    }


def test_a_400_body_keeps_its_types(switch_on, visitor_client):
    # A DRF ValidationError would turn null into "None" and 200 into "200".
    term = "x" * 201
    response = visitor_client.get(RESOLVE, {"q": term, "foo": 1})
    assert response.status_code == 400
    body = json.loads(response.content)
    allowed = ["q", "namespace", "taxon", "entity_type", "releases"]
    assert body == {
        "detail": (
            f"Unknown parameter 'foo'. Allowed: {', '.join(allowed)}. "
            "q terms are at most 200 characters."
        ),
        "errors": [
            {"parameter": "foo", "value": None, "allowed": allowed, "limit": None},
            {"parameter": "q", "value": term, "allowed": None, "limit": 200},
        ],
    }
    assert body == ParameterErrorSerializer(instance=body).data


def test_every_problem_is_listed_once(registry, visitor_client):
    response = visitor_client.get(f"{RESOLVE}?q=&entity_type=chemical&foo=1")
    assert response.status_code == 400
    allowed = ["q", "namespace", "taxon", "entity_type", "releases"]
    types = ["gene", "protein", "protein_isoform"]
    assert response.json() == {
        "detail": (
            f"Unknown parameter 'foo'. Allowed: {', '.join(allowed)}. "
            "q names at least one term. "
            f"Unknown entity_type 'chemical'. Allowed: {', '.join(types)}."
        ),
        "errors": [
            {"parameter": "foo", "value": None, "allowed": allowed, "limit": None},
            {"parameter": "q", "value": None, "allowed": None, "limit": None},
            {
                "parameter": "entity_type",
                "value": "chemical",
                "allowed": types,
                "limit": None,
            },
        ],
    }


@pytest.mark.parametrize(
    "query,parameter,detail",
    [
        ("q=a%00b", "q", "q holds a control character."),
        ("q=a%1Fb", "q", "q holds a control character."),
        ("q=a%C2%85b", "q", "q holds a control character."),
        ("q=x&namespace=hgnc&namespace=hgnc", "namespace", "namespace is given once."),
        ("q=x&taxon=9606&taxon=9606", "taxon", "taxon is given once."),
        ("q=x&entity_type=gene&entity_type=gene", "entity_type", None),
        ("q=x&taxon=+9606", "taxon", None),
        ("q=x&taxon=%2B9606", "taxon", None),
        ("q=x&taxon=9_606", "taxon", None),
        ("q=x&taxon=%D9%A9606", "taxon", None),
        ("q=x&taxon=", "taxon", None),
        (f"q=x&taxon={'9' * 20}", "taxon", None),
        ("q=x&namespace=", "namespace", None),
        (f"q=x&releases=release:{'1' * 20}", "releases", None),
        ("q=x&releases=release:012", "releases", None),
        ("q=x&releases=release:-1", "releases", None),
        ("q=x&releases=RELEASE:1", "releases", None),
        ("q=x&releases=release:1%0A", "releases", None),
    ],
)
def test_a_value_error_never_reaches_the_client(
    query, parameter, detail, registry, visitor_client
):
    response = visitor_client.get(f"{RESOLVE}?{query}")
    assert response.status_code == 400
    body = response.json()
    assert [problem["parameter"] for problem in body["errors"]] == [parameter]
    if detail is not None:
        assert body["detail"] == detail


@pytest.mark.parametrize("value", (f"entity:{'1' * 20}", "entity:012", "entity:1%0A"))
def test_a_value_error_never_reaches_the_client_through_the_path(
    value, switch_on, visitor_client
):
    response = visitor_client.get(f"{BASE}entities/{value}")
    assert response.status_code == 400
    assert [problem["parameter"] for problem in response.json()["errors"]] == [
        "entity_id"
    ]


def test_a_ref_beyond_64_bits_names_nothing(switch_on, visitor_client):
    # Nineteen digits parse, and no row has that id.
    pinned = {"q": "x", "releases": f"release:{'9' * 19}"}
    body = visitor_client.get(RESOLVE, pinned).json()
    assert body["results"][0]["status"] == "unmatched"
    assert body["query"]["releases"] == [f"release:{'9' * 19}"]
    response = visitor_client.get(f"{BASE}entities/entity:{'9' * 19}")
    assert response.status_code == 404
    assert response.json() == NOT_FOUND


def test_format_is_not_an_unknown_parameter(registry, visitor_client):
    entity = EntityFactory(created_by_release=public())
    for path in (STATUS, VOCABULARIES, f"{RESOLVE}?q=x", entity_url(entity)):
        separator = "&" if "?" in path else "?"
        assert visitor_client.get(f"{path}{separator}format=json").status_code == 200
        browsable = visitor_client.get(f"{path}{separator}format=api")
        assert browsable.status_code == 200
        assert browsable["Content-Type"].startswith("text/html")


def test_an_unknown_format_is_404_and_an_unacceptable_accept_406(
    registry, settings, visitor_client
):
    # An entity the caller may see: a missing one's own 404 would hide a format
    # that is no longer refused.
    entity = EntityFactory(created_by_release=public())
    for path in (STATUS, VOCABULARIES, f"{RESOLVE}?q=x", entity_url(entity)):
        separator = "&" if "?" in path else "?"
        response = visitor_client.get(f"{path}{separator}format=xml")
        assert response.status_code == 404
        assert response["Content-Type"] == "application/json"
        assert response.json() == NOT_FOUND
        response = visitor_client.get(path, HTTP_ACCEPT="application/xml")
        assert response.status_code == 406
        assert response["Content-Type"] == "application/json"
    # status negotiates in both states; on the others the gate comes first.
    settings.TYPED_GRAPH_ENABLED = False
    assert visitor_client.get(f"{STATUS}?format=xml").status_code == 404
    assert visitor_client.get(STATUS, HTTP_ACCEPT="application/xml").status_code == 406


def test_the_error_list_is_bounded(switch_on, visitor_client):
    query = "&".join(f"name{index}=1" for index in range(40))
    body = visitor_client.get(f"{STATUS}?{query}").json()
    assert len(body["errors"]) == MAX_PROBLEMS == 20
    assert body["errors"][-1]["parameter"] == "name19"
    assert body["detail"].endswith(" and more.")
    # Exactly twenty is no more than twenty.
    query = "&".join(f"name{index}=1" for index in range(20))
    body = visitor_client.get(f"{STATUS}?{query}").json()
    assert len(body["errors"]) == 20
    assert not body["detail"].endswith("and more.")
    # The same bad piece twice is one problem, and says nothing of "more".
    params = Params(QueryDict("releases=bad&releases=bad,bad"), allowed=("releases",))
    params.releases()
    assert [problem.value for problem in params.problems] == ["bad"]
    assert params.more is False
    body = visitor_client.get(f"{RESOLVE}?q=x&releases=bad&releases=bad,bad").json()
    assert [problem["value"] for problem in body["errors"]] == ["bad"]
    assert body["detail"] == "releases: a release ref looks like release:3."


def test_a_ref_problem_reads_for_every_kind():
    # The network's seeds show these; the collector is pinned here on its own,
    # under the name that route gives it.
    many = ",".join(f"entity:{index}" for index in range(11))
    details = []
    for value in ("release:3", many, ","):
        params = Params(QueryDict(f"seeds={value}"), allowed=("seeds",))
        params.refs("seeds", "entity", 10)
        details += [problem.detail for problem in params.problems]
    assert details == [
        "seeds: an entity ref looks like entity:3.",
        "seeds names at most 10 entities.",
        "seeds names at least one entity.",
    ]


@pytest.mark.parametrize(
    "path",
    (
        STATUS,
        VOCABULARIES,
        RESOLVE,
        reachable(ENTITY),
        NETWORK,
        reachable(RELATIONSHIP),
        reachable(EVIDENCE),
    ),
)
def test_too_many_parameters_is_a_json_400(path, switch_on, visitor_client):
    # Django refuses the query string from the 1,001st field, in DRF's
    # negotiation, and would answer with its HTML 400.
    query = "&".join(f"name{index}=1" for index in range(1_001))
    response = visitor_client.get(f"{path}?{query}")
    assert response.status_code == 400
    assert response["Content-Type"] == "application/json"
    assert response.json() == {"detail": "Too many query parameters.", "errors": []}
    assert "Authorization" in response["Vary"]


def test_too_many_parameters_is_the_gates_404_while_off(
    db, settings, visitor_client, django_assert_num_queries
):
    settings.TYPED_GRAPH_ENABLED = False
    query = "&".join(f"name{index}=1" for index in range(1_001))
    with django_assert_num_queries(0):
        response = visitor_client.get(f"{VOCABULARIES}?{query}")
    assert response.status_code == 404
    assert response.json() == NOT_FOUND


# ── Responses ───────────────────────────────────────────────────────────────


def test_responses_vary_by_authorization(registry, settings, visitor_client):
    release = public()
    survivor = EntityFactory(created_by_release=release)
    merged = EntityFactory(
        status=EntityStatus.MERGED, merged_into=survivor, created_by_release=release
    )
    answered = visitor_client.get(VOCABULARIES)
    refused = visitor_client.get(f"{VOCABULARIES}?foo=1")
    moved = visitor_client.get(entity_url(merged))
    missing = visitor_client.get(f"{BASE}entities/entity:{merged.pk + 1_000}")
    settings.TYPED_GRAPH_ENABLED = False
    gated = visitor_client.get(VOCABULARIES)
    shown = visitor_client.get(STATUS)
    responses = (answered, refused, moved, missing, gated, shown)
    assert [response.status_code for response in responses] == [
        200,
        400,
        301,
        404,
        404,
        200,
    ]
    for response in responses:
        assert "Authorization" in re.split(r"\s*,\s*", response["Vary"])


@pytest.mark.parametrize("path", sorted({path for _, path, _ in ROUTES}))
def test_trailing_slash_is_404(path, switch_on, visitor_client):
    assert visitor_client.get(f"{reachable(path)}/").status_code == 404


def test_the_echo_reads_the_request_not_the_selection(
    registry, visitor_client, monkeypatch
):
    # selection() is where an instance's releases will enter, of which the
    # caller named none. The visibility walk skips `query`, the caller's own
    # words, so it would not see a ref the caller never wrote.
    shown, hidden = public(), published(ApprovalScope.INTERNAL)
    entity = EntityFactory(label="SHOWN", created_by_release=shown)

    def pins_an_instance(view):
        view.pinned = frozenset({shown.pk, hidden.pk})

    monkeypatch.setattr(TypedGraphView, "selection", pins_an_instance)
    for url in (resolve_url("SHOWN"), entity_url(entity)):
        response = visitor_client.get(url)
        assert response.status_code == 200
        body = response.json()
        assert body["query"]["releases"] is None
        assert [entry["id"] for entry in body["releases"]] == [f"release:{shown.pk}"]
        assert f"release:{hidden.pk}" not in response.content.decode()


# ── Throttles ───────────────────────────────────────────────────────────────


class BudgetedView(TypedGraphView):
    """Served by no URL: the network budget alone."""

    throttle_classes = [GraphNetworkBudgetThrottle]

    def get(self, request):
        return Response({})


class CappedView(TypedGraphView):
    """Served by no URL: a per-caller limit, then the budget."""

    throttle_classes = [GraphNetworkThrottle, GraphNetworkBudgetThrottle]

    def get(self, request):
        return Response({})


def test_each_graph_scope_has_a_rate_and_each_class_a_scope(settings):
    # A class with no rate is a 500 on first use; a rate with no class is
    # harmless, so graph_export lands before its class.
    rates = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
    assert {
        scope: rate for scope, rate in rates.items() if scope.startswith("graph_")
    } == {
        "graph_read": "300/min",
        "graph_resolve": "120/min",
        "graph_network": "60/min",
        "graph_network_site": "120/min",
        "graph_export": "20/hour",
    }
    classes = [
        member
        for _, member in inspect.getmembers(throttling, inspect.isclass)
        if member.__module__ == throttling.__name__
    ]
    assert len(classes) == 4
    for throttle in classes:
        assert throttle.scope.startswith("graph_")
        assert throttle().rate == rates[throttle.scope]
    # The network and the relationship page carry it; the per-account
    # parametrization below proves it counts accounts.
    assert issubclass(GraphNetworkThrottle, EveryCallerRateThrottle)


def test_the_read_throttle_returns_429_at_its_rate(
    switch_on, visitor_client, monkeypatch
):
    monkeypatch.setattr(GraphReadThrottle, "THROTTLE_RATES", {"graph_read": "3/min"})
    responses = [visitor_client.get(VOCABULARIES) for _ in range(4)]
    assert [response.status_code for response in responses] == [200, 200, 200, 429]
    assert int(responses[-1]["Retry-After"]) > 0
    assert responses[-1].json()["detail"].startswith("Request was throttled.")


def test_the_entity_page_shares_the_read_scope(registry, visitor_client, monkeypatch):
    monkeypatch.setattr(GraphReadThrottle, "THROTTLE_RATES", {"graph_read": "2/min"})
    page = entity_url(EntityFactory(created_by_release=public()))
    codes = [visitor_client.get(url).status_code for url in (VOCABULARIES, page, page)]
    assert codes == [200, 200, 429]


def test_resolve_has_its_own_scope(switch_on, visitor_client, monkeypatch):
    monkeypatch.setattr(
        GraphResolveThrottle, "THROTTLE_RATES", {"graph_resolve": "3/min"}
    )
    query = {"q": "x"}
    codes = [visitor_client.get(RESOLVE, query).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    assert visitor_client.get(VOCABULARIES).status_code == 200
    assert len(history("graph_resolve")) == 3
    assert len(history("graph_read")) == 1
    # The request its scope refused spent no global allowance.
    assert len(history("anon")) == 4
    assert len(history("user")) == 4


@pytest.mark.parametrize(
    "path, throttle, scope, first",
    [
        (VOCABULARIES, GraphReadThrottle, "graph_read", 200),
        (f"{RESOLVE}?q=x", GraphResolveThrottle, "graph_resolve", 200),
        (f"{NETWORK}?seeds=entity:1", GraphNetworkThrottle, "graph_network", 200),
        (reachable(RELATIONSHIP), GraphNetworkThrottle, "graph_network", 404),
        (reachable(EVIDENCE), GraphReadThrottle, "graph_read", 404),
    ],
)
def test_a_logged_in_caller_is_counted_per_account(
    path, throttle, scope, first, switch_on, user_auth_client, regular_user, monkeypatch
):
    # AnonRateThrottle keys no authenticated request, so a scope built on it
    # would stop applying at login, and anyone can register. `first` is what
    # the route answers with no data: a throttle counts in initial(), before
    # a handler's 404.
    monkeypatch.setattr(throttle, "THROTTLE_RATES", {scope: "1/min"})
    codes = [user_auth_client.get(path).status_code for _ in range(2)]
    assert codes == [first, 429]
    assert len(cache.get(f"throttle_{scope}_user-{regular_user.pk}", [])) == 1
    assert history(scope) == []


def test_a_denied_request_spends_no_global_allowance(
    switch_on, visitor_client, monkeypatch
):
    monkeypatch.setattr(GraphReadThrottle, "THROTTLE_RATES", {"graph_read": "3/min"})
    codes = [visitor_client.get(VOCABULARIES).status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]
    assert len(history("graph_read")) == 3
    assert len(history("anon")) == 3
    assert len(history("user")) == 3


def test_the_network_budget_is_shared_by_every_caller(
    switch_on, staff_user, monkeypatch
):
    monkeypatch.setattr(
        GraphNetworkBudgetThrottle, "THROTTLE_RATES", {"graph_network_site": "2/min"}
    )
    factory = APIRequestFactory()
    staff = factory.get("/", REMOTE_ADDR="192.0.2.3")
    force_authenticate(staff, user=staff_user)
    requests = [
        factory.get("/", REMOTE_ADDR="192.0.2.1"),
        factory.get("/", REMOTE_ADDR="192.0.2.2"),
        staff,
    ]
    view = BudgetedView.as_view()
    responses = [view(request) for request in requests]
    assert [response.status_code for response in responses] == [200, 200, 429]
    assert (
        responses[-1]
        .data["detail"]
        .startswith("The portal's network budget is spent for the moment.")
    )
    assert int(responses[-1]["Retry-After"]) > 0


def test_a_caller_over_their_own_limit_spends_no_budget(switch_on, monkeypatch):
    monkeypatch.setattr(
        GraphNetworkThrottle, "THROTTLE_RATES", {"graph_network": "1/min"}
    )
    monkeypatch.setattr(
        GraphNetworkBudgetThrottle, "THROTTLE_RATES", {"graph_network_site": "3/min"}
    )
    factory = APIRequestFactory()
    view = CappedView.as_view()
    first = [
        view(factory.get("/", REMOTE_ADDR="192.0.2.1")).status_code for _ in range(5)
    ]
    assert first == [200, 429, 429, 429, 429]
    assert view(factory.get("/", REMOTE_ADDR="192.0.2.2")).status_code == 200


def test_status_is_outside_the_read_and_anon_scopes(
    switch_on, visitor_client, monkeypatch
):
    assert StatusView.throttle_classes == [UserRateThrottle]
    monkeypatch.setattr(GraphReadThrottle, "THROTTLE_RATES", {"graph_read": "1/min"})
    monkeypatch.setattr(AnonRateThrottle, "THROTTLE_RATES", {"anon": "1/min"})
    codes = [visitor_client.get(STATUS).status_code for _ in range(3)]
    assert codes == [200, 200, 200]
    assert history("graph_read") == []
    assert history("anon") == []
    assert len(history("user")) == 3


def test_the_switch_off_404_is_not_counted(db, settings, visitor_client, monkeypatch):
    monkeypatch.setattr(GraphReadThrottle, "THROTTLE_RATES", {"graph_read": "1/min"})
    settings.TYPED_GRAPH_ENABLED = False
    codes = [visitor_client.get(VOCABULARIES).status_code for _ in range(2)]
    assert codes == [404, 404]
    assert history("graph_read") == []
    assert history("anon") == []
    assert history("user") == []
    settings.TYPED_GRAPH_ENABLED = True
    assert visitor_client.get(VOCABULARIES).status_code == 200

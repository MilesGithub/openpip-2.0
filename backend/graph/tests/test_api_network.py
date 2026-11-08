"""GET /api/graph/v1/network: the edges around up to ten seeds, as the caller
may see them (architecture §4.5, §7.3).

Its parameters and every 400; the echo; the demonstration contract, row by
row, for staff; the shapes; who sees what; the throttles and the site-wide
budget; what a request costs, and the 503 of a cancelled statement or a
passed deadline. The service is tested in test_neighbourhood, and the walk of
every route over every hidden release in test_api_visibility.

Evidence is built with api_helpers.state() or fan_out, which write the
participants that make an endpoint visible.
"""

import json
import time
from urllib.parse import urlencode

import pytest
from django.core.cache import cache
from django.db import connection
from django.http import QueryDict
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

import graph.neighbourhood
from graph.api.base import RequestTimedOut, TypedGraphView
from graph.api.limits import (
    CANDIDATES_PER_SEED,
    DEFAULT_MAX_EDGES,
    MAX_EDGES,
    MAX_LAYERS,
    MAX_NODES,
    MAX_SEEDS,
)
from graph.api.params import Params
from graph.api.serializers import (
    EdgeSerializer,
    NetworkNodeSerializer,
    NetworkPageSerializer,
    NetworkQuerySerializer,
    NetworkSerializer,
    WarningSerializer,
)
from graph.api.throttling import GraphNetworkBudgetThrottle, GraphNetworkThrottle
from graph.claims import canonical_relationship, find_relationship
from graph.models import (
    ApprovalScope,
    ContextFacet,
    EvidenceAssertion,
    EvidenceBasis,
    EvidenceContext,
    EvidenceMethod,
    EvidenceScore,
    MethodRole,
    Outcome,
)
from graph.summary import EFFECT_KEYS, LEVEL_KEYS, SIGN_KEYS
from graph.tests.api_helpers import (
    NETWORK,
    STATUS,
    fan_out,
    ids,
    link,
    network_url,
    pin,
    public,
    published,
    state,
)
from graph.tests.factories import (
    EvidenceContextFactory,
    RelationshipTypeFactory,
    ScoreTypeFactory,
    TermFactory,
    entity_of,
    evidence_release,
    taxon_of,
)
from graph.tests.test_api_resolve import public_view
from graph.tests.test_provenance import PUBLISHED, advance
from graph.tests.test_summary import publish, release

TR = "transcriptional_regulation"
PI = "physical_interaction"
ENVELOPE = ["api_version", "query", "nodes", "edges", "releases", "page", "warnings"]
NOTHING_HIDDEN = {outcome: 0 for outcome in Outcome.values}
NOT_CUT = {
    "truncated": False,
    "max_edges": DEFAULT_MAX_EDGES,
    "omitted": {},
    "hidden": NOTHING_HIDDEN,
}
LAYER_NAMES = [
    "encodes",
    "isoform_of",
    "physical_interaction",
    "protein_interaction_unspecified",
    "transcriptional_regulation",
    "family:physical",
    "family:reference",
    "family:regulation",
]
TAXIDS = ["7227", "9606", "32630"]
SCORE_TYPES = ["synthetic:confidence", "synthetic:rank"]
MALFORMED_SCORE = (
    "score looks like score_type:min:max, with an empty min or max for an "
    "open bound."
)
# The route's parameters in the order they are documented, read and listed.
ALLOWED = [
    "seeds",
    "layers",
    "direction",
    "outcome",
    "evidence_basis",
    "effect",
    "method",
    "level",
    "score",
    "context.host_taxon",
    "context.tissue",
    "context.cell_type",
    "context.cell_line",
    "context.condition",
    "context.perturbation",
    "context.compartment",
    "context.assay_system",
    "context.developmental_stage",
    "context.timepoint",
    "context.genome_assembly",
    "context.other",
    "taxon",
    "include_partner_edges",
    "max_edges",
    "releases",
]
# Test-only continuous types, which a non-synthetic release may score with.
STATS_TYPES = ("stats:test_value", "stats:test_other")
# The timed block's own statements inside a test transaction: on PostgreSQL
# set_config and the savepoint pair, elsewhere none.
BLOCK = 3 if connection.vendor == "postgresql" else 0


def network(client, *seeds, **parameters) -> dict:
    """The body of a 200 for the network around these rows."""
    response = client.get(network_url(*seeds, **parameters))
    assert response.status_code == 200, response.content
    return response.json()


def refused(client, query) -> dict:
    """The body of a 400 for this query string."""
    response = client.get(f"{NETWORK}?{query}")
    assert response.status_code == 400, response.content
    return response.json()


def problem(parameter, value=None, allowed=None, limit=None) -> dict:
    return {"parameter": parameter, "value": value, "allowed": allowed, "limit": limit}


def answered(client, url) -> tuple:
    """What tells one response from another: its status, its body and the
    queries it took."""
    with CaptureQueriesContext(connection) as queries:
        response = client.get(url)
    return response.status_code, response.content, len(queries)


def counted(client, url) -> int:
    status, content, queries = answered(client, url)
    assert status == 200, content
    return queries


def ref(kind, row) -> str:
    return f"{kind}:{row.pk}"


def edge_ids(body) -> list:
    return ids(body["edges"])


def by_id(body) -> dict:
    return {edge["id"]: edge for edge in body["edges"]}


def seeds_of(body) -> list:
    return [node["id"] for node in body["nodes"] if node["is_seed"]]


def hidden(**counts) -> dict:
    return {**NOTHING_HIDDEN, **counts}


def warning(code, detail) -> dict:
    return {"code": code, "detail": detail}


def hidden_warning(outcome, count) -> dict:
    return warning(
        f"{outcome}_hidden",
        f"{count} hidden relationships have {outcome.replace('_', '-')} evidence; "
        f"outcome={outcome} or outcome=all shows them.",
    )


def history(scope, ident="127.0.0.1") -> list:
    """What this scope has counted for a caller; the budget's caller is the
    site."""
    return cache.get(f"throttle_{scope}_{ident}", [])


def stored(demo, slug, subject, object_):
    """The demonstration's relationship of this type between these
    identifiers, through graph.claims."""
    found = find_relationship(
        canonical_relationship(slug, demo.entity(subject), demo.entity(object_))
    )
    assert found is not None, (slug, subject, object_)
    return found


def bearer(client, user):
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}"
    )


def enrich(relationships, ev):
    """Every filter kind on each assertion of these relationships, written in
    a fixed number of statements while the release is a draft: a two-hybrid
    detection method, a score of each of two stats: types, and a tissue, host
    taxon, cell line and other context."""
    assertions = list(EvidenceAssertion.objects.filter(relationship__in=relationships))
    two_hybrid = TermFactory(curie="MI:0018", label="two hybrid")
    hela = TermFactory(curie="MI:9002")
    human = taxon_of(9606)
    for slug in STATS_TYPES:
        ScoreTypeFactory(slug=slug)
    EvidenceMethod.objects.bulk_create(
        [
            EvidenceMethod(
                assertion=made,
                release=ev.release,
                term=two_hybrid,
                role=MethodRole.DETECTION,
            )
            for made in assertions
        ]
    )
    EvidenceScore.objects.bulk_create(
        [
            EvidenceScore(
                assertion=made,
                release=ev.release,
                score_type_id=slug,
                raw_value="0.7",
                value=0.7,
            )
            for made in assertions
            for slug in STATS_TYPES
        ]
    )
    contexts = (
        {"facet": ContextFacet.TISSUE, "raw_value": "liver"},
        {"facet": ContextFacet.HOST_TAXON, "taxon": human, "raw_value": "9606"},
        {"facet": ContextFacet.CELL_LINE, "term": hela, "raw_value": "HeLa"},
        {"facet": ContextFacet.OTHER, "raw_value": "x"},
    )
    EvidenceContext.objects.bulk_create(
        [
            EvidenceContext(assertion=made, release=ev.release, **fields)
            for made in assertions
            for fields in contexts
        ]
    )


def every_parameter(*releases) -> dict:
    """Every parameter of the route, with values the rows of enrich() match."""
    return {
        "layers": f"{TR},family:physical",
        "direction": "out",
        "outcome": "supported,tested_absent",
        "evidence_basis": "experimental",
        "effect": "not_stated,activating",
        "method": "MI:0018",
        "level": "association,not_stated",
        "score": ["stats:test_value:0:1", "stats:test_other::"],
        "context.tissue": "liver",
        "context.host_taxon": "9606",
        "context.cell_line": "MI:9002",
        "context.other": "x",
        "taxon": "9606",
        "include_partner_edges": "true",
        "max_edges": str(MAX_EDGES),
        **pin(*releases),
    }


# ── The parameters ──────────────────────────────────────────────────────────


def test_while_off_it_is_404(db, settings, visitor_client, django_assert_num_queries):
    settings.TYPED_GRAPH_ENABLED = False
    with django_assert_num_queries(0):
        response = visitor_client.get(f"{NETWORK}?seeds=entity:1")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not found."}


@pytest.mark.parametrize("query", ["", "seeds=", "seeds=,"])
def test_seeds_is_required(query, switch_on, visitor_client):
    assert refused(visitor_client, query) == {
        "detail": "seeds names at least one entity.",
        "errors": [problem("seeds")],
    }


@pytest.mark.parametrize(
    "value,piece",
    [
        ("3", "3"),
        ("release:3", "release:3"),
        ("entity:1,,entity:2", ""),
        ("entity:03", "entity:03"),
        ("entity:1%20", "entity:1 "),
        (f"entity:{'1' * 20}", f"entity:{'1' * 20}"),
    ],
)
def test_a_malformed_seed_is_400(value, piece, switch_on, visitor_client):
    assert refused(visitor_client, f"seeds={value}") == {
        "detail": "seeds: an entity ref looks like entity:3.",
        "errors": [problem("seeds", piece)],
    }


def test_seeds_names_at_most_10_entities_however_they_are_sent(
    switch_on, visitor_client
):
    refs = [f"entity:{pk}" for pk in range(1, MAX_SEEDS + 2)]
    for query in (
        f"seeds={','.join(refs)}",
        "&".join(f"seeds={item}" for item in refs),
    ):
        assert refused(visitor_client, query) == {
            "detail": f"seeds names at most {MAX_SEEDS} entities.",
            "errors": [problem("seeds", limit=MAX_SEEDS)],
        }
    # Duplicates are no more seeds, and the echo is sorted by id.
    query = f"seeds={','.join(reversed(refs[:MAX_SEEDS]))}&seeds={refs[0]}"
    response = visitor_client.get(f"{NETWORK}?{query}")
    assert response.status_code == 200
    assert response.json()["query"]["seeds"] == refs[:MAX_SEEDS]


def test_an_unknown_layer_lists_the_allowed_ones(registry, visitor_client):
    assert refused(visitor_client, "seeds=entity:1&layers=encodes,x") == {
        "detail": f"Unknown layers 'x'. Allowed: {', '.join(LAYER_NAMES)}.",
        "errors": [problem("layers", "x", LAYER_NAMES)],
    }
    assert refused(visitor_client, "seeds=entity:1&layers=") == {
        "detail": "layers names at least one layer.",
        "errors": [problem("layers")],
    }


def test_layers_names_at_most_20_distinct_layers(registry, visitor_client):
    slugs = LAYER_NAMES[:5] + [
        RelationshipTypeFactory().slug for _ in range(MAX_LAYERS - 4)
    ]
    assert refused(visitor_client, f"seeds=entity:1&layers={','.join(slugs)}") == {
        "detail": f"layers names at most {MAX_LAYERS} layers.",
        "errors": [problem("layers", limit=MAX_LAYERS)],
    }
    # Twenty-five copies of one layer are one layer.
    copies = ",".join(["encodes"] * 25)
    body = visitor_client.get(f"{NETWORK}?seeds=entity:1&layers={copies}").json()
    assert body["query"]["layers"] == ["encodes"]


@pytest.mark.parametrize(
    "name,value,allowed",
    [
        ("direction", "up", ["out", "in", "both"]),
        ("outcome", "x", [*Outcome.values, "all"]),
        ("evidence_basis", "x", list(EvidenceBasis.values)),
        # mixed and not_applicable are summary values, never stored.
        ("effect", "mixed", list(EFFECT_KEYS)),
        ("level", "x", list(LEVEL_KEYS)),
        ("taxon", "1", TAXIDS),
        ("include_partner_edges", "yes", ["true", "false"]),
    ],
)
def test_an_unknown_value_lists_the_allowed_ones(
    name, value, allowed, registry, visitor_client
):
    assert refused(visitor_client, f"seeds=entity:1&{name}={value}") == {
        "detail": f"Unknown {name} {value!r}. Allowed: {', '.join(allowed)}.",
        "errors": [problem(name, value, allowed)],
    }


@pytest.mark.parametrize(
    "name,what",
    [
        ("outcome", "outcome"),
        ("evidence_basis", "basis"),
        ("effect", "effect"),
        ("level", "level"),
        ("method", "method"),
    ],
)
def test_a_list_names_at_least_one_value(name, what, switch_on, visitor_client):
    assert refused(visitor_client, f"seeds=entity:1&{name}=,") == {
        "detail": f"{name} names at least one {what}.",
        "errors": [problem(name)],
    }


def test_all_stands_alone(switch_on, visitor_client):
    assert refused(visitor_client, "seeds=entity:1&outcome=supported,all") == {
        "detail": "outcome: all stands alone.",
        "errors": [problem("outcome", "all")],
    }
    body = visitor_client.get(f"{NETWORK}?seeds=entity:1&outcome=all,all").json()
    assert body["query"]["outcome"] == ["all"]


@pytest.mark.parametrize("value", ["MI:18", "ECO:0000353", "two hybrid", "MI:0018,x"])
def test_a_method_is_a_psi_mi_curie(value, switch_on, visitor_client):
    piece = value.split(",")[-1]
    assert refused(visitor_client, f"seeds=entity:1&method={value}") == {
        "detail": "method: a detection method is a PSI-MI CURIE, such as MI:0018.",
        "errors": [problem("method", piece)],
    }


@pytest.mark.parametrize(
    "query,detail,errors",
    [
        ("score=x", MALFORMED_SCORE, [problem("score", "x")]),
        (
            "score=synthetic:rank:a:b",
            MALFORMED_SCORE,
            [problem("score", "synthetic:rank:a:b")],
        ),
        (
            "score=nope:1:2",
            f"Unknown score type 'nope'. Allowed: {', '.join(SCORE_TYPES)}.",
            [problem("score", "nope:1:2", SCORE_TYPES)],
        ),
        # A comma is no separator here: a value that holds one is malformed,
        # in the slug as in a bound.
        (
            "score=synthetic:rank::1,synthetic:confidence::",
            MALFORMED_SCORE,
            [problem("score", "synthetic:rank::1,synthetic:confidence::")],
        ),
        (
            "score=synthetic:rank:1,5:",
            MALFORMED_SCORE,
            [problem("score", "synthetic:rank:1,5:")],
        ),
        # One colon is no range.
        (
            "score=synthetic:rank",
            MALFORMED_SCORE,
            [problem("score", "synthetic:rank")],
        ),
        # Bounds float() would read, which the grammar refuses: without it
        # 1e5, +1, 01, .5, 1. and 1_0 would reach the filter, and nan and the
        # infinities would get the finite-number sentence instead. %2B is a
        # plus sign; a bare + is a space.
        *[
            (
                f"score=synthetic:rank:{quoted}:",
                MALFORMED_SCORE,
                [problem("score", f"synthetic:rank:{bound}:")],
            )
            for quoted, bound in (
                ("1e5", "1e5"),
                ("nan", "nan"),
                ("inf", "inf"),
                ("-inf", "-inf"),
                ("%2B1", "+1"),
                ("01", "01"),
                (".5", ".5"),
                ("1.", "1."),
                ("1_0", "1_0"),
            )
        ],
        (
            "score=synthetic:rank::0.95&score=synthetic:rank:1:",
            "score names each score type once.",
            [problem("score", "synthetic:rank:1:")],
        ),
        (
            "score=synthetic:rank:2:1",
            "score: min 2 is above max 1.",
            [problem("score", "synthetic:rank:2:1")],
        ),
        (
            f"score=synthetic:rank:1{'0' * 400}:",
            "score bounds are finite numbers.",
            [problem("score", f"synthetic:rank:1{'0' * 400}:")],
        ),
    ],
)
def test_a_bad_score_is_400(query, detail, errors, registry, visitor_client):
    assert refused(visitor_client, f"seeds=entity:1&{query}") == {
        "detail": detail,
        "errors": errors,
    }


def test_a_score_range_may_be_open_on_both_sides(registry, visitor_client):
    given = ["synthetic:rank::", "synthetic:confidence:0.5:"]
    body = network(visitor_client, entity_of(), score=given)
    assert body["query"]["score"] == given


@pytest.mark.parametrize(
    "given",
    ["synthetic:rank:-1:", "synthetic:rank:-0:0", "synthetic:rank:-0.5:0.25"],
)
def test_a_score_bound_may_be_negative_or_zero(given, registry, visitor_client):
    body = network(visitor_client, entity_of(), score=given)
    assert body["query"]["score"] == [given]


def test_a_context_value_is_checked(registry, visitor_client):
    long = "x" * 201
    for query, detail, errors in (
        (
            "context.tissue=",
            "context.tissue names a value.",
            [problem("context.tissue")],
        ),
        (
            f"context.tissue={long}",
            "context.tissue is at most 200 characters.",
            [problem("context.tissue", long, limit=200)],
        ),
        (
            "context.tissue=liv%00er",
            "context.tissue holds a control character.",
            [problem("context.tissue", "liv\x00er")],
        ),
        (
            "context.host_taxon=liver",
            f"Unknown context.host_taxon 'liver'. Allowed: {', '.join(TAXIDS)}.",
            [problem("context.host_taxon", "liver", TAXIDS)],
        ),
    ):
        assert refused(visitor_client, f"seeds=entity:1&{query}") == {
            "detail": detail,
            "errors": errors,
        }


def test_context_names_at_most_4_facets(registry, visitor_client):
    # Attributed to the fifth facet present in documentation order, which is
    # not the fifth in the query string.
    query = (
        "seeds=entity:1&context.condition=d&context.cell_line=c&context.cell_type=b"
        "&context.tissue=a&context.host_taxon=9606"
    )
    assert refused(visitor_client, query) == {
        "detail": "context names at most 4 facets.",
        "errors": [problem("context.condition", "d", limit=4)],
    }
    body = visitor_client.get(f"{NETWORK}?{query.replace('&context.condition=d', '')}")
    assert body.status_code == 200
    assert body.json()["query"]["context"] == {
        "host_taxon": "9606",
        "tissue": "a",
        "cell_type": "b",
        "cell_line": "c",
    }


def test_a_context_curie_matches_the_term_and_other_text_the_value(
    registry, visitor_client
):
    ev = release()
    seed = entity_of()
    termed, raw = link(TR, seed, entity_of()), link(TR, seed, entity_of())
    EvidenceContextFactory(
        assertion=state(ev, termed),
        term=TermFactory(curie="MI:0018", label="two hybrid"),
        raw_value="two hybrid",
    )
    EvidenceContextFactory(assertion=state(ev, raw), raw_value="liver")
    publish(ev)
    # A CURIE after stripping, in any case, matches the term.
    body = network(visitor_client, seed, **{"context.tissue": " mi:0018 "})
    assert edge_ids(body) == [ref("relationship", termed)]
    assert body["query"]["context"] == {"tissue": " mi:0018 "}
    assert edge_ids(network(visitor_client, seed, **{"context.tissue": "liver"})) == [
        ref("relationship", raw)
    ]
    # Text that is no CURIE matches the value as written, so MI:18 and
    # undecodable bytes (U+FFFD on arrival) match nothing.
    for query in ("context.tissue=MI:18", "context.tissue=%FF%FE"):
        response = visitor_client.get(f"{network_url(seed)}&{query}")
        assert response.status_code == 200, response.content
        assert response.json()["edges"] == []


@pytest.mark.parametrize(
    "sent,given",
    [
        ("0", "0"),
        (str(MAX_EDGES + 1), str(MAX_EDGES + 1)),
        ("%2B5", "+5"),
        ("+5", " 5"),
        ("1_0", "1_0"),
        ("01", "01"),
        ("-1", "-1"),
        ("abc", "abc"),
        ("9" * 5000, "9" * 5000),
    ],
)
def test_a_bad_max_edges_is_400(sent, given, switch_on, visitor_client):
    assert refused(visitor_client, f"seeds=entity:1&max_edges={sent}") == {
        "detail": f"max_edges is a whole number from 1 to {MAX_EDGES:,}.",
        "errors": [problem("max_edges", given, limit=MAX_EDGES)],
    }


@pytest.mark.parametrize("name", ["browser", "expand"])
def test_browser_and_expand_are_unknown_until_their_milestones(
    name, switch_on, visitor_client
):
    assert refused(visitor_client, f"seeds=entity:1&{name}=x") == {
        "detail": f"Unknown parameter {name!r}. Allowed: {', '.join(ALLOWED)}.",
        "errors": [problem(name, allowed=ALLOWED)],
    }


def test_every_problem_is_listed_in_the_routes_order(registry, visitor_client):
    # The query string runs backwards, so the order shown is the route's.
    query = (
        "releases=bad&max_edges=0&include_partner_edges=yes&taxon=1"
        "&context.tissue=&score=x&level=x&method=x&effect=x&evidence_basis=x"
        "&outcome=x&direction=x&layers=x&seeds=bad&foo=1"
    )
    assert [
        entry["parameter"] for entry in refused(visitor_client, query)["errors"]
    ] == [
        "foo",
        "seeds",
        "layers",
        "direction",
        "outcome",
        "evidence_basis",
        "effect",
        "method",
        "level",
        "score",
        "context.tissue",
        "taxon",
        "include_partner_edges",
        "max_edges",
        "releases",
    ]


def test_a_bad_request_reads_only_the_vocabularies_it_names(
    registry, visitor_client, django_assert_num_queries
):
    with django_assert_num_queries(0):
        refused(visitor_client, "seeds=bad&outcome=x&max_edges=0&releases=bad")
    # One each for layers, score and the taxa, which taxon and
    # context.host_taxon share.
    with django_assert_num_queries(3):
        refused(
            visitor_client,
            "seeds=bad&layers=x&score=a:b:c&taxon=5&context.host_taxon=5",
        )


# ── The echo ────────────────────────────────────────────────────────────────


def test_the_echo_is_the_request_as_read(registry, visitor_client):
    query = {
        "layers": f"{TR},family:physical,{TR}",
        "direction": "out",
        "outcome": "tested_absent,supported,tested_absent",
        "evidence_basis": "experimental",
        "effect": "activating,inhibiting",
        "method": "mi:0018,MI:0018,MI:0019",
        "level": "association",
        "score": ["synthetic:rank::0.95", "synthetic:confidence:0:"],
        "context.tissue": "liver",
        "context.host_taxon": "9606",
        "taxon": "9606",
        "include_partner_edges": "true",
        "max_edges": "5",
        "releases": "release:9,release:3",
    }
    query = urlencode({"seeds": ["entity:2,entity:1", "entity:2"], **query}, doseq=True)
    response = visitor_client.get(f"{NETWORK}?{query}")
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["query"] == {
        "seeds": ["entity:1", "entity:2"],
        "layers": [TR, "family:physical"],
        "direction": "out",
        "outcome": ["tested_absent", "supported"],
        "evidence_basis": ["experimental"],
        "effect": ["activating", "inhibiting"],
        "method": ["MI:0018", "MI:0019"],
        "level": ["association"],
        "score": ["synthetic:rank::0.95", "synthetic:confidence:0:"],
        "context": {"host_taxon": "9606", "tissue": "liver"},
        "taxon": 9606,
        "include_partner_edges": True,
        "max_edges": 5,
        "releases": ["release:3", "release:9"],
    }
    assert list(body["query"]) == list(NetworkQuerySerializer().fields)


def test_a_list_keeps_the_order_of_first_appearance():
    # A set iterates in hash order, which two-piece lists match under some
    # hash seeds; a tuple compares the order on every run. Neither order is
    # sorted, nor the order of last appearance.
    params = Params(
        QueryDict("outcome=tested_absent,supported&outcome=inconclusive,supported"),
        allowed=("outcome",),
    )
    assert params.choices("outcome", lambda: list(Outcome.values)) == (
        "tested_absent",
        "supported",
        "inconclusive",
    )
    params = Params(QueryDict("method=MI:0019,mi:0018,MI:0019"), allowed=("method",))
    assert params.methods() == ("MI:0019", "MI:0018")
    assert params.problems == []


def test_with_nothing_given_the_query_is_null(switch_on, visitor_client):
    body = visitor_client.get(f"{NETWORK}?seeds=entity:1").json()
    assert body["query"] == {
        "seeds": ["entity:1"],
        "layers": None,
        "direction": None,
        "outcome": None,
        "evidence_basis": None,
        "effect": None,
        "method": None,
        "level": None,
        "score": None,
        "context": {},
        "taxon": None,
        "include_partner_edges": None,
        "max_edges": None,
        "releases": None,
    }


def test_the_echo_reads_the_request_not_the_selection(
    registry, visitor_client, monkeypatch
):
    # selection() is where an instance's releases will enter, of which the
    # caller named none.
    shown, hidden_release = public(), published(ApprovalScope.INTERNAL)
    ev = release()
    seed = entity_of(created_by_release=shown)
    state(ev, link(TR, seed, entity_of()))
    publish(ev)

    def pins_an_instance(view):
        view.pinned = frozenset({ev.release.pk, hidden_release.pk})

    monkeypatch.setattr(TypedGraphView, "selection", pins_an_instance)
    response = visitor_client.get(network_url(seed))
    assert response.status_code == 200
    body = response.json()
    assert body["query"]["releases"] is None
    assert ids(body["releases"]) == [ref("release", ev.release)]
    assert ref("release", hidden_release) not in response.content.decode()


# ── The demonstration ───────────────────────────────────────────────────────


def test_direction_on_syn_tf1(demo):
    tf1, g1, g4 = (demo.entity(name) for name in ("SYN-TF1", "SYN-G1", "SYN-G4"))
    forward = stored(demo, TR, "SYN-TF1", "SYN-G1")
    backward = stored(demo, TR, "SYN-G1", "SYN-TF1")
    unknown = stored(demo, TR, "SYN-TF1", "SYN-G4")
    absent = stored(demo, TR, "SYN-TF1", "SYN-G2")
    encodes = stored(demo, "encodes", "SYN-TF1", "SYN-P1")
    v2 = ref("release", demo.v2)

    # out: SYN-TF1's two supported edges, one assertion each, so by ascending
    # id; not the reverse edge, not the tested-absent one, which is hidden
    # and counted, and no reference edge.
    out = network(demo.staff, tf1, direction="out")
    assert edge_ids(out) == [
        ref("relationship", edge)
        for edge in sorted((forward, unknown), key=lambda edge: edge.pk)
    ]
    edges = by_id(out)
    assert edges[ref("relationship", forward)]["effect"] == "inhibiting"
    assert edges[ref("relationship", unknown)]["effect"] == "unknown"
    assert ref("relationship", backward) not in edges
    assert ref("relationship", encodes) not in edges
    assert out["page"] == {
        "truncated": False,
        "max_edges": DEFAULT_MAX_EDGES,
        "omitted": {},
        "hidden": hidden(tested_absent=1),
    }
    assert out["warnings"] == [hidden_warning("tested_absent", 1)]
    assert ids(out["nodes"]) == [
        ref("entity", row) for row in sorted((tf1, g1, g4), key=lambda row: row.pk)
    ]
    assert seeds_of(out) == [ref("entity", tf1)]
    assert ids(out["releases"]) == [v2]

    inward = network(demo.staff, tf1, direction="in")
    assert edge_ids(inward) == [ref("relationship", backward)]
    assert inward["edges"][0]["effect"] == "inhibiting"
    assert (inward["page"]["hidden"], inward["warnings"]) == (NOTHING_HIDDEN, [])

    both = network(demo.staff, tf1)
    assert edge_ids(both) == [
        ref("relationship", edge)
        for edge in sorted((backward, forward, unknown), key=lambda edge: edge.pk)
    ]
    assert both["page"]["hidden"] == hidden(tested_absent=1)
    assert both["query"]["direction"] is None

    # Asked for, the tested-absent edge is shown with no support, and the
    # three supported edges are hidden in their turn.
    tested = network(demo.staff, tf1, outcome="tested_absent")
    [edge] = tested["edges"]
    assert edge["id"] == ref("relationship", absent)
    assert (edge["shown"], edge["assertion_count"], edge["effect"]) == (True, 0, None)
    assert edge["outcome_counts"] == {**NOTHING_HIDDEN, "tested_absent": 1}
    assert edge["releases"] == [v2]
    assert edge["tested_levels"] == {}
    assert tested["page"]["hidden"] == hidden(supported=3)
    assert tested["warnings"] == [hidden_warning("supported", 3)]

    every = network(demo.staff, tf1, outcome="all")
    assert edge_ids(every) == [
        ref("relationship", edge)
        for edge in sorted(
            (backward, forward, absent, unknown), key=lambda edge: edge.pk
        )
    ]
    assert (every["page"]["hidden"], every["warnings"]) == (NOTHING_HIDDEN, [])


def test_syn_tf2_is_mixed_and_regulates_itself(demo):
    tf2, g2 = demo.entity("SYN-TF2"), demo.entity("SYN-G2")
    mixed = stored(demo, TR, "SYN-TF2", "SYN-G2")
    loop = stored(demo, TR, "SYN-TF2", "SYN-TF2")

    body = network(demo.staff, tf2)
    # Two covering assertions rank above one.
    assert edge_ids(body) == [ref("relationship", mixed), ref("relationship", loop)]
    first, second = body["edges"]
    assert (
        first["effect"],
        first["effect_counts"],
        first["assertion_count"],
        first["evidence_count"],
        first["distinct"],
    ) == (
        "mixed",
        {"activating": 1, "inhibiting": 1, "unknown": 0, "not_stated": 0},
        2,
        2,
        {"publications": 2, "releases": 1, "datasets": 1},
    )
    assert second["source"] == second["target"] == ref("entity", tf2)
    assert second["effect"] == "activating"
    assert ids(body["nodes"]) == [
        ref("entity", row) for row in sorted((tf2, g2), key=lambda row: row.pk)
    ]

    # Under an effect filter the matching assertions are counted, the rest
    # outside; the self-loop, activating, has no row left and is not hidden.
    inhibiting = network(demo.staff, tf2, effect="inhibiting")
    [edge] = inhibiting["edges"]
    assert edge["id"] == ref("relationship", mixed)
    assert (
        edge["effect"],
        edge["assertion_count"],
        edge["distinct"]["publications"],
        edge["outside_filters"],
        edge["outside_outcome_counts"],
    ) == ("inhibiting", 1, 1, 1, {**NOTHING_HIDDEN, "supported": 1})
    assert (inhibiting["page"]["hidden"], inhibiting["warnings"]) == (
        NOTHING_HIDDEN,
        [],
    )
    # The filter ranks by the matching counts: one activating each, so the
    # lower id first.
    activating = network(demo.staff, tf2, effect="activating", max_edges="1")
    assert edge_ids(activating) == [ref("relationship", mixed)]
    assert activating["page"]["omitted"] == {"edges": 1}


def test_the_symmetric_edges_of_syn_p2(demo):
    p1, p2, p3 = (demo.entity(name) for name in ("SYN-P1", "SYN-P2", "SYN-P3"))
    pair = stored(demo, PI, "SYN-P1", "SYN-P2")
    tested = stored(demo, PI, "SYN-P3", "SYN-P2")
    # A symmetric pair is stored lower id first, and matches a seed on either
    # endpoint whatever direction says.
    for direction in ("out", "in", "both"):
        body = network(demo.staff, p2, direction=direction)
        assert edge_ids(body) == [
            ref("relationship", pair),
            ref("relationship", tested),
        ]
        for edge in body["edges"]:
            assert edge["orientation"] == "symmetric"
            assert int(edge["source"].split(":")[1]) < int(edge["target"].split(":")[1])
        assert ids(body["nodes"]) == [
            ref("entity", row) for row in sorted((p1, p2, p3), key=lambda row: row.pk)
        ]
    first, second = body["edges"]
    # The strongest supported level, stated, not assigned; two records of
    # one origin are one observation.
    assert (
        first["level"],
        first["level_counts"],
        first["level_assigned"],
        first["assertion_count"],
        first["evidence_count"],
    ) == (
        "direct_interaction",
        {
            "direct_interaction": 1,
            "physical_association": 1,
            "association": 0,
            "not_stated": 0,
        },
        False,
        2,
        1,
    )
    # Only support sets the level; the test against the edge is counted and
    # shown as tested for.
    assert (second["level"], second["tested_levels"], second["outcome_counts"]) == (
        "not_stated",
        {"tested_absent": ["direct_interaction"]},
        {**NOTHING_HIDDEN, "supported": 1, "tested_absent": 1},
    )
    assert second["level_counts"]["not_stated"] == 1
    assert (first["effect"], first["genetic_sign"]) == (
        "not_applicable",
        "not_applicable",
    )

    # A level filter applies to the physical family: the pair keeps its
    # direct-interaction record, and the tested edge, whose only row at that
    # level is the test against it, is hidden.
    direct = network(demo.staff, p2, level="direct_interaction")
    [edge] = direct["edges"]
    assert edge["id"] == ref("relationship", pair)
    assert (edge["assertion_count"], edge["level"], edge["outside_filters"]) == (
        1,
        "direct_interaction",
        1,
    )
    assert direct["page"]["hidden"] == hidden(tested_absent=1)


def test_ranking_and_filters_on_syn_tf3(demo):
    tf3 = demo.entity("SYN-TF3")
    one_label = stored(demo, TR, "SYN-TF3", "SYN-G3")
    no_effect = stored(demo, TR, "SYN-TF3", "SYN-G4")
    prediction = stored(demo, TR, "SYN-TF3", "SYN-G5")
    two_papers = stored(demo, TR, "SYN-TF3", "SYN-G6")
    singles = [
        ref("relationship", edge)
        for edge in sorted((one_label, no_effect, prediction), key=lambda edge: edge.pk)
    ]

    # Two assertions first, then the ties by ascending id.
    body = network(demo.staff, tf3)
    assert edge_ids(body) == [ref("relationship", two_papers), *singles]
    assert body["edges"][0]["distinct"]["publications"] == 2
    assert by_id(body)[ref("relationship", prediction)]["by_basis"] == {
        "computational_prediction": 1
    }

    # The prediction fails an experimental filter: absent, and not hidden,
    # since no row of it passes the other filters.
    experimental = network(demo.staff, tf3, evidence_basis="experimental")
    assert edge_ids(experimental) == [
        ref("relationship", two_papers),
        *[item for item in singles if item != ref("relationship", prediction)],
    ]
    assert experimental["page"]["hidden"] == NOTHING_HIDDEN

    # A score range applies to its own type: rank 1 is above 0.95, confidence
    # 0.8 is not, and slug:: asks for any finite score of the type. The other
    # edges carry no score, so a score filter leaves them out the same way.
    assert network(demo.staff, tf3, score="synthetic:rank::0.95")["edges"] == []
    for given in ("synthetic:confidence::0.95", "synthetic:rank::"):
        ranked = network(demo.staff, tf3, score=given)
        assert edge_ids(ranked) == [ref("relationship", prediction)]
        assert ranked["page"]["hidden"] == NOTHING_HIDDEN
    alone = network(demo.staff, tf3, score="synthetic:rank::0.95")
    assert ids(alone["nodes"]) == [ref("entity", tf3)]
    assert seeds_of(alone) == [ref("entity", tf3)]
    assert alone["page"]["hidden"] == NOTHING_HIDDEN

    # The caps: the first of the ranking is kept, the rest reported.
    cut = network(demo.staff, tf3, max_edges="1")
    assert edge_ids(cut) == [ref("relationship", two_papers)]
    assert cut["page"] == {
        "truncated": True,
        "max_edges": 1,
        "omitted": {"edges": 3},
        "hidden": NOTHING_HIDDEN,
    }
    assert cut["warnings"] == [
        warning("edges_truncated", "3 ranked edges omitted: max_edges is 1.")
    ]
    assert cut["query"]["max_edges"] == 1
    assert edge_ids(network(demo.staff, tf3, max_edges="2")) == [
        ref("relationship", two_papers),
        singles[0],
    ]


def test_layers_and_releases_on_syn_tf1(demo):
    tf1, p1 = demo.entity("SYN-TF1"), demo.entity("SYN-P1")
    forward = stored(demo, TR, "SYN-TF1", "SYN-G1")
    backward = stored(demo, TR, "SYN-G1", "SYN-TF1")
    unknown = stored(demo, TR, "SYN-TF1", "SYN-G4")
    encodes = stored(demo, "encodes", "SYN-TF1", "SYN-P1")
    v1, v2 = ref("release", demo.v1), ref("release", demo.v2)

    # A reference type is a layer when named, by its slug or its family.
    for layers in ("encodes", "family:reference"):
        body = network(demo.staff, tf1, layers=layers)
        [edge] = body["edges"]
        assert (edge["id"], edge["type"], edge["family"], edge["orientation"]) == (
            ref("relationship", encodes),
            "encodes",
            "reference",
            "role_ordered",
        )
        assert (edge["effect"], edge["by_basis"], edge["level"]) == (
            "not_applicable",
            {"unknown": 1},
            None,
        )
        assert ids(body["nodes"]) == [
            ref("entity", row) for row in sorted((tf1, p1), key=lambda row: row.pk)
        ]
        assert body["query"]["layers"] == [layers]
        # SYN-TF1's tested-absent regulation edge, hidden under the default
        # layers, is of neither layer.
        assert (body["page"]["hidden"], body["warnings"]) == (NOTHING_HIDDEN, [])
    mixed_layers = network(demo.staff, tf1, layers=f"{TR},encodes")
    assert edge_ids(mixed_layers) == [
        ref("relationship", edge)
        for edge in sorted(
            (encodes, backward, forward, unknown), key=lambda edge: edge.pk
        )
    ]

    # v1 alone: its effect, flagged superseded; both: mixed, two assertions
    # of one observation.
    only_v1 = network(demo.staff, tf1, direction="out", **pin(demo.v1))
    edge = by_id(only_v1)[ref("relationship", forward)]
    assert (edge["effect"], edge["releases"]) == ("activating", [v1])
    [entry] = only_v1["releases"]
    assert (entry["id"], entry["status"], entry["superseded"]) == (
        v1,
        "superseded",
        True,
    )
    assert only_v1["query"]["releases"] == [v1]

    both = network(demo.staff, tf1, direction="out", **pin(demo.v1, demo.v2))
    edge = by_id(both)[ref("relationship", forward)]
    assert (
        edge["effect"],
        edge["effect_counts"],
        edge["assertion_count"],
        edge["evidence_count"],
        edge["releases"],
    ) == (
        "mixed",
        {"activating": 1, "inhibiting": 1, "unknown": 0, "not_stated": 0},
        2,
        1,
        [v1, v2],
    )
    assert [(entry["id"], entry["superseded"]) for entry in both["releases"]] == [
        (v1, True),
        (v2, False),
    ]
    assert edge["is_synthetic"] is True


# ── Partner edges and the caps, on factory rows ─────────────────────────────


def test_partner_edges_are_listed_when_asked(registry, visitor_client):
    ev = release()
    seed, a, b, c, x = (entity_of() for _ in range(5))
    sa, sb, sc = link(TR, seed, a), link(TR, seed, b), link(TR, seed, c)
    ab, bc, ax = link(TR, a, b), link(TR, b, c), link(TR, a, x)
    for _ in range(2):
        state(ev, sa)
    for edge in (sb, sc, ab, bc, ax):
        state(ev, edge)
    publish(ev)
    plain = network(visitor_client, seed)
    assert edge_ids(plain) == [ref("relationship", edge) for edge in (sa, sb, sc)]
    # Among the partners, after the seed's edges; not to x, which is no node.
    asked = network(visitor_client, seed, include_partner_edges="true")
    assert edge_ids(asked) == [
        ref("relationship", edge) for edge in (sa, sb, sc, ab, bc)
    ]
    assert asked["nodes"] == plain["nodes"]
    assert asked["query"]["include_partner_edges"] is True
    assert asked["page"] == NOT_CUT
    # The edge cap applies to them too.
    cut = network(visitor_client, seed, include_partner_edges="true", max_edges="4")
    assert edge_ids(cut) == [ref("relationship", edge) for edge in (sa, sb, sc, ab)]
    assert cut["page"] == {
        "truncated": True,
        "max_edges": 4,
        "omitted": {"edges": 1},
        "hidden": NOTHING_HIDDEN,
    }
    assert cut["warnings"] == [
        warning("edges_truncated", "1 ranked edges omitted: max_edges is 4.")
    ]


def test_a_cut_partner_lookup_is_reported(registry, visitor_client):
    # 201 edges among 21 partners have covered evidence, the one with the
    # highest id twice: the partner lookup ranks it first, its pre-limit
    # drops the highest-id single, and the page says a lookup was cut.
    ev = release()
    seed = entity_of()
    spokes = fan_out(seed, 21, ev)
    partners = [spoke.object for spoke in spokes]
    pairs = [(i, j) for i in range(21) for j in range(21) if i != j]
    singles = [
        link(TR, partners[i], partners[j]) for i, j in pairs[:CANDIDATES_PER_SEED]
    ]
    i, j = pairs[CANDIDATES_PER_SEED]
    best = link(TR, partners[i], partners[j])
    for edge in singles:
        state(ev, edge)
    for _ in range(2):
        state(ev, best)
    publish(ev)
    body = network(visitor_client, seed, include_partner_edges="true")
    assert edge_ids(body)[: len(spokes)] == [ref("relationship", e) for e in spokes]
    assert edge_ids(body)[len(spokes) :] == [ref("relationship", best)] + [
        ref("relationship", edge) for edge in singles[:-1]
    ]
    assert body["page"] == {
        "truncated": True,
        "max_edges": DEFAULT_MAX_EDGES,
        "omitted": {"candidates": 1},
        "hidden": NOTHING_HIDDEN,
    }
    assert body["warnings"] == [
        warning(
            "partner_edges_truncated",
            f"More than {CANDIDATES_PER_SEED} relationships among the partners have "
            f"covered evidence; the {CANDIDATES_PER_SEED} most covered were ranked.",
        )
    ]


def test_taxon_keeps_the_partners_of_that_taxon(registry, visitor_client):
    ev = release()
    seed = entity_of(taxid=9606, created_by_release=ev.release)
    human, fly = entity_of(taxid=9606), entity_of(taxid=7227)
    to_human, to_fly = link(TR, seed, human), link(TR, seed, fly)
    loop = link(TR, seed, seed)
    for edge in (to_human, to_fly, loop):
        state(ev, edge)
    publish(ev)
    # The fly partner alone: the human seed stays a node, and its self-loop
    # is kept only under its own taxon.
    flies = network(visitor_client, seed, taxon="7227")
    assert flies["query"]["taxon"] == 7227
    assert edge_ids(flies) == [ref("relationship", to_fly)]
    assert ids(flies["nodes"]) == [ref("entity", seed), ref("entity", fly)]
    humans = network(visitor_client, seed, taxon="9606")
    assert edge_ids(humans) == [ref("relationship", edge) for edge in (to_human, loop)]
    assert ids(humans["nodes"]) == [ref("entity", seed), ref("entity", human)]


def test_the_pre_limit_and_the_node_cap_are_reported(registry, visitor_client):
    ev = release()
    first, second, third = entity_of(), entity_of(), entity_of()
    fan_out(first, CANDIDATES_PER_SEED + 1, ev)
    fan_out(second, CANDIDATES_PER_SEED, ev)
    fan_out(third, CANDIDATES_PER_SEED, ev)
    publish(ev)
    body = network(visitor_client, first, second, third, max_edges=str(MAX_EDGES))
    # Three seeds and 497 partners fill the network; the first seed's lookup
    # was cut, so 600 candidates were ranked and 103 refused for nodes.
    assert len(body["nodes"]) == MAX_NODES
    assert len(body["edges"]) == MAX_NODES - 3
    assert body["page"] == {
        "truncated": True,
        "max_edges": MAX_EDGES,
        "omitted": {"candidates": 1, "edges": 103},
        "hidden": NOTHING_HIDDEN,
    }
    assert body["warnings"] == [
        warning(
            "seed_truncated",
            f"{ref('entity', first)}: more than {CANDIDATES_PER_SEED} relationships "
            f"have covered evidence; the {CANDIDATES_PER_SEED} most covered were "
            "ranked.",
        ),
        warning(
            "nodes_truncated",
            f"103 ranked edges omitted: a network holds at most {MAX_NODES} nodes; "
            "edges between the shown nodes were kept.",
        ),
    ]
    assert seeds_of(body) == [
        ref("entity", row)
        for row in sorted((first, second, third), key=lambda row: row.pk)
    ]


# ── The shapes ──────────────────────────────────────────────────────────────


def test_the_envelope_and_its_exact_keys(registry, visitor_client):
    ev = release()
    seed, partner = entity_of(), entity_of()
    lonely = entity_of(created_by_release=public())
    edge = link(TR, seed, partner)
    state(ev, edge)
    publish(ev)
    body = network(visitor_client, lonely, seed)
    assert list(body) == ENVELOPE == list(NetworkSerializer().fields)
    assert body["api_version"] == "graph/v1"
    assert list(body["query"]) == list(NetworkQuerySerializer().fields)
    # Nodes sorted by id; a seed with no edge is a node; is_seed names the
    # seeds alone.
    assert ids(body["nodes"]) == [
        ref("entity", row)
        for row in sorted((seed, partner, lonely), key=lambda row: row.pk)
    ]
    assert {node["id"]: node["is_seed"] for node in body["nodes"]} == {
        ref("entity", seed): True,
        ref("entity", partner): False,
        ref("entity", lonely): True,
    }
    for node in body["nodes"]:
        assert list(node) == list(NetworkNodeSerializer().fields)
    assert list(node) == [
        "id",
        "type",
        "label",
        "taxon",
        "status",
        "primary_identifier",
        "is_seed",
    ]
    [listed] = body["edges"]
    fields = list(EdgeSerializer().fields)
    assert list(listed) == fields
    assert fields[:8] == [
        "id",
        "type",
        "family",
        "orientation",
        "source",
        "target",
        "releases",
        "is_synthetic",
    ]
    assert listed["id"] == ref("relationship", edge)
    assert (listed["source"], listed["target"]) == (
        ref("entity", seed),
        ref("entity", partner),
    )
    assert (listed["releases"], listed["is_synthetic"], listed["qualifiers"]) == (
        [ref("release", ev.release)],
        False,
        {},
    )
    assert list(listed["distinct"]) == ["publications", "releases", "datasets"]
    # Every count holds every key.
    for name in (
        "outcome_counts",
        "independent_outcome_counts",
        "outside_outcome_counts",
    ):
        assert list(listed[name]) == list(Outcome.values)
    assert list(listed["effect_counts"]) == list(EFFECT_KEYS)
    assert list(listed["sign_counts"]) == list(SIGN_KEYS)
    assert (
        list(body["page"])
        == list(NetworkPageSerializer().fields)
        == [
            "truncated",
            "max_edges",
            "omitted",
            "hidden",
        ]
    )
    assert list(body["page"]["hidden"]) == list(Outcome.values)
    assert body["page"] == NOT_CUT
    assert body["warnings"] == []
    # A warning's keys, on a hidden edge.
    against = network(visitor_client, seed, outcome="tested_absent")
    [warned] = against["warnings"]
    assert list(warned) == list(WarningSerializer().fields) == ["code", "detail"]
    assert warned == hidden_warning("supported", 1)
    # The key order of the page's example, at every level.
    assert json.dumps(body) == json.dumps(NetworkSerializer(instance=body).data)


# ── Who sees what ───────────────────────────────────────────────────────────


def test_a_visitor_sees_nothing_of_the_demonstration_until_public_view(
    demo, visitor_client
):
    tf1, g3, tf3 = demo.entity("SYN-TF1"), demo.entity("SYN-G3"), demo.entity("SYN-TF3")
    body = network(visitor_client, tf1, g3)
    assert (body["nodes"], body["edges"], body["releases"]) == ([], [], [])
    assert (body["page"], body["warnings"]) == (NOT_CUT, [])
    assert body["query"]["seeds"] == [
        ref("entity", row) for row in sorted((tf1, g3), key=lambda row: row.pk)
    ]

    public_view(demo.v2, demo.alice)
    shown = network(visitor_client, tf1)
    assert edge_ids(shown) == edge_ids(network(demo.staff, tf1))
    assert ids(shown["releases"]) == [ref("release", demo.v2)]
    # v1 created every entity and has no public_view, so a visitor reads no
    # label of it: the identifiers name the nodes.
    labels = {
        node["id"]: node["label"] for node in network(visitor_client, g3)["nodes"]
    }
    assert labels == {ref("entity", g3): "SYN-G3", ref("entity", tf3): "SYN-TF3"}
    assert {node["id"]: node["label"] for node in network(demo.staff, g3)["nodes"]} == {
        ref("entity", g3): "SYN-ALPHA",
        ref("entity", tf3): "SYN-TF3",
    }


def test_a_hidden_seed_reads_like_a_nonexistent_one(
    registry, visitor_client, staff_client
):
    unseen = evidence_release(records=0, approved=False)
    lonely = entity_of()
    state(unseen, link(TR, lonely, entity_of()))
    advance(unseen.release, PUBLISHED)
    nowhere = f"{NETWORK}?seeds=entity:{lonely.pk + 1_000_000}"
    for client in (visitor_client, staff_client):
        status, content, queries = answered(client, network_url(lonely))
        assert status == 200
        body = json.loads(content)
        assert (body["nodes"], body["edges"], body["warnings"]) == ([], [], [])
        same = answered(client, nowhere)
        assert same[0] == 200
        assert json.loads(same[1]) | {"query": None} == body | {"query": None}
        assert same[2] == queries
        # Pinned to its own release, the seed is still unseen.
        pinned = client.get(network_url(lonely, **pin(unseen.release))).json()
        assert (pinned["nodes"], pinned["edges"]) == ([], [])


def test_a_hidden_release_states_nothing_on_a_visible_edge(
    registry, staff_user, visitor_client
):
    concealed = evidence_release(records=0, approved=False)
    shown = release()
    seed = entity_of()
    a, b, c = (link(TR, seed, entity_of()) for _ in range(3))
    state(shown, a)
    for _ in range(2):
        state(shown, b)
    for _ in range(5):
        state(concealed, a)
    state(concealed, a, outcome=Outcome.TESTED_ABSENT)
    state(concealed, c)
    advance(concealed.release, PUBLISHED)
    publish(shown)
    body = network(visitor_client, seed)
    # b outranks a, whose hidden rows count for nothing; c is absent.
    assert edge_ids(body) == [ref("relationship", b), ref("relationship", a)]
    assert [edge["assertion_count"] for edge in body["edges"]] == [2, 1]
    assert body["edges"][1]["outcome_counts"] == {**NOTHING_HIDDEN, "supported": 1}
    assert ids(body["releases"]) == [ref("release", shown.release)]
    assert ref("entity", c.object) not in ids(body["nodes"])


# ── Throttles and the budget ────────────────────────────────────────────────


def test_a_caller_over_their_own_limit_spends_no_budget(
    switch_on, visitor_client, monkeypatch
):
    monkeypatch.setattr(
        GraphNetworkThrottle, "THROTTLE_RATES", {"graph_network": "1/min"}
    )
    monkeypatch.setattr(
        GraphNetworkBudgetThrottle, "THROTTLE_RATES", {"graph_network_site": "3/min"}
    )
    url = f"{NETWORK}?seeds=entity:1"
    codes = [visitor_client.get(url).status_code for _ in range(3)]
    assert codes == [200, 429, 429]
    assert len(history("graph_network")) == 1
    assert len(history("graph_network_site", "site")) == 1
    # The refused requests spent no global allowance either.
    assert len(history("anon")) == 1
    assert len(history("user")) == 1
    assert APIClient(REMOTE_ADDR="192.0.2.2").get(url).status_code == 200


def test_a_malformed_request_spends_no_budget(switch_on, monkeypatch):
    monkeypatch.setattr(
        GraphNetworkBudgetThrottle, "THROTTLE_RATES", {"graph_network_site": "2/min"}
    )
    for address in ("192.0.2.1", "192.0.2.2"):
        with CaptureQueriesContext(connection) as queries:
            response = APIClient(REMOTE_ADDR=address).get(
                f"{NETWORK}?seeds=bad&max_edges=0"
            )
        assert (response.status_code, len(queries)) == (400, 0)
    assert history("graph_network_site", "site") == []
    assert (
        APIClient(REMOTE_ADDR="192.0.2.3").get(f"{NETWORK}?seeds=entity:1").status_code
        == 200
    )
    assert len(history("graph_network_site", "site")) == 1


def test_a_served_request_spends_the_budget(switch_on, monkeypatch):
    monkeypatch.setattr(
        GraphNetworkBudgetThrottle, "THROTTLE_RATES", {"graph_network_site": "2/min"}
    )
    url = f"{NETWORK}?seeds=entity:1"
    codes = [
        APIClient(REMOTE_ADDR=address).get(url).status_code
        for address in ("192.0.2.1", "192.0.2.2")
    ]
    assert codes == [200, 200]
    response = APIClient(REMOTE_ADDR="192.0.2.3").get(url)
    assert response.status_code == 429
    assert response.json()["detail"].startswith(
        "The portal's network budget is spent for the moment."
    )
    assert int(response["Retry-After"]) > 0
    assert "Authorization" in response["Vary"]


def test_status_says_the_network_is_served(switch_on, settings, visitor_client):
    body = visitor_client.get(STATUS).json()
    assert body["features"]["network"] is True
    rates = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
    assert body["limits"]["throttles"] == {
        scope: rates[scope]
        for scope in (
            "graph_read",
            "graph_resolve",
            "graph_network",
            "graph_network_site",
        )
    }
    assert (body["limits"]["max_seeds"], body["limits"]["max_edges"]) == (
        MAX_SEEDS,
        MAX_EDGES,
    )


# ── What a request costs ────────────────────────────────────────────────────


def test_the_network_costs_the_same_at_degree_1_and_30(
    registry, staff_user, visitor_client
):
    ev = release()
    other = release()
    # Created by a release, as the loader creates every entity, so the nodes'
    # text is gated by a creator read; a row no release created would skip it.
    one, many = (entity_of(created_by_release=ev.release) for _ in range(2))
    enrich(fan_out(one, 1, ev) + fan_out(many, 30, ev), ev)
    publish(ev, other)
    bearer(visitor_client, staff_user)
    parameters = every_parameter(ev.release, other.release)
    body = network(visitor_client, many, **parameters)
    assert len(body["edges"]) == 30
    assert body["page"] == {
        "truncated": False,
        "max_edges": MAX_EDGES,
        "omitted": {},
        "hidden": NOTHING_HIDDEN,
    }
    assert len(network(visitor_client, one, **parameters)["edges"]) == 1
    first = counted(visitor_client, network_url(one, **parameters))
    assert first == counted(visitor_client, network_url(many, **parameters))
    # The token, the set, the labels, three validation reads, the seeds, one
    # lookup, the hidden aggregate, the partner lookup, summarize's three, the
    # entity rows, entity_nodes' two and release_entries' three.
    assert first == 20 + BLOCK


def test_ten_seeds_with_every_parameter_stay_under_the_bound(
    registry, staff_user, visitor_client
):
    ev = release()
    seeds = [entity_of(created_by_release=ev.release) for _ in range(MAX_SEEDS)]
    partners = [entity_of() for _ in range(MAX_SEEDS)]
    edges = [link(TR, seed, partner) for seed, partner in zip(seeds, partners)]
    between = link(TR, partners[0], partners[1])
    for edge in (*edges, between):
        state(ev, edge)
    enrich([*edges, between], ev)
    publish(ev)
    bearer(visitor_client, staff_user)
    url = network_url(*seeds, **every_parameter(ev.release))
    body = network(visitor_client, *seeds, **every_parameter(ev.release))
    assert edge_ids(body) == [ref("relationship", edge) for edge in (*edges, between)]
    assert len(body["nodes"]) == 2 * MAX_SEEDS
    # One lookup per seed beside the fixed statements of the one-seed case.
    assert counted(visitor_client, url) == 20 + BLOCK + MAX_SEEDS - 1 <= 32


def test_a_visitor_with_no_parameter_costs_fewer(registry, visitor_client):
    ev = release()
    seed = entity_of(created_by_release=ev.release)
    state(ev, link(TR, seed, entity_of()))
    publish(ev)
    # The set, the labels, the seeds, the lookup, the hidden aggregate,
    # summarize's three, the entity rows, entity_nodes' two and
    # release_entries' three.
    assert counted(visitor_client, network_url(seed)) == 15 + BLOCK
    # A seed that names nothing stops at the seeds: no empty step sends a
    # statement.
    assert counted(visitor_client, f"{NETWORK}?seeds=entity:{seed.pk + 1_000_000}") == (
        4 + BLOCK
    )


# ── The timeout and the deadline ────────────────────────────────────────────


def timed_out(response) -> None:
    assert response.status_code == 503, response.content
    assert response["Content-Type"] == "application/json"
    assert response.json() == {"detail": RequestTimedOut.default_detail}
    assert "Authorization" in response["Vary"]
    assert not response.has_header("Retry-After")


@pytest.mark.postgres
def test_a_cancelled_statement_is_a_503(registry, staff_client, monkeypatch):
    ev = release()
    seed = entity_of()
    edge = link(TR, seed, entity_of())
    state(ev, edge)
    publish(ev)
    rows = graph.neighbourhood._candidate_rows

    def slow(seed, **lookup):
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_sleep(0.2)")
        return rows(seed, **lookup)

    monkeypatch.setattr(graph.neighbourhood, "_candidate_rows", slow)
    monkeypatch.setattr(graph.neighbourhood, "STATEMENT_TIMEOUT_MS", 20)
    monkeypatch.setattr(
        GraphNetworkBudgetThrottle, "THROTTLE_RATES", {"graph_network_site": "1/min"}
    )
    timed_out(staff_client.get(network_url(seed)))
    # Counted by the budget, like a 200.
    assert len(history("graph_network_site", "site")) == 1
    cache.clear()
    browsable = staff_client.get(network_url(seed, format="api"))
    assert browsable.status_code == 503
    assert browsable["Content-Type"].startswith("text/html")
    # The connection serves the next request.
    monkeypatch.setattr(graph.neighbourhood, "_candidate_rows", rows)
    monkeypatch.setattr(graph.neighbourhood, "STATEMENT_TIMEOUT_MS", 5000)
    cache.clear()
    assert edge_ids(network(staff_client, seed)) == [ref("relationship", edge)]


def test_a_request_past_its_deadline_is_a_503(registry, staff_client, monkeypatch):
    ev = release()
    seed = entity_of()
    state(ev, link(TR, seed, entity_of()))
    publish(ev)
    rows = graph.neighbourhood._candidate_rows
    calls, sent = [], []

    def slow(seed, **lookup):
        calls.append(seed)
        time.sleep(0.05)
        return rows(seed, **lookup)

    def recorded(name):
        function = getattr(graph.neighbourhood, name)

        def record(*args, **kwargs):
            sent.append(name)
            return function(*args, **kwargs)

        return record

    monkeypatch.setattr(graph.neighbourhood, "_candidate_rows", slow)
    for name in ("_hidden_counts", "summarize"):
        monkeypatch.setattr(graph.neighbourhood, name, recorded(name))
    monkeypatch.setattr(graph.neighbourhood, "STATEMENT_TIMEOUT_MS", 20)
    # The one lookup runs; the check before the hidden aggregate finds the
    # deadline passed and sends nothing more.
    timed_out(staff_client.get(network_url(seed)))
    assert (calls, sent) == ([seed.pk], [])

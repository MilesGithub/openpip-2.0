"""GET /api/graph/v1/relationships/{relationship_ref} and its evidence list:
one relationship as the caller may see it, and its assertions row by row
(architecture §7.2, §7.3).

The path and the parameters; the 404 that reads like a nonexistent id's; the
page on the demonstration, for staff, as the selection and the filters
change; the evidence rows, key by key; who reads a term's label, a stated
isoform and a record's raw columns; the pages by keyset and the character
cap; what a request costs, and the 503 of a cancelled statement. The walk of
every route over every hidden release is in test_api_visibility.

Evidence is built with api_helpers.state() or fan_out, which write the
participants that make an endpoint visible.
"""

import json
import re
from types import SimpleNamespace

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework_simplejwt.tokens import RefreshToken

import graph.api.views
import graph.neighbourhood
from graph.api.base import RequestTimedOut
from graph.api.limits import (
    API_VERSION,
    EVIDENCE_MAX_PAGE_SIZE,
    EVIDENCE_PAGE_RAW_CHARS,
    EVIDENCE_PAGE_SIZE,
)
from graph.api.serializers import (
    EdgeSerializer,
    EntityNodeSerializer,
    EvidencePageSerializer,
    EvidenceQuerySerializer,
    EvidenceRowSerializer,
    EvidenceSerializer,
    RelationshipQuerySerializer,
    RelationshipSerializer,
)
from graph.api.throttling import GraphNetworkThrottle, GraphReadThrottle
from graph.claims import canonical_relationship, find_relationship
from graph.entity_visibility import label_release_set
from graph.models import (
    ApprovalScope,
    ContextFacet,
    EvidencePublicationRole,
    InteractionLevel,
    MethodRole,
    Outcome,
    ParticipantSide,
    RelationshipType,
    ResolutionBasis,
    ScoreType,
)
from graph.publications import get_or_create_publication
from graph.summary import term_label
from graph.terms import get_or_create_term
from graph.tests.api_helpers import (
    BASE,
    HIDDEN_KINDS,
    STATUS,
    VOCABULARIES,
    entity_url,
    evidence_url,
    fan_out,
    hidden_evidence,
    hide,
    ids,
    link,
    network_url,
    pin,
    relationship_url,
    state,
)
from graph.tests.factories import (
    EvidenceContextFactory,
    EvidenceMethodFactory,
    EvidencePublicationFactory,
    PublicationFactory,
    TermFactory,
    approve,
    entity_of,
    evidence_release,
    mapped_record,
    taxon_of,
)
from graph.tests.test_api_network import enrich
from graph.tests.test_api_resolve import public_view
from graph.tests.test_api_visibility import STAFF_SEE
from graph.tests.test_provenance import PUBLISHED, advance
from graph.tests.test_summary import physical_edge, publish, release, request_for

TR = "transcriptional_regulation"
PI = "physical_interaction"
NOT_FOUND = {"detail": "Not found."}
PAGE_ENVELOPE = [
    "api_version",
    "query",
    "edge",
    "definition",
    "nodes",
    "by_release",
    "releases",
]
LIST_ENVELOPE = ["api_version", "query", "evidence", "releases", "page"]
ROW_KEYS = [
    "id",
    "release",
    "record",
    "outcome",
    "evidence_basis",
    "assertion_method",
    "eco_term",
    "effect",
    "effect_as_stated",
    "genetic_sign",
    "interaction_type",
    "interaction_level",
    "relationship_as_stated",
    "methods",
    "scores",
    "contexts",
    "participants",
    "qualifiers",
    "evidence_group",
    "publications",
]
NOTHING_COUNTED = {outcome: 0 for outcome in Outcome.values}
# The filters both routes take, in the order they document, read and list
# them.
FILTERS = [
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
]
# The filters as the two routes echo them when none is given.
NO_FILTER = {
    "outcome": None,
    "evidence_basis": None,
    "effect": None,
    "method": None,
    "level": None,
    "score": None,
    "context": {},
}
# The timed block's own statements inside a test transaction: on PostgreSQL
# set_config and the savepoint pair, elsewhere none.
BLOCK = 3 if connection.vendor == "postgresql" else 0
# A record of the size the loader accepts: thirteen fields of ten thousand
# characters, as the critique's probe built them.
BIG_RAW = {f"column_{index}": "x" * 10_000 for index in range(13)}
BIG_RAW_CHARS = 13 * 10_000


def page(client, relationship, **parameters) -> dict:
    """The body of a 200 for this relationship's page."""
    response = client.get(relationship_url(relationship, **parameters))
    assert response.status_code == 200, response.content
    return response.json()


def listed(client, relationship, **parameters) -> dict:
    """The body of a 200 for this relationship's evidence."""
    response = client.get(evidence_url(relationship, **parameters))
    assert response.status_code == 200, response.content
    return response.json()


def rows_of(body) -> list:
    return [row["id"] for row in body["evidence"]]


def refused(client, url) -> dict:
    response = client.get(url)
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


def nowhere(relationship):
    """A row no relationship has."""
    return SimpleNamespace(pk=relationship.pk + 1_000_000)


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


def read_statements(queries) -> list:
    """The captured statements that read the assertions table as their rows:
    the slice and its prefetches, never the existence query, whose rows are
    relationships."""
    return [
        query["sql"]
        for query in queries.captured_queries
        if query["sql"].startswith('SELECT "graph_evidenceassertion".')
    ]


PART_TABLES = (
    "graph_assertionparticipant",
    "graph_evidencemethod",
    "graph_evidencepublication",
    "graph_evidencescore",
    "graph_evidencecontext",
)


def part_statements(queries) -> list:
    """The prefetches of a page's parts, one per part table."""
    return [
        query["sql"]
        for query in queries.captured_queries
        if query["sql"].startswith(tuple(f'SELECT "{table}".' for table in PART_TABLES))
    ]


def record_statements(queries) -> list:
    """The captured statements that read the source records as their rows:
    the raw values of a page's kept rows."""
    return [
        query["sql"]
        for query in queries.captured_queries
        if query["sql"].startswith('SELECT "graph_sourcerecord".')
    ]


def every_filter(*releases) -> dict:
    """Every filter of both routes, with values the rows of enrich() match."""
    return {
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
        **pin(*releases),
    }


def isoform_row(ev, edge, isoform):
    """A row of `ev` on this physical edge whose object is stated as
    `isoform`."""
    return state(
        ev,
        edge,
        interaction_level=InteractionLevel.NOT_STATED,
        participants=(
            (ParticipantSide.SUBJECT, edge.subject, None, ResolutionBasis.PRIMARY),
            (
                ParticipantSide.OBJECT,
                edge.object,
                isoform,
                ResolutionBasis.ISOFORM_ENTRY,
            ),
        ),
    )


# ── The path and the parameters ─────────────────────────────────────────────


def test_while_off_both_routes_are_404(
    db, settings, visitor_client, django_assert_num_queries
):
    settings.TYPED_GRAPH_ENABLED = False
    for url in (
        f"{BASE}relationships/relationship:1",
        f"{BASE}relationships/1/evidence",
    ):
        with django_assert_num_queries(0):
            response = visitor_client.get(url)
        assert response.status_code == 404
        assert response.json() == NOT_FOUND


@pytest.mark.parametrize(
    "value,given",
    [
        ("12", "12"),
        ("entity:3", "entity:3"),
        ("relationship:x", "relationship:x"),
        ("relationship:012", "relationship:012"),
        ("relationship:", "relationship:"),
        ("RELATIONSHIP:1", "RELATIONSHIP:1"),
        ("relationship:-1", "relationship:-1"),
        ("relationship:1%20", "relationship:1 "),
        ("relationship:1%0A", "relationship:1\n"),
        ("relationship:%D9%A1", "relationship:١"),
        (f"relationship:{'1' * 20}", f"relationship:{'1' * 20}"),
    ],
)
def test_a_malformed_ref_is_400_on_both_routes(value, given, switch_on, visitor_client):
    for tail in ("", "/evidence"):
        assert refused(visitor_client, f"{BASE}relationships/{value}{tail}") == {
            "detail": "relationship_ref looks like relationship:12.",
            "errors": [problem("relationship_ref", given)],
        }


def test_every_problem_is_listed_in_the_routes_order(registry, visitor_client):
    # The query string runs backwards, so the order shown is the route's: the
    # unknown name, the path, then the parameters as the route lists them.
    filters = (
        "releases=bad&context.tissue=&score=x&level=x&method=x&effect=x"
        "&evidence_basis=x&outcome=x&foo=1"
    )
    body = refused(visitor_client, f"{BASE}relationships/12?{filters}")
    assert [entry["parameter"] for entry in body["errors"]] == [
        "foo",
        "relationship_ref",
        "outcome",
        "evidence_basis",
        "effect",
        "method",
        "level",
        "score",
        "context.tissue",
        "releases",
    ]
    body = refused(
        visitor_client,
        f"{BASE}relationships/12/evidence?page_size=0&after=x&{filters}",
    )
    assert [entry["parameter"] for entry in body["errors"]] == [
        "foo",
        "relationship_ref",
        "outcome",
        "evidence_basis",
        "effect",
        "method",
        "level",
        "score",
        "context.tissue",
        "after",
        "page_size",
        "releases",
    ]
    assert body["errors"][-3:] == [
        problem("after", "x"),
        problem("page_size", "0", limit=EVIDENCE_MAX_PAGE_SIZE),
        problem("releases", "bad"),
    ]


def test_after_is_one_evidence_ref(switch_on, visitor_client):
    url = f"{BASE}relationships/relationship:1/evidence"
    for value in ("x", "3", "relationship:3", "evidence:03", "evidence:1%20"):
        assert refused(visitor_client, f"{url}?after={value}")["detail"] == (
            "after: an evidence row ref looks like evidence:3."
        )
    assert refused(visitor_client, f"{url}?after=evidence:1&after=evidence:2") == {
        "detail": "after is given once.",
        "errors": [problem("after")],
    }


@pytest.mark.parametrize(
    "sent,given",
    [
        ("0", "0"),
        (str(EVIDENCE_MAX_PAGE_SIZE + 1), str(EVIDENCE_MAX_PAGE_SIZE + 1)),
        ("%2B5", "+5"),
        ("1_0", "1_0"),
        ("01", "01"),
        ("abc", "abc"),
        ("9" * 5000, "9" * 5000),
    ],
)
def test_a_bad_page_size_is_400(sent, given, switch_on, visitor_client):
    url = f"{BASE}relationships/relationship:1/evidence?page_size={sent}"
    assert refused(visitor_client, url) == {
        "detail": f"page_size is a whole number from 1 to {EVIDENCE_MAX_PAGE_SIZE:,}.",
        "errors": [problem("page_size", given, limit=EVIDENCE_MAX_PAGE_SIZE)],
    }


def test_a_bad_request_reads_only_the_vocabularies_it_names(
    registry, visitor_client, django_assert_num_queries
):
    for tail in ("", "/evidence"):
        url = f"{BASE}relationships/relationship:1{tail}"
        with django_assert_num_queries(0):
            refused(visitor_client, f"{url}?outcome=x&releases=bad")
        # One for score and one for the taxa.
        with django_assert_num_queries(2):
            refused(visitor_client, f"{url}?score=a:b:c&context.host_taxon=5")


@pytest.mark.parametrize("name", ["browser", "expand"])
@pytest.mark.parametrize(
    "tail,allowed",
    [
        ("", [*FILTERS, "releases"]),
        ("/evidence", [*FILTERS, "after", "page_size", "releases"]),
    ],
)
def test_browser_and_expand_are_unknown_until_their_milestones(
    tail, allowed, name, switch_on, visitor_client
):
    # The allowed names in documentation order, which is the order the
    # sentence and the list give them.
    url = f"{BASE}relationships/relationship:1{tail}?{name}=x"
    assert refused(visitor_client, url) == {
        "detail": f"Unknown parameter {name!r}. Allowed: {', '.join(allowed)}.",
        "errors": [problem(name, allowed=allowed)],
    }


# ── The 404 ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", HIDDEN_KINDS)
def test_a_hidden_relationship_reads_like_a_nonexistent_one(
    kind, registry, staff_user, visitor_client, staff_client
):
    ev = hidden_evidence(kind)
    edge = link(TR, entity_of(), entity_of())
    state(ev, edge)
    hide(ev, kind, staff_user)
    for client, is_staff in ((visitor_client, False), (staff_client, True)):
        for pinned in (False, True):
            if is_staff and pinned in STAFF_SEE.get(kind, ()):
                continue
            query = pin(ev.release) if pinned else {}
            for build in (relationship_url, evidence_url):
                status, content, queries = answered(client, build(edge, **query))
                assert status == 404, (kind, pinned, build.__name__)
                assert json.loads(content) == NOT_FOUND
                assert (status, content, queries) == answered(
                    client, build(nowhere(edge), **query)
                )


def test_a_ref_beyond_64_bits_asks_for_no_row(switch_on, visitor_client):
    for tail in ("", "/evidence"):
        with CaptureQueriesContext(connection) as queries:
            response = visitor_client.get(
                f"{BASE}relationships/relationship:{'9' * 19}{tail}"
            )
        assert response.status_code == 404
        assert response.json() == NOT_FOUND
        assert [
            query["sql"] for query in queries if '"graph_relationship"' in query["sql"]
        ] == []


def test_a_404_follows_the_negotiation_while_on(switch_on, visitor_client):
    for tail in ("", "/evidence"):
        response = visitor_client.get(
            f"{BASE}relationships/relationship:1{tail}?format=api"
        )
        assert response.status_code == 404
        assert response["Content-Type"].startswith("text/html")


# ── The page, on the demonstration ──────────────────────────────────────────


def test_the_page_of_syn_tf1_to_syn_g1(demo):
    tf1, g1 = demo.entity("SYN-TF1"), demo.entity("SYN-G1")
    forward = stored(demo, TR, "SYN-TF1", "SYN-G1")
    v1, v2 = ref("release", demo.v1), ref("release", demo.v2)

    body = page(demo.staff, forward)
    assert list(body) == PAGE_ENVELOPE == list(RelationshipSerializer().fields)
    assert body["api_version"] == API_VERSION
    assert list(body["query"]) == list(RelationshipQuerySerializer().fields)
    assert body["query"] == {
        "relationship_ref": ref("relationship", forward),
        **NO_FILTER,
        "releases": None,
    }
    edge = body["edge"]
    assert list(edge) == list(EdgeSerializer().fields)
    assert (edge["id"], edge["shown"], edge["effect"], edge["releases"]) == (
        ref("relationship", forward),
        True,
        "inhibiting",
        [v2],
    )
    assert (edge["source"], edge["target"]) == (ref("entity", tf1), ref("entity", g1))
    definition = RelationshipType.objects.get(slug=TR).definition
    assert body["definition"] == definition and definition
    assert ids(body["nodes"]) == [
        ref("entity", row) for row in sorted((tf1, g1), key=lambda row: row.pk)
    ]
    for node in body["nodes"]:
        assert list(node) == list(EntityNodeSerializer().fields)
    assert body["by_release"] == [
        {"release": v2, "outcome_counts": {**NOTHING_COUNTED, "supported": 1}}
    ]
    assert ids(body["releases"]) == [v2]
    # The key order of the page's example, at every level.
    assert json.dumps(body) == json.dumps(RelationshipSerializer(instance=body).data)

    only_v1 = page(demo.staff, forward, **pin(demo.v1))
    assert (only_v1["edge"]["effect"], only_v1["edge"]["releases"]) == (
        "activating",
        [v1],
    )
    [entry] = only_v1["releases"]
    assert (entry["id"], entry["status"], entry["superseded"]) == (
        v1,
        "superseded",
        True,
    )
    assert [row["release"] for row in only_v1["by_release"]] == [v1]
    assert only_v1["query"]["releases"] == [v1]

    both = page(demo.staff, forward, **pin(demo.v1, demo.v2))
    edge = both["edge"]
    assert (edge["effect"], edge["assertion_count"], edge["evidence_count"]) == (
        "mixed",
        2,
        1,
    )
    assert edge["releases"] == [v1, v2]
    # The per-release counts, over the filtered assertions, sum to the edge's.
    assert both["by_release"] == [
        {"release": v1, "outcome_counts": {**NOTHING_COUNTED, "supported": 1}},
        {"release": v2, "outcome_counts": {**NOTHING_COUNTED, "supported": 1}},
    ]
    summed = {
        outcome: sum(row["outcome_counts"][outcome] for row in both["by_release"])
        for outcome in Outcome.values
    }
    assert summed == edge["outcome_counts"]
    assert [(entry["id"], entry["superseded"]) for entry in both["releases"]] == [
        (v1, True),
        (v2, False),
    ]


def test_a_tested_absent_only_relationship_exists(demo):
    absent = stored(demo, TR, "SYN-TF1", "SYN-G2")
    v2 = ref("release", demo.v2)
    body = page(demo.staff, absent)
    edge = body["edge"]
    assert (edge["shown"], edge["effect"], edge["assertion_count"]) == (False, None, 0)
    assert edge["outcome_counts"] == {**NOTHING_COUNTED, "tested_absent": 1}
    # No covered assertion, so no covered release; the entries list the
    # release whose evidence is counted all the same.
    assert (edge["releases"], edge["is_synthetic"]) == ([], False)
    assert body["by_release"] == [
        {"release": v2, "outcome_counts": {**NOTHING_COUNTED, "tested_absent": 1}}
    ]
    assert ids(body["releases"]) == [v2]
    asked = page(demo.staff, absent, outcome="tested_absent")
    assert (asked["edge"]["shown"], asked["edge"]["effect"]) == (True, None)
    assert asked["edge"]["releases"] == [v2]
    assert asked["query"]["outcome"] == ["tested_absent"]


def test_filters_change_the_summary_and_the_counts(demo):
    mixed = stored(demo, TR, "SYN-TF2", "SYN-G2")
    pair = stored(demo, PI, "SYN-P1", "SYN-P2")
    v2 = ref("release", demo.v2)
    body = page(demo.staff, mixed, effect="inhibiting")
    edge = body["edge"]
    assert (
        edge["effect"],
        edge["assertion_count"],
        edge["outside_filters"],
        edge["outside_outcome_counts"],
    ) == ("inhibiting", 1, 1, {**NOTHING_COUNTED, "supported": 1})
    # by_release counts what passes the filters, as the edge does.
    assert body["by_release"] == [
        {"release": v2, "outcome_counts": {**NOTHING_COUNTED, "supported": 1}}
    ]
    assert body["query"]["effect"] == ["inhibiting"]
    direct = page(demo.staff, pair, level="direct_interaction")["edge"]
    assert (direct["assertion_count"], direct["level"], direct["outside_filters"]) == (
        1,
        "direct_interaction",
        1,
    )
    none = page(demo.staff, pair, method="MI:0018")["edge"]
    assert (none["shown"], none["outside_filters"]) == (False, 2)


# ── The evidence, on the demonstration ──────────────────────────────────────


def test_the_evidence_of_syn_tf1_to_syn_g1(demo):
    forward = stored(demo, TR, "SYN-TF1", "SYN-G1")
    v1, v2 = ref("release", demo.v1), ref("release", demo.v2)

    body = listed(demo.staff, forward)
    assert list(body) == LIST_ENVELOPE == list(EvidenceSerializer().fields)
    assert list(body["query"]) == list(EvidenceQuerySerializer().fields)
    assert body["query"] == {
        "relationship_ref": ref("relationship", forward),
        **NO_FILTER,
        "after": None,
        "page_size": None,
        "releases": None,
    }
    # By default the list shows v2's corrected row alone.
    [row] = body["evidence"]
    assert (
        row["release"],
        row["record"]["key"],
        row["record"]["line_number"],
        row["outcome"],
        row["effect"],
    ) == (v2, "syn-01", 2, "supported", "inhibiting")
    assert row["record"]["raw"]["record_id"] == "syn-01"
    assert row["record"]["raw"]["effect"] == "inhibiting"
    assert body["page"] == {"page_size": EVIDENCE_PAGE_SIZE, "next": None}
    assert list(body["page"]) == list(EvidencePageSerializer().fields)
    assert ids(body["releases"]) == [v2]
    assert json.dumps(body) == json.dumps(EvidenceSerializer(instance=body).data)

    only_v1 = listed(demo.staff, forward, **pin(demo.v1))
    [row] = only_v1["evidence"]
    assert (row["release"], row["record"]["key"], row["effect"]) == (
        v1,
        "syn-01",
        "activating",
    )
    [entry] = only_v1["releases"]
    assert (entry["id"], entry["superseded"]) == (v1, True)

    both = listed(demo.staff, forward, **pin(demo.v1, demo.v2))
    assert [(row["release"], row["effect"]) for row in both["evidence"]] == [
        (v1, "activating"),
        (v2, "inhibiting"),
    ]
    first, second = both["evidence"]
    assert int(first["id"].split(":")[1]) < int(second["id"].split(":")[1])
    assert ids(both["releases"]) == [v1, v2]
    edge = page(demo.staff, forward, **pin(demo.v1, demo.v2))["edge"]
    assert (edge["effect"], edge["assertion_count"], edge["evidence_count"]) == (
        "mixed",
        2,
        1,
    )


def test_every_outcome_is_listed_unless_narrowed(demo):
    tested = stored(demo, PI, "SYN-P3", "SYN-P2")
    body = listed(demo.staff, tested)
    assert [row["outcome"] for row in body["evidence"]] == [
        "supported",
        "tested_absent",
    ]
    assert rows_of(body) == sorted(rows_of(body), key=lambda item: int(item[9:]))
    supported = listed(demo.staff, tested, outcome="supported")
    assert [row["outcome"] for row in supported["evidence"]] == ["supported"]
    against = listed(demo.staff, tested, outcome="tested_absent")
    [row] = against["evidence"]
    assert (row["outcome"], row["interaction_level"]) == (
        "tested_absent",
        "direct_interaction",
    )
    assert against["query"]["outcome"] == ["tested_absent"]


def test_the_row_shape_on_syn_tf2_to_syn_g2(demo):
    tf2, g2 = demo.entity("SYN-TF2"), demo.entity("SYN-G2")
    mixed = stored(demo, TR, "SYN-TF2", "SYN-G2")
    v2 = ref("release", demo.v2)
    body = listed(demo.staff, mixed)
    first, second = body["evidence"]
    assert list(first) == ROW_KEYS == list(EvidenceRowSerializer().fields)
    assert first["record"]["raw"]["record_id"] == "syn-03"
    assert first == {
        "id": first["id"],
        "release": v2,
        "record": {"key": "syn-03", "line_number": 4, "raw": first["record"]["raw"]},
        "outcome": "supported",
        "evidence_basis": "experimental",
        "assertion_method": "manual_assertion",
        "eco_term": None,
        "effect": "activating",
        "effect_as_stated": "",
        "genetic_sign": None,
        "interaction_type": None,
        "interaction_level": None,
        "relationship_as_stated": "",
        "methods": [],
        "scores": [],
        "contexts": [],
        "participants": [
            {
                "side": "subject",
                "entity": ref("entity", tf2),
                "stated_entity": None,
                "identifier_as_stated": "SYN-TF2",
                "resolution_basis": "primary",
            },
            {
                "side": "object",
                "entity": ref("entity", g2),
                "stated_entity": None,
                "identifier_as_stated": "SYN-G2",
                "resolution_basis": "primary",
            },
        ],
        "qualifiers": {},
        "evidence_group": "",
        "publications": [
            {
                "role": "reported_in",
                "publication": {
                    "pmid": None,
                    "doi": None,
                    "pmcid": None,
                    "imex_id": None,
                    "local_key": "syn-paper-1",
                    "title": (
                        "SYN demonstration paper 1 (synthetic: not a real publication)"
                    ),
                    "authors": "",
                    "journal": "",
                    "year": 2026,
                    "url": None,
                    "is_synthetic": True,
                },
            }
        ],
    }
    assert first["id"].startswith("evidence:")
    assert (second["record"]["key"], second["effect"]) == ("syn-04", "inhibiting")
    assert second["publications"][0]["publication"]["local_key"] == "syn-paper-2"
    assert json.dumps(body) == json.dumps(EvidenceSerializer(instance=body).data)


def test_the_predictions_scores_carry_their_types(demo):
    prediction = stored(demo, TR, "SYN-TF3", "SYN-G5")
    [row] = listed(demo.staff, prediction)["evidence"]
    assert (row["evidence_basis"], row["assertion_method"]) == (
        "computational_prediction",
        "automatic_assertion",
    )
    confidence = ScoreType.objects.get(slug="synthetic:confidence")
    rank = ScoreType.objects.get(slug="synthetic:rank")
    assert row["scores"] == [
        {
            "type": "synthetic:confidence",
            "label": confidence.label,
            "value": 0.8,
            "raw_value": "0.8",
            "value_kind": confidence.value_kind,
            "direction": confidence.direction,
            "unit": None,
        },
        {
            "type": "synthetic:rank",
            "label": rank.label,
            "value": 1.0,
            "raw_value": "1",
            "value_kind": rank.value_kind,
            "direction": rank.direction,
            "unit": None,
        },
    ]
    assert (confidence.label, rank.label) == ("Synthetic confidence", "Synthetic rank")


def test_the_participants_of_a_symmetric_pair_keep_their_sides(demo):
    p1, p2 = demo.entity("SYN-P1"), demo.entity("SYN-P2")
    pair = stored(demo, PI, "SYN-P1", "SYN-P2")
    first, second = listed(demo.staff, pair)["evidence"]
    # The crosswalk labels its own terms, whatever release stated them.
    assert (
        first["record"]["key"],
        first["interaction_type"],
        first["interaction_level"],
    ) == (
        "syn-10",
        {"curie": "MI:0915", "label": "physical association"},
        "physical_association",
    )
    assert (
        second["record"]["key"],
        second["interaction_type"],
        second["interaction_level"],
    ) == (
        "syn-11",
        {"curie": "MI:0407", "label": "direct interaction"},
        "direct_interaction",
    )
    assert [(part["side"], part["entity"]) for part in first["participants"]] == [
        ("subject", ref("entity", p1)),
        ("object", ref("entity", p2)),
    ]
    assert [(part["side"], part["entity"]) for part in second["participants"]] == [
        ("subject", ref("entity", p2)),
        ("object", ref("entity", p1)),
    ]


# ── The evidence row, on factory rows ───────────────────────────────────────


def test_a_row_with_every_part(registry, staff_client, visitor_client):
    ev = release()
    edge = physical_edge()
    isoform = entity_of("protein_isoform", created_by_release=ev.release)
    TermFactory(curie="MI:0407", label="direct interaction")
    eco = TermFactory(
        curie="ECO:0000353",
        label="physical interaction evidence",
        label_release=ev.release,
    )
    detection = TermFactory(
        curie="MI:9001", label="test detection", label_release=ev.release
    )
    code = TermFactory(
        curie="ECO:0000006", label="experimental evidence", label_release=ev.release
    )
    cell = TermFactory(curie="MI:9002", label="a cell line", label_release=ev.release)
    paper = PublicationFactory(
        doi="10.1000/x",
        title="A title",
        authors="A, B",
        journal="J",
        year=2020,
        url="https://example.org/paper",
    )
    other = PublicationFactory()
    # The participants are written object first, and listed subject first.
    made = state(
        ev,
        edge,
        outcome=Outcome.NOT_SIGNIFICANT,
        eco_term=eco,
        interaction_type_id="MI:0407",
        interaction_level=InteractionLevel.DIRECT_INTERACTION,
        evidence_group="grp-1",
        relationship_as_stated="binds",
        effect_as_stated="",
        participants=(
            (ParticipantSide.OBJECT, edge.object, None, ResolutionBasis.PRIMARY),
            (
                ParticipantSide.SUBJECT,
                edge.subject,
                isoform,
                ResolutionBasis.ISOFORM_ENTRY,
            ),
        ),
    )
    EvidenceMethodFactory(assertion=made, term=detection)
    EvidenceMethodFactory(assertion=made, term=code, role=MethodRole.EVIDENCE_CODE)
    EvidenceContextFactory(
        assertion=made,
        facet=ContextFacet.CELL_LINE,
        term=cell,
        raw_value="HeLa",
        label="HeLa cell",
    )
    EvidenceContextFactory(
        assertion=made,
        facet=ContextFacet.HOST_TAXON,
        taxon=taxon_of(9606),
        raw_value="9606",
        label="Homo sapiens",
    )
    EvidenceContextFactory(assertion=made, facet=ContextFacet.TISSUE, raw_value="liver")
    EvidencePublicationFactory(assertion=made, publication=paper)
    EvidencePublicationFactory(
        assertion=made, publication=other, role=EvidencePublicationRole.CITED
    )
    publish(ev)
    for client in (visitor_client, staff_client):
        body = listed(client, edge)
        [row] = body["evidence"]
        assert list(row) == ROW_KEYS
        assert (row["id"], row["release"]) == (
            ref("evidence", made),
            ref("release", ev.release),
        )
        assert (row["outcome"], row["evidence_basis"], row["assertion_method"]) == (
            "not_significant",
            "experimental",
            "manual_assertion",
        )
        assert row["eco_term"] == {
            "curie": "ECO:0000353",
            "label": "physical interaction evidence",
        }
        assert (row["effect"], row["effect_as_stated"], row["genetic_sign"]) == (
            None,
            "",
            None,
        )
        assert row["interaction_type"] == {
            "curie": "MI:0407",
            "label": "direct interaction",
        }
        assert (row["interaction_level"], row["relationship_as_stated"]) == (
            "direct_interaction",
            "binds",
        )
        assert row["methods"] == [
            {"role": "detection", "curie": "MI:9001", "label": "test detection"},
            {
                "role": "evidence_code",
                "curie": "ECO:0000006",
                "label": "experimental evidence",
            },
        ]
        assert row["scores"] == []
        assert row["contexts"] == [
            {
                "facet": "cell_line",
                "term": {"curie": "MI:9002", "label": "a cell line"},
                "taxon": None,
                "label": "HeLa cell",
                "raw_value": "HeLa",
            },
            {
                "facet": "host_taxon",
                "term": None,
                "taxon": 9606,
                "label": "Homo sapiens",
                "raw_value": "9606",
            },
            {
                "facet": "tissue",
                "term": None,
                "taxon": None,
                "label": "",
                "raw_value": "liver",
            },
        ]
        assert [
            (
                part["side"],
                part["entity"],
                part["stated_entity"],
                part["resolution_basis"],
            )
            for part in row["participants"]
        ] == [
            (
                "subject",
                ref("entity", edge.subject),
                ref("entity", isoform),
                "isoform_entry",
            ),
            ("object", ref("entity", edge.object), None, "primary"),
        ]
        assert row["qualifiers"] == {
            "threshold": [
                {"score_type": "stats:test_p", "operator": "<", "value": "0.05"}
            ]
        }
        assert row["evidence_group"] == "grp-1"
        assert row["publications"] == [
            {
                "role": "reported_in",
                "publication": {
                    "pmid": paper.pmid,
                    "doi": "10.1000/x",
                    "pmcid": None,
                    "imex_id": None,
                    "local_key": None,
                    "title": "A title",
                    "authors": "A, B",
                    "journal": "J",
                    "year": 2020,
                    "url": "https://example.org/paper",
                    "is_synthetic": False,
                },
            },
            {
                "role": "cited",
                "publication": {
                    "pmid": other.pmid,
                    "doi": None,
                    "pmcid": None,
                    "imex_id": None,
                    "local_key": None,
                    "title": "",
                    "authors": "",
                    "journal": "",
                    "year": None,
                    "url": None,
                    "is_synthetic": False,
                },
            },
        ]
        assert json.dumps(body) == json.dumps(EvidenceSerializer(instance=body).data)


def test_a_term_is_labelled_only_for_a_caller_who_may_read_its_release(
    registry, staff_user, visitor_client, staff_client
):
    shown = release()
    edge = link(TR, entity_of(), entity_of())
    made = state(shown, edge)
    # An embargoed release's commit may state terms; visitors never read it.
    hidden = hidden_evidence("embargoed")
    secret = get_or_create_term("MI:9123", "HIDDEN-LABEL", import_run=hidden.run).term
    EvidenceMethodFactory(assertion=made, term=secret)
    publish(shown)
    hide(hidden, "embargoed", staff_user)
    assert listed(visitor_client, edge)["evidence"][0]["methods"] == [
        {"role": "detection", "curie": "MI:9123", "label": None}
    ]
    assert listed(staff_client, edge)["evidence"][0]["methods"][0]["label"] == (
        "HIDDEN-LABEL"
    )


def test_a_label_a_visible_release_stated_shows_whatever_is_selected(
    registry, visitor_client
):
    # The label goes through the may-read set, so a view pinned to the row's
    # own release keeps it, and term_labels agrees with term_label term by term.
    labeller, stater = release(), release()
    edge = link(TR, entity_of(), entity_of())
    term = get_or_create_term("MI:9124", "VISIBLE-LABEL", import_run=labeller.run).term
    TermFactory(curie="MI:0407", label="direct interaction")
    made = state(stater, edge)
    EvidenceMethodFactory(assertion=made, term=term)
    EvidenceContextFactory(
        assertion=made, facet=ContextFacet.ASSAY_SYSTEM, term_id="MI:0407"
    )
    publish(labeller, stater)
    for query in ({}, pin(stater.release)):
        [row] = listed(visitor_client, edge, **query)["evidence"]
        assert row["methods"][0]["label"] == "VISIBLE-LABEL"
        assert row["contexts"][0]["term"] == {
            "curie": "MI:0407",
            "label": "direct interaction",
        }
    labels = label_release_set(request_for(None))
    for shown in (row["methods"][0], row["contexts"][0]["term"]):
        assert shown["label"] == term_label(shown["curie"], releases=labels)


def test_a_stated_isoform_is_named_only_when_the_caller_may_see_it(
    registry, staff_user, visitor_client, staff_client
):
    shown = release()
    edge = physical_edge()
    hidden = hidden_evidence("embargoed")
    isoform = entity_of("protein_isoform", created_by_release=hidden.release)
    state(
        shown,
        edge,
        interaction_level=InteractionLevel.NOT_STATED,
        participants=(
            (ParticipantSide.SUBJECT, edge.subject, None, ResolutionBasis.PRIMARY),
            (
                ParticipantSide.OBJECT,
                edge.object,
                isoform,
                ResolutionBasis.ISOFORM_ENTRY,
            ),
        ),
    )
    publish(shown)
    hide(hidden, "embargoed", staff_user)
    stated = [
        part["stated_entity"]
        for part in listed(visitor_client, edge)["evidence"][0]["participants"]
    ]
    assert stated == [None, None]
    stated = [
        part["stated_entity"]
        for part in listed(staff_client, edge)["evidence"][0]["participants"]
    ]
    assert stated == [None, ref("entity", isoform)]
    assert ref("entity", isoform) not in json.dumps(listed(visitor_client, edge))


def test_a_stated_isoform_is_named_only_under_the_releases_selected(
    registry, visitor_client, staff_client
):
    # The caller may read the isoform's creator whatever is selected, but a
    # pin to the stating release alone leaves the creator out of the data
    # set, as the entity page does.
    creator, stater = release(), release()
    isoform = entity_of("protein_isoform", created_by_release=creator.release)
    edge = physical_edge()
    isoform_row(stater, edge, isoform)
    publish(creator, stater)
    for client in (visitor_client, staff_client):
        [row] = listed(client, edge)["evidence"]
        assert [part["stated_entity"] for part in row["participants"]] == [
            None,
            ref("entity", isoform),
        ]
        [row] = listed(client, edge, **pin(stater.release))["evidence"]
        assert [part["stated_entity"] for part in row["participants"]] == [None, None]
        assert client.get(entity_url(isoform, **pin(stater.release))).status_code == (
            404
        )


def test_the_stated_isoforms_of_a_page_cost_one_query(registry, visitor_client):
    ev = release()
    many, one, plain = physical_edge(), physical_edge(), physical_edge()
    for _ in range(20):
        isoform_row(
            ev, many, entity_of("protein_isoform", created_by_release=ev.release)
        )
    isoform_row(ev, one, entity_of("protein_isoform", created_by_release=ev.release))
    state(ev, plain, interaction_level=InteractionLevel.NOT_STATED)
    publish(ev)
    stated = {}
    for edge in (many, one, plain):
        body = listed(visitor_client, edge)
        stated[edge.pk] = {
            row["participants"][1]["stated_entity"] for row in body["evidence"]
        }
    assert (len(stated[many.pk]), len(stated[one.pk]), stated[plain.pk]) == (
        20,
        1,
        {None},
    )
    assert None not in stated[many.pk] | stated[one.pk]
    # Twenty distinct isoforms cost what one does: one query over them all.
    costs = [counted(visitor_client, evidence_url(edge)) for edge in (many, one, plain)]
    # The set, the labels, the existence query, the slice's six,
    # release_entries' three, the kept rows' raw records, and the stated
    # isoforms' one.
    assert costs == [15 + BLOCK, 15 + BLOCK, 14 + BLOCK]


def test_a_title_a_hidden_release_filled_shows(registry, staff_user, visitor_client):
    # Accepted: publication text is the first writer's, with no release
    # recorded behind it. A later gate changes this test, not a surprise.
    shown = release()
    edge = link(TR, entity_of(), entity_of())
    paper = PublicationFactory(title="")
    EvidencePublicationFactory(assertion=state(shown, edge), publication=paper)
    hidden = hidden_evidence("embargoed")
    get_or_create_publication(pmid=paper.pmid, title="HIDDEN-TITLE")
    publish(shown)
    hide(hidden, "embargoed", staff_user)
    [row] = listed(visitor_client, edge)["evidence"]
    assert row["publications"][0]["publication"]["title"] == "HIDDEN-TITLE"


# ── Pages ───────────────────────────────────────────────────────────────────


def test_pages_walk_by_keyset(registry, visitor_client):
    ev = release()
    edge = link(TR, entity_of(), entity_of())
    rows = [state(ev, edge) for _ in range(3)]
    publish(ev)
    refs = [ref("evidence", row) for row in rows]
    first = listed(visitor_client, edge, page_size="1")
    assert (rows_of(first), first["page"]) == (
        [refs[0]],
        {"page_size": 1, "next": refs[0]},
    )
    assert (first["query"]["after"], first["query"]["page_size"]) == (None, 1)
    second = listed(visitor_client, edge, page_size="1", after=first["page"]["next"])
    assert (rows_of(second), second["page"]["next"]) == ([refs[1]], refs[1])
    assert second["query"]["after"] == refs[0]
    # Stable: the same page however often it is asked for.
    assert listed(visitor_client, edge, page_size="1", after=refs[0]) == second
    third = listed(visitor_client, edge, page_size="1", after=second["page"]["next"])
    assert (rows_of(third), third["page"]["next"]) == ([refs[2]], None)
    # A position, never checked against what exists: from the start, and
    # from a row of another relationship.
    assert rows_of(listed(visitor_client, edge, after="evidence:0")) == refs
    assert (
        rows_of(listed(visitor_client, edge, after=f"evidence:{rows[0].pk - 1}"))
        == refs
    )
    whole = listed(visitor_client, edge, page_size=str(EVIDENCE_MAX_PAGE_SIZE))
    assert (rows_of(whole), whole["page"]) == (
        refs,
        {"page_size": EVIDENCE_MAX_PAGE_SIZE, "next": None},
    )
    assert listed(visitor_client, edge)["page"] == {
        "page_size": EVIDENCE_PAGE_SIZE,
        "next": None,
    }


def test_after_is_scoped_to_the_relationship(registry, visitor_client):
    ev = release()
    seed = entity_of()
    first, second = link(TR, seed, entity_of()), link(TR, seed, entity_of())
    rows = [state(ev, edge) for edge in (first, second, first, second)]
    publish(ev)
    body = listed(visitor_client, first, after=ref("evidence", rows[1]))
    assert rows_of(body) == [ref("evidence", rows[2])]


def test_an_empty_page_and_a_position_beyond_64_bits_cost_less(
    registry, visitor_client
):
    ev = release()
    edge = link(TR, entity_of(), entity_of())
    rows = [state(ev, edge) for _ in range(2)]
    publish(ev)
    with CaptureQueriesContext(connection) as full:
        body = listed(visitor_client, edge, after=ref("evidence", rows[0]))
    assert (rows_of(body), body["page"]["next"]) == ([ref("evidence", rows[1])], None)
    # The slice is six statements when it has rows: its own and the five
    # prefetches, one per part table; one, its own, when it has none, and
    # then no release entry and no raw record is read either.
    assert len(read_statements(full)) == 1
    assert len(part_statements(full)) == 5
    assert len(record_statements(full)) == 1
    with CaptureQueriesContext(connection) as empty:
        body = listed(visitor_client, edge, after=ref("evidence", rows[1]))
    assert (body["evidence"], body["page"]["next"], ids(body["releases"])) == (
        [],
        None,
        [],
    )
    assert len(read_statements(empty)) == 1
    assert part_statements(empty) == record_statements(empty) == []
    assert len(empty) == len(full) - 5 - 3 - 1
    with CaptureQueriesContext(connection) as beyond:
        far = listed(visitor_client, edge, after=f"evidence:{'9' * 19}")
    assert far | {"query": None} == body | {"query": None}
    assert far["query"]["after"] == f"evidence:{'9' * 19}"
    assert read_statements(beyond) == []
    assert len(beyond) == len(empty) - 1


def test_a_page_costs_the_same_at_size_1_and_200(registry, staff_user, visitor_client):
    ev = release()
    other = release()
    seed = entity_of(created_by_release=ev.release)
    [edge] = fan_out(seed, 1, ev, assertions_per_edge=EVIDENCE_MAX_PAGE_SIZE + 1)
    enrich([edge], ev)
    publish(ev, other)
    bearer(visitor_client, staff_user)
    parameters = {**every_filter(ev.release, other.release), "after": "evidence:0"}
    small = listed(visitor_client, edge, page_size="1", **parameters)
    large = listed(
        visitor_client, edge, page_size=str(EVIDENCE_MAX_PAGE_SIZE), **parameters
    )
    assert (len(small["evidence"]), len(large["evidence"])) == (
        1,
        EVIDENCE_MAX_PAGE_SIZE,
    )
    assert small["page"]["next"] == small["evidence"][0]["id"]
    assert large["page"]["next"] == large["evidence"][-1]["id"]
    # The method's term has no labelling release, so its label is null.
    assert large["evidence"][0]["methods"] == [
        {"role": "detection", "curie": "MI:0018", "label": None}
    ]
    first = counted(visitor_client, evidence_url(edge, page_size="1", **parameters))
    assert first == counted(
        visitor_client,
        evidence_url(edge, page_size=str(EVIDENCE_MAX_PAGE_SIZE), **parameters),
    )
    # The token, the set, the labels, two validation reads, the existence
    # query, the slice's six, release_entries' three, the kept rows' raw
    # records and term_labels' one.
    assert first == 18 + BLOCK


def test_the_page_costs_the_same_at_one_and_a_thousand_assertions(
    registry, staff_user, visitor_client
):
    ev = release()
    other = release()
    one, many = (entity_of(created_by_release=ev.release) for _ in range(2))
    [single] = fan_out(one, 1, ev)
    [hub] = fan_out(many, 1, ev, assertions_per_edge=1000)
    enrich([single, hub], ev)
    publish(ev, other)
    bearer(visitor_client, staff_user)
    body = page(visitor_client, hub)
    assert (body["edge"]["assertion_count"], body["edge"]["evidence_count"]) == (
        1000,
        1,
    )
    assert body["by_release"][0]["outcome_counts"]["supported"] == 1000
    first = counted(visitor_client, relationship_url(single))
    assert first == counted(visitor_client, relationship_url(hub))
    # The token, the set, the labels, the existence query, summarize's three,
    # the aggregate, the endpoint rows, entity_nodes' two and
    # release_entries' three.
    assert first == 15 + BLOCK
    # Every filter: the same rows pass, read by the same statements, and
    # score and context.host_taxon add a validation read each, the score
    # types and the taxa.
    filters = every_filter(ev.release, other.release)
    filtered = page(visitor_client, hub, **filters)["edge"]
    assert (filtered["assertion_count"], filtered["shown"]) == (1000, True)
    every = counted(visitor_client, relationship_url(single, **filters))
    assert every == counted(visitor_client, relationship_url(hub, **filters))
    assert every == 17 + BLOCK


def test_a_page_ends_early_when_its_raw_records_pass_the_character_cap(
    registry, staff_client
):
    ev = release()
    edge = link(TR, entity_of(), entity_of())
    rows = [
        state(ev, edge, record=mapped_record(ev.release, ev.run, raw=BIG_RAW))
        for _ in range(20)
    ]
    publish(ev)
    refs = [ref("evidence", row) for row in rows]
    # The sixteenth row brings the sum past the cap, and rows follow it.
    cut = EVIDENCE_PAGE_RAW_CHARS // BIG_RAW_CHARS + 1
    assert cut == 16
    body = listed(staff_client, edge)
    assert rows_of(body) == refs[:cut]
    assert body["page"] == {"page_size": EVIDENCE_PAGE_SIZE, "next": refs[cut - 1]}
    assert body["evidence"][0]["record"]["raw"] == BIG_RAW
    rest = listed(staff_client, edge, after=body["page"]["next"])
    assert (rows_of(rest), rest["page"]["next"]) == (refs[cut:], None)
    # A page whose last fetched row is the one past the cap has nothing left.
    exact = listed(staff_client, edge, page_size=str(cut))
    assert (rows_of(exact), exact["page"]["next"]) == (refs[:cut], refs[cut - 1])
    last = listed(staff_client, edge, after=refs[3], page_size=str(cut))
    assert (rows_of(last), last["page"]["next"]) == (refs[4 : 4 + cut], None)


def test_a_page_reads_the_raw_records_it_delivers_alone(registry, staff_client):
    # The slice counts each record's characters without reading its raw
    # value; the cap cuts on those counts, and one statement then reads the
    # kept rows' records. So a page_size of 200 reads the sixteen records a
    # page of sixteen does, not the twenty its slice holds.
    ev = release()
    edge = link(TR, entity_of(), entity_of())
    rows = [
        state(ev, edge, record=mapped_record(ev.release, ev.run, raw=BIG_RAW))
        for _ in range(20)
    ]
    publish(ev)
    cut = EVIDENCE_PAGE_RAW_CHARS // BIG_RAW_CHARS + 1
    kept = sorted(row.source_record_id for row in rows[:cut])
    bodies = []
    for page_size in (cut, EVIDENCE_MAX_PAGE_SIZE):
        with CaptureQueriesContext(connection) as queries:
            body = listed(staff_client, edge, page_size=str(page_size))
        [slice_statement] = read_statements(queries)
        # Selected as a column, raw would be followed by the next column.
        assert '"graph_sourcerecord"."raw",' not in slice_statement
        [read] = record_statements(queries)
        named = re.search(r'"graph_sourcerecord"\."id" IN \(([^)]*)\)', read)
        assert [int(pk) for pk in named.group(1).split(", ")] == kept
        bodies.append(body)
    small, large = bodies
    assert rows_of(large) == [ref("evidence", row) for row in rows[:cut]]
    assert large["page"]["next"] == ref("evidence", rows[cut - 1])
    assert (small["evidence"], small["page"]["next"]) == (
        large["evidence"],
        large["page"]["next"],
    )
    assert all(row["record"]["raw"] == BIG_RAW for row in large["evidence"])


def test_raw_a_caller_may_not_read_counts_nothing(registry, staff_client, monkeypatch):
    # Rows the caller sees whose raw columns are gated fill the page: an
    # internal-only release read by an account the gate does not count as
    # staff. The gate hides nothing from anyone who sees a row today.
    ev = release(public=False)
    edge = link(TR, entity_of(), entity_of())
    rows = [
        state(ev, edge, record=mapped_record(ev.release, ev.run, raw=BIG_RAW))
        for _ in range(20)
    ]
    publish(ev)
    monkeypatch.setattr(graph.api.views, "is_staff_caller", lambda request: False)
    body = listed(staff_client, edge)
    assert rows_of(body) == [ref("evidence", row) for row in rows]
    assert body["page"]["next"] is None
    assert {row["record"]["raw"] for row in body["evidence"]} == {None}


def test_the_character_cap_cuts_small_rows_when_lowered(
    registry, visitor_client, monkeypatch
):
    first, second = release(), release()
    edge = link(TR, entity_of(), entity_of())
    stating = (first, second, first)
    rows = [state(ev, edge) for ev in stating]
    publish(first, second)
    monkeypatch.setattr(graph.api.views, "EVIDENCE_PAGE_RAW_CHARS", 1)
    refs = [ref("evidence", row) for row in rows]
    walked, after = [], None
    for ev in stating:
        body = listed(visitor_client, edge, **({"after": after} if after else {}))
        walked += rows_of(body)
        # The entries are the delivered rows' releases, not those of the rows
        # the cap left to the next page.
        assert ids(body["releases"]) == [ref("release", ev.release)]
        after = body["page"]["next"]
    assert walked == refs
    assert after is None
    # A single row past the cap is still delivered, alone.
    assert len(listed(visitor_client, edge)["evidence"]) == 1


# ── Raw records ─────────────────────────────────────────────────────────────


def test_raw_shows_to_staff_and_on_a_public_view_release(demo, visitor_client):
    forward = stored(demo, TR, "SYN-TF1", "SYN-G1")
    [row] = listed(demo.staff, forward)["evidence"]
    assert row["record"]["raw"]["record_id"] == "syn-01"
    assert visitor_client.get(evidence_url(forward)).status_code == 404
    public_view(demo.v2, demo.alice)
    [row] = listed(visitor_client, forward)["evidence"]
    assert (row["record"]["key"], row["record"]["line_number"]) == ("syn-01", 2)
    assert row["record"]["raw"]["record_id"] == "syn-01"
    assert ids(listed(visitor_client, forward)["releases"]) == [ref("release", demo.v2)]


def test_raw_is_null_when_the_release_stores_none(
    registry, visitor_client, staff_client
):
    ev = evidence_release(records=0, store_raw_rows=False)
    approve(ev.release, ApprovalScope.PUBLIC_VIEW)
    edge = link(TR, entity_of(), entity_of())
    made = state(ev, edge, record=mapped_record(ev.release, ev.run, raw=None))
    advance(ev.release, PUBLISHED)
    for client in (visitor_client, staff_client):
        [row] = listed(client, edge)["evidence"]
        assert row["record"] == {
            "key": made.source_record.record_key,
            "line_number": made.source_record.line_number,
            "raw": None,
        }


# ── Releases ────────────────────────────────────────────────────────────────


def test_a_page_carries_the_entries_of_its_rows_releases(registry, visitor_client):
    first, second = release(), release()
    edge = link(TR, entity_of(), entity_of())
    rows = [state(first, edge), state(second, edge), state(first, edge)]
    publish(first, second)
    both = listed(visitor_client, edge)
    assert ids(both["releases"]) == [
        ref("release", first.release),
        ref("release", second.release),
    ]
    assert [row["release"] for row in both["evidence"]] == [
        ref("release", first.release),
        ref("release", second.release),
        ref("release", first.release),
    ]
    one = listed(visitor_client, edge, page_size="1", after=ref("evidence", rows[0]))
    assert ids(one["releases"]) == [ref("release", second.release)]
    pinned = listed(visitor_client, edge, **pin(second.release))
    assert rows_of(pinned) == [ref("evidence", rows[1])]
    assert ids(pinned["releases"]) == [ref("release", second.release)]
    body = page(visitor_client, edge, **pin(second.release))
    assert [row["release"] for row in body["by_release"]] == [
        ref("release", second.release)
    ]
    assert body["edge"]["assertion_count"] == 1


def test_a_visitor_sees_nothing_of_the_demonstration(demo, visitor_client):
    forward = stored(demo, TR, "SYN-TF1", "SYN-G1")
    for build in (relationship_url, evidence_url):
        response = visitor_client.get(build(forward))
        assert response.status_code == 404
        assert response.json() == NOT_FOUND
        assert visitor_client.get(build(forward, **pin(demo.v2))).json() == NOT_FOUND


def test_a_release_withdrawn_while_the_page_is_read_is_named_nowhere(
    registry, staff_user, visitor_client, monkeypatch
):
    from graph.releases import withdraw_release

    ev = release()
    edge = link(TR, entity_of(), entity_of())
    state(ev, edge)
    publish(ev)
    entries_of = graph.api.views.release_entries

    def withdrawn_first(pks, releases):
        withdraw_release(ev.release, reason="Test only.", withdrawn_by=staff_user)
        return entries_of(pks, releases)

    monkeypatch.setattr(graph.api.views, "release_entries", withdrawn_first)
    body = listed(visitor_client, edge)
    assert (body["evidence"], body["releases"]) == ([], [])
    assert ref("release", ev.release) not in json.dumps(body)
    # No row follows the one read, so nothing remains: next is null.
    assert body["page"] == {"page_size": EVIDENCE_PAGE_SIZE, "next": None}


def test_a_page_whose_rows_were_all_withdrawn_names_where_it_read_to(
    registry, staff_user, visitor_client, monkeypatch
):
    from graph.releases import withdraw_release

    withdrawn, kept = release(), release()
    edge = link(TR, entity_of(), entity_of())
    read = [state(withdrawn, edge) for _ in range(2)]
    last = state(kept, edge)
    publish(withdrawn, kept)
    entries_of = graph.api.views.release_entries

    def withdrawn_first(pks, releases):
        withdraw_release(
            withdrawn.release, reason="Test only.", withdrawn_by=staff_user
        )
        return entries_of(pks, releases)

    monkeypatch.setattr(graph.api.views, "release_entries", withdrawn_first)
    body = listed(visitor_client, edge, page_size="1")
    monkeypatch.setattr(graph.api.views, "release_entries", entries_of)
    # The one row read is left out, and rows follow it: next names it, so a
    # client that follows next until it is null reads every remaining row.
    assert (body["evidence"], body["releases"]) == ([], [])
    assert body["page"] == {"page_size": 1, "next": ref("evidence", read[0])}
    rest = listed(visitor_client, edge, page_size="1", after=body["page"]["next"])
    assert (rows_of(rest), rest["page"]["next"]) == ([ref("evidence", last)], None)


def test_a_release_withdrawn_before_the_summary_is_read_answers_the_404(
    registry, staff_user, visitor_client, monkeypatch
):
    from graph.releases import withdraw_release

    ev = release()
    edge = link(TR, entity_of(), entity_of())
    state(ev, edge)
    publish(ev)
    summarized = graph.api.views.summarize

    def withdrawn_first(*args, **kwargs):
        withdraw_release(ev.release, reason="Test only.", withdrawn_by=staff_user)
        return summarized(*args, **kwargs)

    # The existence query found the relationship's one visible assertion;
    # the summary, read after its release was withdrawn, finds none.
    monkeypatch.setattr(graph.api.views, "summarize", withdrawn_first)
    response = visitor_client.get(relationship_url(edge))
    assert response.status_code == 404
    assert response.json() == NOT_FOUND


# ── Throttles and status ────────────────────────────────────────────────────


def test_the_page_is_rationed_with_the_network_and_the_list_with_the_reads(
    registry, visitor_client, monkeypatch
):
    ev = release()
    seed = entity_of(created_by_release=ev.release)
    edge = link(TR, seed, entity_of())
    state(ev, edge)
    publish(ev)
    monkeypatch.setattr(
        GraphNetworkThrottle, "THROTTLE_RATES", {"graph_network": "2/min"}
    )
    monkeypatch.setattr(GraphReadThrottle, "THROTTLE_RATES", {"graph_read": "2/min"})
    codes = [
        visitor_client.get(url).status_code
        for url in (network_url(seed), relationship_url(edge), relationship_url(edge))
    ]
    assert codes == [200, 200, 429]
    codes = [
        visitor_client.get(url).status_code
        for url in (VOCABULARIES, evidence_url(edge), evidence_url(edge))
    ]
    assert codes == [200, 200, 429]


def test_status_says_the_relationships_are_served(switch_on, visitor_client):
    assert visitor_client.get(STATUS).json()["features"]["relationships"] is True


# ── The timeout ─────────────────────────────────────────────────────────────


def timed_out(response) -> None:
    assert response.status_code == 503, response.content
    assert response["Content-Type"] == "application/json"
    assert response.json() == {"detail": RequestTimedOut.default_detail}
    assert "Authorization" in response["Vary"]
    assert not response.has_header("Retry-After")


@pytest.mark.postgres
def test_a_cancelled_statement_is_a_503_on_both_routes(
    registry, staff_client, monkeypatch
):
    ev = release()
    edge = link(TR, entity_of(), entity_of())
    state(ev, edge)
    publish(ev)
    summarized = graph.api.views.summarize
    evidence_of = graph.api.views.evidence

    def slow_summary(ids, **arguments):
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_sleep(0.2)")
        return summarized(ids, **arguments)

    def slow_evidence(pk, **arguments):
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_sleep(0.2)")
        return evidence_of(pk, **arguments)

    monkeypatch.setattr(graph.api.views, "summarize", slow_summary)
    monkeypatch.setattr(graph.api.views, "evidence", slow_evidence)
    monkeypatch.setattr(graph.neighbourhood, "STATEMENT_TIMEOUT_MS", 20)
    timed_out(staff_client.get(relationship_url(edge)))
    timed_out(staff_client.get(evidence_url(edge)))
    browsable = staff_client.get(relationship_url(edge, format="api"))
    assert browsable.status_code == 503
    assert browsable["Content-Type"].startswith("text/html")
    # The connection serves the next request.
    monkeypatch.setattr(graph.api.views, "summarize", summarized)
    monkeypatch.setattr(graph.api.views, "evidence", evidence_of)
    monkeypatch.setattr(graph.neighbourhood, "STATEMENT_TIMEOUT_MS", 5000)
    assert page(staff_client, edge)["edge"]["id"] == ref("relationship", edge)
    assert len(listed(staff_client, edge)["evidence"]) == 1


def existence(sql) -> bool:
    return sql.startswith('SELECT "graph_relationship".') and "EXISTS" in sql


# The reads each route sends inside its timed block, told apart by their SQL:
# the existence query, summarize's assertion fetch and the per-release
# aggregate; the existence query, the slice, its first and last prefetch, and
# the kept rows' raw records.
TIMED_READS = {
    "page-existence": (relationship_url, existence),
    "page-summarize": (relationship_url, lambda sql: '"reported_in"' in sql),
    "page-by_release": (
        relationship_url,
        lambda sql: sql.startswith('SELECT "graph_evidenceassertion"."release_id"')
        and 'AS "n"' in sql,
    ),
    "list-existence": (evidence_url, existence),
    "list-slice": (
        evidence_url,
        lambda sql: sql.startswith('SELECT "graph_evidenceassertion"."id"'),
    ),
    "list-participants": (
        evidence_url,
        lambda sql: sql.startswith('SELECT "graph_assertionparticipant".'),
    ),
    "list-contexts": (
        evidence_url,
        lambda sql: sql.startswith('SELECT "graph_evidencecontext".'),
    ),
    "list-raw": (
        evidence_url,
        lambda sql: sql.startswith('SELECT "graph_sourcerecord".'),
    ),
}


@pytest.mark.postgres
@pytest.mark.parametrize("read", TIMED_READS)
def test_each_read_of_both_routes_runs_under_the_timeout(
    read, registry, staff_client, monkeypatch
):
    build, matches = TIMED_READS[read]
    ev = release()
    edge = link(TR, entity_of(), entity_of())
    state(ev, edge)
    publish(ev)
    sent = []

    def slow(execute, sql, params, many, context):
        # Sent just before the read, so only the block's timeout cancels it:
        # a read moved before the block runs it in full, and one moved after
        # it is cancelled outside timed(), which no 503 catches.
        if matches(sql):
            sent.append(sql)
            execute("SELECT pg_sleep(0.2)", None, False, context)
        return execute(sql, params, many, context)

    monkeypatch.setattr(graph.neighbourhood, "STATEMENT_TIMEOUT_MS", 20)
    with connection.execute_wrapper(slow):
        response = staff_client.get(build(edge))
    assert len(sent) == 1
    timed_out(response)

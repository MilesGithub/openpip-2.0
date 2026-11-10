"""GET /api/graph/v1/browsers and GET /api/graph/v1/browsers/{slug}: the
gallery of browser instances and one instance, as the caller may see them
(architecture §7.2).

Their parameters and every 400; the echo and the shapes; who lists and
reaches an instance; what a response names of the selected releases, and the
facets it derives from the releases the caller may see; availability for a
visitor and for staff as releases change; external services; and what each
request costs, against the statements browsers.api enumerates. Then browser=
on the five routes that read data: what an instance's slug selects, on the
demonstration too; that a slug pinning nothing the caller may see reads as
an unknown one; what it costs against releases= naming the same refs; its
400s and its echo. The walk of every route over every hidden release is in
test_api_visibility; the models' rules and availability's truth table are in
browsers/tests.

A release whose facets matter states its evidence with api_helpers.state(),
then carries the stats recounted from those rows and its declared taxa, both
written while it is a draft, as a commit writes them.
"""

import json
import re
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from browsers.availability import Availability
from browsers.models import (
    BrowserInstance,
    BrowserInstanceRelease,
    Kind,
    Status,
    Visibility,
)
from browsers.rules import TAGS
from browsers.serializers import (
    BrowserSerializer,
    BrowsersQuerySerializer,
    BrowsersSerializer,
    InstanceItemSerializer,
    InstanceSerializer,
)
from graph.api.limits import API_VERSION, Q_MAX_CHARS
from graph.approvals import revoke
from graph.claims import canonical_relationship, find_relationship
from graph.models import (
    ApprovalScope,
    DueDiligenceRecord,
    DueDiligenceStatus,
    EntityType,
    InteractionLevel,
    RelationshipType,
    UsageApproval,
)
from graph.releases import publish_release, withdraw_release
from graph.tests.api_helpers import (
    BROWSER,
    BROWSERS,
    ENTITY,
    EVIDENCE,
    HIDDEN_KINDS,
    NETWORK,
    RELATIONSHIP,
    REQUIRED,
    RESOLVE,
    browser_url,
    browsers_url,
    entity_url,
    evidence_url,
    hidden_release,
    ids,
    instance,
    link,
    network_url,
    pin,
    public,
    published,
    reachable,
    relationship_url,
    resolve_url,
    state,
    with_stats,
)
from graph.tests.factories import (
    PublicationFactory,
    ReleaseTaxonFactory,
    TermFactory,
    approvable_release,
    approve,
    entity_of,
    evidence_release,
    taxon_of,
)
from graph.tests.test_api_entities import merged_into
from graph.tests.test_provenance import PUBLISHED, STAGED, SUPERSEDED, advance

INTERNAL = ApprovalScope.INTERNAL
PUBLIC_VIEW = ApprovalScope.PUBLIC_VIEW
EXTERNAL_SERVICES = ApprovalScope.EXTERNAL_SERVICES
TR = "transcriptional_regulation"
PI = "physical_interaction"
NOT_FOUND = {"detail": "Not found."}
ENVELOPE = ["api_version", "query", "instances", "page"]
PAGE_ENVELOPE = ["api_version", "query", "instance", "releases"]
ITEM_KEYS = [
    "slug",
    "title",
    "short_title",
    "summary",
    "kind",
    "status",
    "visibility",
    "availability",
    "is_synthetic",
    "external_url",
    "tags",
    "taxa",
    "primary_taxon",
    "relationship_types",
    "assays",
    "publications",
]
# The gallery's parameters in the order they are documented, read and listed.
ALLOWED = [
    "q",
    "tag",
    "relationship_type",
    "taxon",
    "assay",
    "year",
    "status",
    "availability",
]
# The registry's relationship types and taxa, as the 400s list them.
TYPE_SLUGS = [
    "encodes",
    "isoform_of",
    "physical_interaction",
    "protein_interaction_unspecified",
    "transcriptional_regulation",
]
TAXIDS = ["7227", "9606", "32630"]
ASSAY_SENTENCE = "assay: an assay is a PSI-MI CURIE, such as MI:0018."
YEAR_SENTENCE = "year is a whole number from 1 to 32,767."
URL = "https://portal.example/browse"
# The entity types each relationship type relates, subject then object.
ENDPOINTS = {
    TR: ("gene", "gene"),
    PI: ("protein", "protein"),
    "encodes": ("gene", "protein"),
    "isoform_of": ("protein_isoform", "protein"),
}

# What a request costs once its parameters are read, as browsers.api
# enumerates it. Derived facets: the declared taxa, then the stats and the
# reference types, sent only when an instance that states none selects a
# release the caller may see. Labels: label_release_set's two and
# term_labels' one, sent only when an instance shown names an assay.
DERIVED = 1 + 2
LABELS = 2 + 1
# The gallery: its instances, their selection rows, four prefetches (the
# citation, taxa, relationship types and assays) and the visible set, then
# the derived facets and the labels.
GALLERY = 1 + 1 + 4 + 1 + DERIVED + LABELS
# relationship_type and taxon each read their registry.
VOCABULARY = 1
# An instance's page: the instance, its selection rows, five prefetches (the
# entity types too) and the visible set, then the derived facets, the labels
# and release_entries' three; the external set only for a live instance
# whose flag is on.
ENTRIES = 3
PAGE = 1 + 1 + 5 + 1 + DERIVED + LABELS + ENTRIES
EXTERNAL = 1
# A real token is one statement: its account.
TOKEN = 1
# The routes that read data, each of which takes browser=.
READERS = (RESOLVE, ENTITY, NETWORK, RELATIONSHIP, EVIDENCE)
# A slug no instance has.
NO_INSTANCE = "no-such-instance"
SLUG_SENTENCE = "browser looks like a slug, such as synthetic-regulation."


def problem(parameter, value=None, allowed=None, limit=None) -> dict:
    return {"parameter": parameter, "value": value, "allowed": allowed, "limit": limit}


def ref(release) -> str:
    return f"release:{release.pk}"


def gallery(client, **parameters) -> dict:
    """The body of a 200 for the gallery under these filters."""
    response = client.get(browsers_url(**parameters))
    assert response.status_code == 200, response.content
    return response.json()


def listed(client, **parameters) -> list:
    """The slugs the gallery lists under these filters, in order."""
    return [item["slug"] for item in gallery(client, **parameters)["instances"]]


def item_of(client, row, **parameters) -> dict:
    """The instance's entry in the gallery."""
    [item] = [
        item
        for item in gallery(client, **parameters)["instances"]
        if item["slug"] == row.slug
    ]
    return item


def page(client, row) -> dict:
    """The body of a 200 for the instance's page."""
    response = client.get(browser_url(row.slug))
    assert response.status_code == 200, response.content
    return response.json()


def refused(client, query) -> dict:
    """The body of a 400 for this query string to the gallery."""
    response = client.get(f"{BROWSERS}?{query}")
    assert response.status_code == 400, response.content
    return response.json()


def answered(client, url) -> tuple:
    """What tells one response from another: its status, its body, its
    headers and the statements it took."""
    with CaptureQueriesContext(connection) as queries:
        response = client.get(url)
    headers = sorted(response.headers.items())
    return response.status_code, response.content, headers, len(queries)


def bearer(user) -> APIClient:
    """A client holding a real token for this account."""
    client = APIClient()
    token = RefreshToken.for_user(user).access_token
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def names(body, release) -> bool:
    """Whether the body names this release anywhere: release:12 is not in
    release:123."""
    return re.search(rf"{ref(release)}(?!\d)", json.dumps(body)) is not None


def holding(*types, taxa=(), scopes=(INTERNAL, PUBLIC_VIEW), status=PUBLISHED):
    """A release whose evidence states one relationship of each type, with its
    stats recounted from those rows and these taxa declared, all while it is
    a draft; then approved for these scopes, none for an unapproved one, and
    moved to this status."""
    ev = evidence_release(records=0, approved=False)
    for slug in types:
        subject, object_ = ENDPOINTS[slug]
        # Physical evidence states its level: not_stated without a type.
        level = {PI: InteractionLevel.NOT_STATED}.get(slug)
        relationship = link(slug, entity_of(subject), entity_of(object_))
        state(ev, relationship, interaction_level=level)
    with_stats(ev.release)
    for taxid in taxa:
        ReleaseTaxonFactory(release=ev.release, taxon=taxon_of(taxid))
    approve(ev.release, *scopes)
    advance(ev.release, status)
    return ev.release


def superseded():
    """A superseded release with both scopes approved, as publishing a
    correction leaves the release it corrects."""
    release = approvable_release(status=SUPERSEDED)
    approve(release, INTERNAL, PUBLIC_VIEW)
    return release


def availability_of(client, row) -> str:
    """The instance's availability for this caller, which its gallery entry,
    its page and the gallery's filter agree on."""
    shown = page(client, row)["instance"]["availability"]
    assert item_of(client, row)["availability"] == shown
    assert row.slug in listed(client, availability=shown)
    return shown


# ── The parameters ──────────────────────────────────────────────────────────


def test_every_problem_is_listed_in_the_routes_order(registry, visitor_client):
    # The query string runs backwards, so the order shown is the route's.
    query = (
        "availability=LIVE&status=Planned&year=0&assay=ECO:0000353&taxon=%2B9606"
        "&relationship_type=nope&tag=nope&q=&foo=1"
    )
    assert refused(visitor_client, query) == {
        "detail": " ".join(
            [
                f"Unknown parameter 'foo'. Allowed: {', '.join(ALLOWED)}.",
                "q names a value.",
                f"Unknown tag 'nope'. Allowed: {', '.join(TAGS)}.",
                f"Unknown relationship_type 'nope'. Allowed: {', '.join(TYPE_SLUGS)}.",
                f"Unknown taxon '+9606'. Allowed: {', '.join(TAXIDS)}.",
                ASSAY_SENTENCE,
                YEAR_SENTENCE,
                "Unknown status 'Planned'. Allowed: planned, available, retired.",
                "Unknown availability 'LIVE'. Allowed: live, planned, external, "
                "metadata_only, retired.",
            ]
        ),
        "errors": [
            problem("foo", allowed=ALLOWED),
            problem("q"),
            problem("tag", "nope", allowed=list(TAGS)),
            problem("relationship_type", "nope", allowed=TYPE_SLUGS),
            problem("taxon", "+9606", allowed=TAXIDS),
            problem("assay", "ECO:0000353"),
            problem("year", "0", limit=32_767),
            problem("status", "Planned", allowed=Status.values),
            problem("availability", "LIVE", allowed=Availability.values),
        ],
    }


@pytest.mark.parametrize(
    "query,error,detail",
    [
        ("q=", problem("q"), "q names a value."),
        ("q=%20%09", problem("q", " \t"), "q names a value."),
        (
            f"q={'x' * (Q_MAX_CHARS + 1)}",
            problem("q", "x" * (Q_MAX_CHARS + 1), limit=Q_MAX_CHARS),
            f"q is at most {Q_MAX_CHARS} characters.",
        ),
        ("q=a%00b", problem("q", "a\x00b"), "q holds a control character."),
        ("q=a%1Fb", problem("q", "a\x1fb"), "q holds a control character."),
        ("q=a%C2%85b", problem("q", "a\x85b"), "q holds a control character."),
        (
            "tag=Gene_regulation",
            problem("tag", "Gene_regulation", allowed=list(TAGS)),
            f"Unknown tag 'Gene_regulation'. Allowed: {', '.join(TAGS)}.",
        ),
        (
            "relationship_type=Encodes",
            problem("relationship_type", "Encodes", allowed=TYPE_SLUGS),
            f"Unknown relationship_type 'Encodes'. Allowed: {', '.join(TYPE_SLUGS)}.",
        ),
        *(
            (
                f"taxon={sent}",
                problem("taxon", given, allowed=TAXIDS),
                f"Unknown taxon {given!r}. Allowed: {', '.join(TAXIDS)}.",
            )
            for sent, given in (
                ("9_606", "9_606"),
                ("%D9%A9606", "\u0669606"),
                ("", ""),
                ("1", "1"),
            )
        ),
        *(
            (f"assay={sent}", problem("assay", given), ASSAY_SENTENCE)
            for sent, given in (
                ("MI:18", "MI:18"),
                ("MI:%200018", "MI: 0018"),
                ("MI:0018,MI:0019", "MI:0018,MI:0019"),
                ("psi-mi:MI:0018", "psi-mi:MI:0018"),
                ("ECO:0000353", "ECO:0000353"),
                (
                    "MI:%EF%BC%90%EF%BC%90%EF%BC%91%EF%BC%98",
                    "MI:\uff10\uff10\uff11\uff18",
                ),
                ("MI:0018%00", "MI:0018\x00"),
                ("", ""),
            )
        ),
        *(
            (f"year={sent}", problem("year", given, limit=32_767), YEAR_SENTENCE)
            for sent, given in (
                ("0", "0"),
                ("32768", "32768"),
                ("0032", "0032"),
                ("-1", "-1"),
                ("%D9%A3", "\u0663"),
                ("1" * 30, "1" * 30),
            )
        ),
        (
            "status=Planned",
            problem("status", "Planned", allowed=Status.values),
            "Unknown status 'Planned'. Allowed: planned, available, retired.",
        ),
        (
            "availability=data_unavailable",
            problem("availability", "data_unavailable", allowed=Availability.values),
            "Unknown availability 'data_unavailable'. Allowed: live, planned, "
            "external, metadata_only, retired.",
        ),
    ],
)
def test_a_bad_value_is_a_400_that_names_it(
    query, error, detail, registry, visitor_client
):
    assert refused(visitor_client, query) == {"detail": detail, "errors": [error]}


@pytest.mark.parametrize("name", ALLOWED)
def test_each_parameter_is_given_once(name, switch_on, visitor_client):
    assert refused(visitor_client, f"{name}=x&{name}=y") == {
        "detail": f"{name} is given once.",
        "errors": [problem(name)],
    }


def test_a_long_value_holding_a_control_character_says_both(switch_on, visitor_client):
    phrase = "\x00" + "x" * Q_MAX_CHARS
    assert refused(visitor_client, f"q=%00{'x' * Q_MAX_CHARS}")["errors"] == [
        problem("q", phrase, limit=Q_MAX_CHARS),
        problem("q", phrase),
    ]


def test_a_bad_request_reads_only_the_vocabularies_it_names(
    registry, visitor_client, django_assert_num_queries
):
    with django_assert_num_queries(0):
        refused(visitor_client, "q=&tag=x&assay=x&year=0&status=x&availability=x&f=1")
    with django_assert_num_queries(2 * VOCABULARY):
        refused(visitor_client, "relationship_type=x&taxon=5")


@pytest.mark.parametrize("name", ["browser", "releases", "foo"])
def test_a_selection_is_an_unknown_name_on_both_routes(
    name, switch_on, visitor_client, django_assert_num_queries
):
    assert refused(visitor_client, f"{name}=x") == {
        "detail": f"Unknown parameter {name!r}. Allowed: {', '.join(ALLOWED)}.",
        "errors": [problem(name, allowed=ALLOWED)],
    }
    # Before the slug is read, whatever it is.
    for slug in ("an-instance", "Bad_Slug", "%00", "{slug}"):
        with django_assert_num_queries(0):
            response = visitor_client.get(f"{browser_url(slug)}?{name}=x")
        assert response.status_code == 400
        assert response.json() == {
            "detail": f"Unknown parameter {name!r}. This route takes no parameters.",
            "errors": [problem(name, allowed=[])],
        }


@pytest.mark.parametrize(
    "parameters",
    [{"q": "no such words"}, {"year": "1999"}, {"assay": "MI:0019"}],
    ids=["q", "year", "assay"],
)
def test_a_well_formed_value_no_instance_has_lists_nothing(
    parameters, registry, visitor_client
):
    row = instance(
        public(),
        assays=[TermFactory(curie="MI:0018")],
        publications=[PublicationFactory(year=2020)],
    )
    assert listed(visitor_client) == [row.slug]
    assert listed(visitor_client, **parameters) == []


# ── The echo and the shapes ─────────────────────────────────────────────────


def test_the_echo_is_the_request_as_read(registry, visitor_client):
    body = gallery(
        visitor_client,
        q="  Alpha  ",
        tag="gene_regulation",
        relationship_type=TR,
        taxon="9606",
        assay=" mi:0018 ",
        year="2020",
        status="available",
        availability="live",
    )
    assert body["query"] == {
        "q": "Alpha",
        "tag": "gene_regulation",
        "relationship_type": TR,
        "taxon": 9606,
        "assay": "MI:0018",
        "year": 2020,
        "status": "available",
        "availability": "live",
    }
    assert list(body["query"]) == list(BrowsersQuerySerializer().fields) == ALLOWED


def test_with_nothing_given_the_query_is_null(switch_on, visitor_client):
    assert gallery(visitor_client)["query"] == dict.fromkeys(ALLOWED)


def test_the_envelopes_and_their_key_order(registry, visitor_client):
    row = instance(public())
    body = gallery(visitor_client)
    assert list(body) == ENVELOPE
    assert body["api_version"] == API_VERSION
    # Every instance is on the one page.
    assert body["page"] == {"next": None}
    [item] = body["instances"]
    assert list(item) == ITEM_KEYS == list(InstanceItemSerializer().fields)
    assert json.dumps(body) == json.dumps(BrowsersSerializer(instance=body).data)
    detail = page(visitor_client, row)
    assert list(detail) == PAGE_ENVELOPE
    assert detail["api_version"] == API_VERSION
    assert detail["query"] == {"slug": row.slug}
    assert list(detail["instance"]) == list(InstanceSerializer().fields)
    assert json.dumps(detail) == json.dumps(BrowserSerializer(instance=detail).data)


def test_the_page_of_an_instance(registry, visitor_client):
    ev = evidence_release(records=0)
    state(ev, link(TR, entity_of(), entity_of()))
    with_stats(ev.release)
    ReleaseTaxonFactory(release=ev.release, taxon=taxon_of(9606))
    two_hybrid = TermFactory(
        curie="MI:0018", label="two hybrid", label_release=ev.release
    )
    approve(ev.release, PUBLIC_VIEW)
    advance(ev.release, PUBLISHED)
    paper = PublicationFactory(title="A study", authors="A. Author", year=2020)
    row = instance(
        ev.release,
        slug="a-study",
        title="A study's browser",
        short_title="A study",
        summary="What the study found.",
        description="Paragraph one.\n\nParagraph two.",
        methods_text="Two-hybrid screens.",
        tags=["gene_regulation", "protein_interaction"],
        entity_types=[
            EntityType.objects.get(slug=slug) for slug in ("protein", "gene")
        ],
        assays=[two_hybrid],
        publications=[paper],
        presentation={"version": 1, "layout": "grid", "accent": "teal"},
        display_order=3,
    )
    body = page(visitor_client, row)
    [entry] = body["releases"]
    assert entry["id"] == ref(ev.release)
    assert body == {
        "api_version": API_VERSION,
        "query": {"slug": "a-study"},
        "instance": {
            "slug": "a-study",
            "title": "A study's browser",
            "short_title": "A study",
            "summary": "What the study found.",
            "kind": "typed",
            "status": "available",
            "visibility": "public",
            "availability": "live",
            "is_synthetic": False,
            "external_url": None,
            "tags": ["gene_regulation", "protein_interaction"],
            "taxa": [9606],
            "primary_taxon": None,
            "relationship_types": [TR],
            "assays": [{"curie": "MI:0018", "label": "two hybrid"}],
            "publications": [
                {
                    "pmid": paper.pmid,
                    "doi": None,
                    "pmcid": None,
                    "imex_id": None,
                    "local_key": None,
                    "title": "A study",
                    "authors": "A. Author",
                    "journal": "",
                    "year": 2020,
                    "url": None,
                    "is_synthetic": False,
                }
            ],
            "description": "Paragraph one.\n\nParagraph two.",
            "methods_text": "Two-hybrid screens.",
            "stated_entity_types": ["gene", "protein"],
            "selection": [{"release": ref(ev.release), "role": "primary"}],
            "presentation": {"version": 1, "layout": "grid", "accent": "teal"},
            "allow_external_services": False,
            "external_services_effective": False,
        },
        "releases": [entry],
    }
    # The gallery shows the same instance with the gallery's keys alone.
    item = item_of(visitor_client, row)
    assert item == {key: body["instance"][key] for key in ITEM_KEYS}


def test_the_citation_keeps_its_stored_order_on_both_routes(registry, visitor_client):
    older = PublicationFactory(title="Older")
    newer = PublicationFactory(title="Newer")
    row = instance(public(), publications=[newer, older])

    def titles():
        shown = (item_of(visitor_client, row), page(visitor_client, row)["instance"])
        return [[paper["title"] for paper in body["publications"]] for body in shown]

    assert titles() == [["Newer", "Older"]] * 2
    # A tie goes by publication id.
    row.citations.update(order=0)
    assert titles() == [["Older", "Newer"]] * 2


def test_markup_comes_back_as_it_was_typed(registry, visitor_client):
    row = instance(
        public(),
        title="<b>x</b> &amp; y",
        summary="<i>a</i> &amp; b",
        description="<script>alert(1)</script> &amp;\n<p>c</p>",
    )
    response = visitor_client.get(browser_url(row.slug))
    shown = (item_of(visitor_client, row), response.json()["instance"])
    for body in shown:
        assert body["title"] == "<b>x</b> &amp; y"
        assert body["summary"] == "<i>a</i> &amp; b"
    assert shown[1]["description"] == "<script>alert(1)</script> &amp;\n<p>c</p>"
    # Nothing is escaped on the way: the bytes are the JSON of the stored text.
    for text in (row.title, row.summary, row.description):
        assert json.dumps(text, ensure_ascii=False).encode() in response.content


def test_presentation_is_on_the_page_alone_and_as_stored(registry, visitor_client):
    stored = {"version": 1, "filter_panels": ["layers", "taxon"], "accent": "grey"}
    row = instance(public(), presentation=stored)
    assert "presentation" not in item_of(visitor_client, row)
    assert page(visitor_client, row)["instance"]["presentation"] == stored
    assert page(visitor_client, instance(public()))["instance"]["presentation"] == {}


def test_an_external_url_is_null_but_for_an_external_instance(registry, visitor_client):
    external = instance(kind="external", external_url=URL)
    typed = instance(public())
    portal = instance(kind="portal_ppi")
    assert item_of(visitor_client, external)["external_url"] == URL
    assert page(visitor_client, external)["instance"]["external_url"] == URL
    for row in (typed, portal):
        assert item_of(visitor_client, row)["external_url"] is None
        assert page(visitor_client, row)["instance"]["external_url"] is None


def test_both_routes_vary_by_authorization(registry, visitor_client):
    row = instance(public())
    for url in (
        BROWSERS,
        f"{BROWSERS}?foo=1",
        browser_url(row.slug),
        f"{browser_url(row.slug)}?foo=1",
        browser_url("no-such-instance"),
        browser_url("Bad_Slug"),
    ):
        response = visitor_client.get(url)
        assert "Authorization" in re.split(r"\s*,\s*", response["Vary"]), url


def test_both_routes_carry_their_operation_ids(db, visitor_client):
    # Left to itself the generator gives both routes one id; client method
    # names follow these.
    paths = visitor_client.get("/api/schema/", {"format": "json"}).json()["paths"]
    assert paths[BROWSERS]["get"]["operationId"] == "api_graph_v1_browsers_list"
    assert paths[BROWSER]["get"]["operationId"] == "api_graph_v1_browsers_retrieve"


# ── The gallery ─────────────────────────────────────────────────────────────


@pytest.fixture
def shelf(registry):
    """Four public instances, each the match of its own filters."""
    two_hybrid = TermFactory(curie="MI:0018")
    fly = taxon_of(7227)
    return SimpleNamespace(
        alpha=instance(
            holding(TR, taxa=[9606]),
            slug="alpha",
            title="Alpha regulation",
            tags=["gene_regulation"],
            assays=[two_hybrid],
            publications=[PublicationFactory(year=2020)],
        ),
        beta=instance(
            holding(PI),
            slug="beta",
            title="Beta",
            short_title="Fly pairs",
            tags=["protein_interaction"],
            taxa=[fly],
            primary_taxon=fly,
            publications=[PublicationFactory(year=2021)],
        ),
        gamma=instance(
            kind="external",
            external_url=URL,
            slug="gamma",
            title="Gamma portal",
            summary="Disease links elsewhere",
            tags=["disease"],
            taxa=[taxon_of(9606)],
        ),
        delta=instance(
            kind="metadata_only", status="planned", slug="delta", title="Delta card"
        ),
    )


@pytest.mark.parametrize(
    "parameters,expected",
    [
        ({}, ["alpha", "beta", "delta", "gamma"]),
        ({"q": "regulation"}, ["alpha"]),
        # The short title, with no regard to case.
        ({"q": "FLY PAIRS"}, ["beta"]),
        # The summary.
        ({"q": "links else"}, ["gamma"]),
        ({"tag": "gene_regulation"}, ["alpha"]),
        ({"tag": "disease"}, ["gamma"]),
        ({"tag": "signaling"}, []),
        # Each derived from the instance's release.
        ({"relationship_type": TR}, ["alpha"]),
        ({"relationship_type": PI}, ["beta"]),
        ({"relationship_type": "encodes"}, []),
        # Declared by alpha's release, and stated by gamma and beta.
        ({"taxon": "9606"}, ["alpha", "gamma"]),
        ({"taxon": "7227"}, ["beta"]),
        ({"taxon": "32630"}, []),
        ({"assay": "MI:0018"}, ["alpha"]),
        ({"assay": "mi:0018"}, ["alpha"]),
        ({"year": "2020"}, ["alpha"]),
        ({"year": "2021"}, ["beta"]),
        ({"status": "planned"}, ["delta"]),
        ({"status": "available"}, ["alpha", "beta", "gamma"]),
        ({"status": "retired"}, []),
        ({"availability": "live"}, ["alpha", "beta"]),
        ({"availability": "external"}, ["gamma"]),
        ({"availability": "planned"}, ["delta"]),
        ({"availability": "metadata_only"}, []),
        # Filters combine with AND.
        ({"status": "available", "taxon": "9606"}, ["alpha", "gamma"]),
        ({"availability": "live", "taxon": "9606"}, ["alpha"]),
        ({"tag": "gene_regulation", "year": "2021"}, []),
        ({"q": "a", "tag": "protein_interaction", "taxon": "7227"}, ["beta"]),
    ],
)
def test_each_filter_narrows_the_gallery_and_filters_combine(
    parameters, expected, shelf, visitor_client
):
    assert listed(visitor_client, **parameters) == expected


def test_a_year_matches_any_paper_of_the_citation(registry, visitor_client):
    row = instance(
        public(),
        publications=[PublicationFactory(year=2020), PublicationFactory(year=2021)],
    )
    assert listed(visitor_client, year="2020") == [row.slug]
    assert listed(visitor_client, year="2021") == [row.slug]
    assert listed(visitor_client, year="2022") == []


def test_q_folds_case_as_casefold_does(registry, visitor_client):
    row = instance(public(), title="Straße regulation")
    assert listed(visitor_client, q="STRASSE") == [row.slug]


def test_a_primary_taxon_matches_beside_the_derived_ones(registry, visitor_client):
    row = instance(holding(TR, taxa=[9606]), primary_taxon=taxon_of(7227))
    item = item_of(visitor_client, row)
    assert (item["taxa"], item["primary_taxon"]) == ([9606], 7227)
    assert listed(visitor_client, taxon="9606") == [row.slug]
    assert listed(visitor_client, taxon="7227") == [row.slug]


def test_the_gallery_is_ordered_by_display_order_then_slug(registry, visitor_client):
    for slug, order in (("b-two", 1), ("a-three", 2), ("c-one", 1), ("d-zero", 0)):
        instance(kind="metadata_only", slug=slug, display_order=order)
    assert listed(visitor_client) == ["d-zero", "b-two", "c-one", "a-three"]
    assert gallery(visitor_client)["page"] == {"next": None}


def test_an_empty_gallery_lists_nothing(switch_on, visitor_client):
    assert gallery(visitor_client)["instances"] == []


# ── Who lists and reaches an instance ───────────────────────────────────────


def test_an_unlisted_or_staff_instance_is_in_no_visitor_list(
    registry, visitor_client, user_auth_client, staff_client
):
    release = holding(TR, taxa=[9606])
    matching = {
        "title": "Matching regulation",
        "tags": ["gene_regulation"],
        "assays": [TermFactory(curie="MI:0018")],
        "publications": [PublicationFactory(year=2020)],
    }
    unlisted = instance(release, visibility="unlisted", slug="by-slug", **matching)
    hidden = instance(release, visibility="staff", slug="staff-only", **matching)
    every_filter = {
        "q": "matching",
        "tag": "gene_regulation",
        "relationship_type": TR,
        "taxon": "9606",
        "assay": "MI:0018",
        "year": "2020",
        "status": "available",
        "availability": "live",
    }
    for client in (visitor_client, user_auth_client):
        assert listed(client) == []
        for name, value in every_filter.items():
            assert listed(client, **{name: value}) == [], name
        assert listed(client, **every_filter) == []
    # Staff list both, each saying who may see it.
    assert [
        (item["slug"], item["visibility"])
        for item in gallery(staff_client, **every_filter)["instances"]
    ] == [("by-slug", "unlisted"), ("staff-only", "staff")]
    # An unlisted instance answers by its slug.
    for client in (visitor_client, user_auth_client):
        assert page(client, unlisted)["instance"]["visibility"] == "unlisted"
        assert client.get(browser_url(hidden.slug)).json() == NOT_FOUND
    assert page(staff_client, hidden)["instance"]["visibility"] == "staff"


def test_an_unknown_slug_and_an_unreachable_instance_answer_alike(
    registry, visitor_client, user_auth_client
):
    hidden = instance(public(), visibility="staff")
    for client in (visitor_client, user_auth_client):
        unknown = answered(client, browser_url("no-such-instance"))
        assert (unknown[0], json.loads(unknown[1])) == (404, NOT_FOUND)
        assert answered(client, browser_url(hidden.slug)) == unknown


@pytest.mark.parametrize(
    "slug",
    [
        "Synthetic",
        "a_b",
        "a--b",
        "-a",
        "a-",
        "%20",
        "a%20b",
        "%00",
        "a%00b",
        "a%1Fb",
        "%FF",
        "a" * 51,
        "a" * 5000,
        "{slug}",
    ],
)
def test_a_slug_outside_the_grammar_is_the_404_with_no_statement(
    slug, registry, visitor_client, django_assert_num_queries
):
    with django_assert_num_queries(0):
        response = visitor_client.get(browser_url(slug))
    assert response.status_code == 404
    assert response.json() == NOT_FOUND


def test_the_longest_slug_the_grammar_takes_is_looked_up(registry, visitor_client):
    row = instance(kind="metadata_only", slug="a" * 50)
    assert page(visitor_client, row)["query"]["slug"] == "a" * 50


# ── What a response names ───────────────────────────────────────────────────


def test_a_selected_release_the_caller_may_not_see_is_named_nowhere(
    registry, visitor_client, staff_client
):
    shown, hidden = public(), published(INTERNAL)
    row = instance(shown, hidden)
    body = page(visitor_client, row)
    assert body["instance"]["selection"] == [{"release": ref(shown), "role": "primary"}]
    assert [entry["id"] for entry in body["releases"]] == [ref(shown)]
    assert not names(body, hidden)
    assert not names(gallery(visitor_client), hidden)
    staff = page(staff_client, row)
    assert [item["release"] for item in staff["instance"]["selection"]] == [
        ref(shown),
        ref(hidden),
    ]
    assert [entry["id"] for entry in staff["releases"]] == [ref(shown), ref(hidden)]
    # The gallery names no release, to anyone.
    assert not names(gallery(staff_client), shown)


def test_the_selection_shows_no_stored_order(registry, visitor_client):
    first, hidden, third = public(), published(INTERNAL), public()
    row = instance(first, hidden, third)
    assert page(visitor_client, row)["instance"]["selection"] == [
        {"release": ref(first), "role": "primary"},
        {"release": ref(third), "role": "primary"},
    ]
    # In stored order, ties by release id, with each role.
    BrowserInstanceRelease.objects.filter(instance=row, release=first).update(
        order=5, role="context"
    )
    BrowserInstanceRelease.objects.filter(instance=row, release=third).update(order=5)
    assert page(visitor_client, row)["instance"]["selection"] == [
        {"release": ref(first), "role": "context"},
        {"release": ref(third), "role": "primary"},
    ]
    # The stored order comes before the release id.
    BrowserInstanceRelease.objects.filter(instance=row, release=third).update(order=4)
    assert page(visitor_client, row)["instance"]["selection"] == [
        {"release": ref(third), "role": "primary"},
        {"release": ref(first), "role": "context"},
    ]


def test_staff_see_a_staged_selected_release_of_an_instance_not_yet_live(
    registry, staff_user, visitor_client, staff_client
):
    staged = hidden_release("staged", staff_user)
    row = instance(staged)
    body = page(staff_client, row)
    assert body["instance"]["availability"] == "metadata_only"
    assert body["instance"]["selection"] == [
        {"release": ref(staged), "role": "primary"}
    ]
    [entry] = body["releases"]
    assert (entry["id"], entry["status"], entry["superseded"]) == (
        ref(staged),
        "staged",
        False,
    )
    visitor = page(visitor_client, row)
    assert (visitor["instance"]["selection"], visitor["releases"]) == ([], [])


@pytest.mark.parametrize("caller", ["visitor", "staff"])
def test_the_derived_facets_are_those_of_the_releases_the_caller_may_see(
    caller, registry, visitor_client, staff_client
):
    client = staff_client if caller == "staff" else visitor_client
    shown = holding(TR, taxa=[9606])
    # Published with no approval: hidden from both.
    hidden = holding(PI, taxa=[7227], scopes=())
    row = instance(shown, hidden)
    for body in (item_of(client, row), page(client, row)["instance"]):
        assert (body["taxa"], body["relationship_types"]) == ([9606], [TR])
    assert listed(client, taxon="9606") == [row.slug]
    assert listed(client, relationship_type=TR) == [row.slug]
    assert listed(client, taxon="7227") == []
    assert listed(client, relationship_type=PI) == []


def test_a_release_only_staff_may_see_gives_its_facets_to_staff_alone(
    registry, visitor_client, staff_client
):
    row = instance(holding(PI, taxa=[7227], scopes=(INTERNAL,)))
    for body in (item_of(visitor_client, row), page(visitor_client, row)["instance"]):
        assert (body["taxa"], body["relationship_types"]) == ([], [])
    assert listed(visitor_client, taxon="7227") == []
    assert listed(visitor_client, relationship_type=PI) == []
    for body in (item_of(staff_client, row), page(staff_client, row)["instance"]):
        assert (body["taxa"], body["relationship_types"]) == ([7227], [PI])
    assert listed(staff_client, taxon="7227") == [row.slug]
    assert listed(staff_client, relationship_type=PI) == [row.slug]


def test_a_reference_type_is_never_derived(registry, visitor_client):
    release = holding("encodes", "isoform_of", TR)
    row = instance(release)
    assert item_of(visitor_client, row)["relationship_types"] == [TR]
    assert listed(visitor_client, relationship_type="encodes") == []
    # Stated, it is shown and matched as stated.
    stated = instance(
        release, relationship_types=[RelationshipType.objects.get(slug="encodes")]
    )
    assert item_of(visitor_client, stated)["relationship_types"] == ["encodes"]
    assert listed(visitor_client, relationship_type="encodes") == [stated.slug]


def test_stated_facets_are_shown_as_stated(registry, visitor_client):
    fly = taxon_of(7227)
    row = instance(
        holding(TR, taxa=[9606]),
        taxa=[fly],
        primary_taxon=fly,
        relationship_types=[RelationshipType.objects.get(slug=PI)],
        entity_types=[EntityType.objects.get(slug="protein")],
    )
    body = page(visitor_client, row)["instance"]
    assert (
        body["taxa"],
        body["primary_taxon"],
        body["relationship_types"],
        body["stated_entity_types"],
    ) == ([7227], 7227, [PI], ["protein"])
    assert listed(visitor_client, taxon="9606") == []
    assert listed(visitor_client, relationship_type=TR) == []
    assert listed(visitor_client, taxon="7227") == [row.slug]


def test_derived_taxa_and_types_ascend(registry, visitor_client):
    row = instance(holding(TR, PI, taxa=[10090, 9606]))
    for body in (item_of(visitor_client, row), page(visitor_client, row)["instance"]):
        assert (body["taxa"], body["relationship_types"]) == ([9606, 10090], [PI, TR])


def test_stated_taxa_and_types_ascend(registry, visitor_client):
    row = instance(
        public(),
        taxa=[taxon_of(10090), taxon_of(9606)],
        relationship_types=[
            RelationshipType.objects.get(slug=TR),
            RelationshipType.objects.get(slug=PI),
        ],
    )
    for body in (item_of(visitor_client, row), page(visitor_client, row)["instance"]):
        assert (body["taxa"], body["relationship_types"]) == ([9606, 10090], [PI, TR])


def test_another_kind_shows_no_selection_and_derives_nothing(registry, visitor_client):
    release = holding(TR, taxa=[9606])
    row = instance(kind="portal_ppi")
    # A bare save past rules.validate_selection, which no write path makes.
    BrowserInstanceRelease.objects.create(instance=row, release=release)
    body = page(visitor_client, row)
    assert (body["instance"]["selection"], body["releases"]) == ([], [])
    assert (body["instance"]["taxa"], body["instance"]["relationship_types"]) == (
        [],
        [],
    )
    assert body["instance"]["availability"] == "live"


def test_is_synthetic_is_stored_or_read_from_the_releases_the_caller_may_see(
    registry, visitor_client, staff_client
):
    row = instance(public(), published(INTERNAL, is_synthetic=True))
    for client, expected in ((visitor_client, False), (staff_client, True)):
        assert item_of(client, row)["is_synthetic"] is expected
        assert page(client, row)["instance"]["is_synthetic"] is expected
    card = instance(kind="metadata_only", is_synthetic=True)
    assert item_of(visitor_client, card)["is_synthetic"] is True


def test_an_assay_label_shows_to_a_caller_who_may_read_its_release(
    registry, visitor_client, staff_client
):
    ev = evidence_release(records=0)
    stated = TermFactory(curie="MI:0018", label="two hybrid", label_release=ev.release)
    advance(ev.release, SUPERSEDED)
    # A crosswalk term shows its pinned label to everyone.
    direct = TermFactory(curie="MI:0407", label="direct interaction")
    # The labels' release is not the instance's, and superseded it is not in
    # the default view set either: only label_set, which ignores selections,
    # holds it.
    row = instance(public(), assays=[direct, stated])
    for client, label in ((visitor_client, None), (staff_client, "two hybrid")):
        for body in (item_of(client, row), page(client, row)["instance"]):
            assert body["assays"] == [
                {"curie": "MI:0018", "label": label},
                {"curie": "MI:0407", "label": "direct interaction"},
            ]


# ── Availability ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "state,visitor,staff",
    [
        ("public", "live", "live"),
        ("internal", "metadata_only", "live"),
        ("superseded", "live", "live"),
        # Staff see an embargoed release; a staged one is not yet live.
        *(
            (kind, "metadata_only", "live" if kind == "embargoed" else "metadata_only")
            for kind in HIDDEN_KINDS
        ),
    ],
)
def test_a_typed_instance_is_live_while_its_release_is_published_and_seen(
    state, visitor, staff, registry, staff_user, visitor_client, staff_client
):
    made = {
        "public": public,
        "internal": lambda: published(INTERNAL),
        "superseded": superseded,
    }
    release = made.get(state, lambda: hidden_release(state, staff_user))()
    row = instance(release)
    assert availability_of(visitor_client, row) == visitor
    assert availability_of(staff_client, row) == staff


def test_one_release_the_caller_may_not_see_keeps_an_instance_from_live(
    registry, staff_user, visitor_client, staff_client
):
    mixed = instance(public(), published(INTERNAL))
    assert availability_of(visitor_client, mixed) == "metadata_only"
    assert availability_of(staff_client, mixed) == "live"
    # A staged release, which staff see pinned, is not yet live.
    staged = instance(public(), hidden_release("staged", staff_user))
    assert availability_of(staff_client, staged) == "metadata_only"


def as_context(row, release):
    """Makes this selected release a context one, as an admin may."""
    BrowserInstanceRelease.objects.filter(instance=row, release=release).update(
        role="context"
    )


def test_a_context_release_counts_for_availability(
    registry, visitor_client, staff_client
):
    shown, hidden = public(), published(INTERNAL)
    row = instance(shown, hidden)
    as_context(row, hidden)
    assert availability_of(visitor_client, row) == "metadata_only"
    assert availability_of(staff_client, row) == "live"


def test_a_context_release_counts_for_external_services(
    registry, visitor_client, staff_client
):
    # Live for both callers, so only the context release decides.
    sendable, kept = published(INTERNAL, PUBLIC_VIEW, EXTERNAL_SERVICES), public()
    row = instance(sendable, kept, allow_external_services=True)
    as_context(row, kept)
    for client in (visitor_client, staff_client):
        body = page(client, row)["instance"]
        assert body["availability"] == "live"
        assert body["external_services_effective"] is False


def test_derived_taxa_read_a_context_release(registry, visitor_client, staff_client):
    main, support = holding(TR, taxa=[9606]), holding(PI, taxa=[7227])
    row = instance(main, support)
    as_context(row, support)
    for client in (visitor_client, staff_client):
        for body in (item_of(client, row), page(client, row)["instance"]):
            assert body["taxa"] == [7227, 9606]
    assert listed(visitor_client, taxon="7227") == [row.slug]


def test_derived_types_read_a_context_release(registry, visitor_client, staff_client):
    main, support = holding(TR, taxa=[9606]), holding(PI, taxa=[7227])
    row = instance(main, support)
    as_context(row, support)
    for client in (visitor_client, staff_client):
        for body in (item_of(client, row), page(client, row)["instance"]):
            assert body["relationship_types"] == [PI, TR]
    assert listed(visitor_client, relationship_type=PI) == [row.slug]


def test_is_synthetic_reads_a_context_release(registry, visitor_client, staff_client):
    main = public()
    support = published(INTERNAL, PUBLIC_VIEW, is_synthetic=True)
    row = instance(main, support)
    as_context(row, support)
    for client in (visitor_client, staff_client):
        assert item_of(client, row)["is_synthetic"] is True
        assert page(client, row)["instance"]["is_synthetic"] is True


def test_an_available_typed_instance_with_no_release_is_metadata_only(
    registry, visitor_client, staff_client
):
    # No write path leaves one (rules.validate_selection), so it is saved past
    # them.
    row = BrowserInstance.objects.create(
        slug="bare",
        title="Bare",
        kind=Kind.TYPED,
        status=Status.AVAILABLE,
        visibility=Visibility.PUBLIC,
    )
    for client in (visitor_client, staff_client):
        assert availability_of(client, row) == "metadata_only"


@pytest.mark.parametrize("status", ["planned", "retired"])
def test_a_stored_planned_or_retired_status_comes_first(
    status, registry, visitor_client, staff_client
):
    rows = [
        instance(public(), status=status),
        instance(kind="external", external_url=URL, status=status),
        instance(kind="portal_ppi", status=status),
        instance(kind="metadata_only", status=status),
    ]
    for row in rows:
        for client in (visitor_client, staff_client):
            assert availability_of(client, row) == status, row.kind


def test_an_available_instance_of_another_kind_shows_its_kind(
    registry, visitor_client, staff_client
):
    for row, expected in (
        (instance(kind="external", external_url=URL), "external"),
        # This portal's /search, which the release gate does not cover.
        (instance(kind="portal_ppi"), "live"),
        (instance(kind="metadata_only"), "metadata_only"),
    ):
        for client in (visitor_client, staff_client):
            assert availability_of(client, row) == expected, row.kind


def test_approvals_make_an_instance_live_on_the_next_request(
    registry, visitor_client, staff_client
):
    release = published()
    row = instance(release)
    assert availability_of(staff_client, row) == "metadata_only"
    approve(release, INTERNAL)
    assert availability_of(staff_client, row) == "live"
    assert availability_of(visitor_client, row) == "metadata_only"
    approve(release, PUBLIC_VIEW)
    assert availability_of(visitor_client, row) == "live"


def test_publishing_a_staged_release_makes_its_instance_live(
    registry, staff_user, visitor_client, staff_client
):
    release = hidden_release("staged", staff_user)
    row = instance(release)
    assert availability_of(staff_client, row) == "metadata_only"
    publish_release(release, published_by=staff_user)
    for client in (visitor_client, staff_client):
        assert availability_of(client, row) == "live"


def _withdraw(release, staff):
    withdraw_release(release, reason="Test only.", withdrawn_by=staff)


def _revoke(scope):
    def change(release, staff):
        approval = UsageApproval.objects.get(
            release=release, scope=scope, revoked_at__isnull=True
        )
        revoke(approval, reason="Test only.", revoked_by=staff)

    return change


def _reopen_diligence(release, staff):
    record = DueDiligenceRecord.objects.get(dataset=release.dataset)
    record.status = DueDiligenceStatus.OPEN
    record._changed_by = staff
    record.save()


@pytest.mark.parametrize(
    "change,visitor,staff",
    [
        (_withdraw, "metadata_only", "metadata_only"),
        (_revoke(INTERNAL), "metadata_only", "metadata_only"),
        (_revoke(PUBLIC_VIEW), "metadata_only", "live"),
        (_reopen_diligence, "metadata_only", "metadata_only"),
    ],
    ids=["withdrawn", "internal revoked", "public_view revoked", "diligence reopened"],
)
def test_a_change_to_a_selected_release_shows_on_the_next_request(
    change, visitor, staff, registry, staff_user, visitor_client, staff_client
):
    release = public()
    row = instance(release)
    for client in (visitor_client, staff_client):
        assert availability_of(client, row) == "live"
    change(release, staff_user)
    assert availability_of(visitor_client, row) == visitor
    assert availability_of(staff_client, row) == staff


def test_an_instance_pinned_to_v1_stays_live_after_v2_supersedes_it(
    registry, staff_user, visitor_client, staff_client
):
    v1 = public()
    pinned = instance(v1)
    v2 = approvable_release(dataset=v1.dataset, supersedes=v1)
    approve(v2, INTERNAL, PUBLIC_VIEW)
    advance(v2, STAGED)
    publish_release(v2, published_by=staff_user)
    v1.refresh_from_db()
    assert v1.status == SUPERSEDED
    current = instance(v2)
    for client in (visitor_client, staff_client):
        assert availability_of(client, pinned) == "live"
        assert availability_of(client, current) == "live"
        [entry] = page(client, pinned)["releases"]
        assert (entry["id"], entry["superseded"]) == (ref(v1), True)
    # Withdrawing v2 never brings v1 back: v1 stays as it was.
    _withdraw(v2, staff_user)
    for client in (visitor_client, staff_client):
        assert availability_of(client, pinned) == "live"
        assert availability_of(client, current) == "metadata_only"


# ── External services ───────────────────────────────────────────────────────


def test_external_services_are_effective_only_where_every_release_allows_them(
    registry, visitor_client, staff_client
):
    sendable = published(INTERNAL, PUBLIC_VIEW, EXTERNAL_SERVICES)
    kept = public()
    staff_only = published(INTERNAL, EXTERNAL_SERVICES)
    for row, visitor, staff in (
        (instance(sendable, allow_external_services=True), True, True),
        # The stored flag is off.
        (instance(sendable), False, False),
        # One release does not allow them.
        (instance(sendable, kept, allow_external_services=True), False, False),
        (
            instance(sendable, status="planned", allow_external_services=True),
            False,
            False,
        ),
        (
            instance(sendable, status="retired", allow_external_services=True),
            False,
            False,
        ),
        # metadata_only for a visitor, live for staff.
        (instance(staff_only, allow_external_services=True), False, True),
        # Live, and selecting nothing: no approval speaks for it.
        (instance(kind="portal_ppi", allow_external_services=True), False, False),
    ):
        for client, expected in ((visitor_client, visitor), (staff_client, staff)):
            body = page(client, row)["instance"]
            assert body["allow_external_services"] is row.allow_external_services
            assert body["external_services_effective"] is expected, row.slug


# ── What a request costs ────────────────────────────────────────────────────


def counted_instances(instances, releases) -> list:
    """`instances` public typed instances, each selecting the same `releases`
    public releases, the first of which holds a regulation and declares the
    human taxon; each states no taxa and no relationship types, so both are
    derived, and names a test-only assay and one paper."""
    selected = [holding(TR, taxa=[9606]), *(public() for _ in range(releases - 1))]
    assay, paper = TermFactory(), PublicationFactory(year=2020)
    return [
        instance(*selected, assays=[assay], publications=[paper])
        for _ in range(instances)
    ]


@pytest.mark.parametrize(
    "instances,releases", [(1, 1), (25, 1), (1, 25), (25, 25)], ids=str
)
def test_the_gallery_costs_its_statements_whatever_it_holds(
    instances,
    releases,
    registry,
    staff_user,
    user_auth_client,
    visitor_client,
    django_assert_num_queries,
):
    counted_instances(instances, releases)
    callers = (
        (visitor_client, 0),
        (user_auth_client, TOKEN),
        (bearer(staff_user), TOKEN),
    )
    for client, token in callers:
        with django_assert_num_queries(GALLERY + token):
            assert len(gallery(client)["instances"]) == instances
        for vocabularies in ({"relationship_type": TR}, {"taxon": "9606"}):
            with django_assert_num_queries(GALLERY + VOCABULARY + token):
                assert len(gallery(client, **vocabularies)["instances"]) == instances
        with django_assert_num_queries(GALLERY + 2 * VOCABULARY + token):
            listing = gallery(client, relationship_type=TR, taxon="9606")
            assert len(listing["instances"]) == instances
        # Listing nothing, it reads no label.
        with django_assert_num_queries(GALLERY - LABELS + token):
            assert gallery(client, q="no such words")["instances"] == []
        with django_assert_num_queries(GALLERY - LABELS + 2 * VOCABULARY + token):
            assert (
                gallery(client, relationship_type=PI, taxon="7227")["instances"] == []
            )


def test_instances_a_visitor_may_not_list_change_no_visitor_count(
    registry, visitor_client, user_auth_client
):
    counted_instances(1, 1)
    urls = (
        BROWSERS,
        browsers_url(relationship_type=TR, taxon="9606"),
        browsers_url(q="no such words"),
    )

    def answers():
        return [
            answered(client, url)
            for client in (visitor_client, user_auth_client)
            for url in urls
        ]

    before = answers()
    assert [answer[3] for answer in before] == [
        GALLERY,
        GALLERY + 2 * VOCABULARY,
        GALLERY - LABELS,
        GALLERY + TOKEN,
        GALLERY + 2 * VOCABULARY + TOKEN,
        GALLERY - LABELS + TOKEN,
    ]
    for visibility in ("unlisted", "staff"):
        for _ in range(25):
            instance(public(), visibility=visibility, assays=[TermFactory()])
    assert answers() == before


def test_an_empty_gallery_reads_its_instances_and_the_empty_pin(
    switch_on, visitor_client, django_assert_num_queries
):
    with django_assert_num_queries(2):
        assert gallery(visitor_client)["instances"] == []


@pytest.mark.parametrize("releases", [1, 25], ids=str)
@pytest.mark.parametrize("flag", [False, True], ids=["flag off", "flag on"])
def test_the_page_costs_its_statements_whatever_it_selects(
    releases,
    flag,
    registry,
    staff_user,
    user_auth_client,
    visitor_client,
    django_assert_num_queries,
):
    scopes = (INTERNAL, PUBLIC_VIEW, EXTERNAL_SERVICES)
    selected = [
        holding(TR, taxa=[9606], scopes=scopes),
        *(published(*scopes) for _ in range(releases - 1)),
    ]
    row = instance(
        *selected,
        assays=[TermFactory()],
        publications=[PublicationFactory()],
        allow_external_services=flag,
    )
    callers = (
        (visitor_client, 0),
        (user_auth_client, TOKEN),
        (bearer(staff_user), TOKEN),
    )
    for client, token in callers:
        with django_assert_num_queries(PAGE + (EXTERNAL if flag else 0) + token):
            body = page(client, row)
        assert len(body["releases"]) == releases
        assert body["instance"]["external_services_effective"] is flag


def test_a_404_costs_the_lookup_alone(registry, visitor_client, user_auth_client):
    hidden = instance(public(), visibility="staff")
    for client, token in ((visitor_client, 0), (user_auth_client, TOKEN)):
        assert answered(client, browser_url("Bad_Slug"))[3] == token
        assert answered(client, browser_url("a" * 51))[3] == token
        assert answered(client, browser_url("no-such-instance"))[3] == 1 + token
        assert answered(client, browser_url("a" * 50))[3] == 1 + token
        assert answered(client, browser_url(hidden.slug))[3] == 1 + token


def test_a_selection_the_caller_may_not_see_costs_what_none_does(
    registry, visitor_client, staff_client
):
    clients = (visitor_client, staff_client)
    empty = instance(status="planned")
    alone = [answered(client, BROWSERS)[3] for client in clients]
    assert alone == [GALLERY - DERIVED - LABELS] * len(clients)
    # Unapproved, so hidden from staff too. The visible set is read whether
    # it pins the hidden release or EMPTY_PIN, and neither derives a facet.
    hidden = instance(published(), status="planned")
    assert [answered(client, BROWSERS)[3] for client in clients] == alone
    for client in clients:
        assert (
            answered(client, browser_url(hidden.slug))[3]
            == answered(client, browser_url(empty.slug))[3]
            == PAGE - DERIVED - LABELS - ENTRIES
        )


def test_an_instance_stating_its_facets_derives_none(
    registry, visitor_client, django_assert_num_queries
):
    # Its release declares a taxon and holds a type, so only the stated sets
    # keep those reads from being sent.
    row = instance(
        holding(TR, taxa=[9606]),
        taxa=[taxon_of(9606)],
        relationship_types=[RelationshipType.objects.get(slug=TR)],
    )
    with django_assert_num_queries(GALLERY - DERIVED - LABELS):
        assert len(gallery(visitor_client)["instances"]) == 1
    with django_assert_num_queries(PAGE - DERIVED - LABELS):
        assert len(page(visitor_client, row)["releases"]) == 1


# ── browser= on the routes that read data ───────────────────────────────────


def regulation(status=PUBLISHED):
    """A release with internal and public_view that states one regulation
    between two genes it created, moved to this status."""
    ev = evidence_release(records=0)
    number = ev.release.pk
    regulator = entity_of(label=f"REGULATOR-{number}", created_by_release=ev.release)
    target = entity_of(label=f"TARGET-{number}", created_by_release=ev.release)
    relationship = link(TR, regulator, target)
    state(ev, relationship)
    approve(ev.release, PUBLIC_VIEW)
    advance(ev.release, status)
    return SimpleNamespace(
        release=ev.release,
        regulator=regulator,
        target=target,
        relationship=relationship,
    )


def readers(world) -> dict:
    """{route: a request about the world's rows}: the regulator's label, its
    page, the network around it, and the regulation's page and evidence."""
    return {
        RESOLVE: resolve_url(world.regulator.label),
        ENTITY: entity_url(world.regulator),
        NETWORK: network_url(world.regulator),
        RELATIONSHIP: relationship_url(world.relationship),
        EVIDENCE: evidence_url(world.relationship),
    }


def reader_url(path, query) -> str:
    """The route's URL with this query string, and what the route needs to
    answer at all: a well-formed ref for each path parameter, and the query
    REQUIRED names."""
    return f"{reachable(path)}?{'&'.join(filter(None, (REQUIRED.get(path), query)))}"


def under(url, **parameters) -> str:
    """The URL with these parameters appended."""
    return f"{url}{'&' if '?' in url else '?'}{urlencode(parameters)}"


def body_at(client, url) -> dict:
    """The body of a 200."""
    response = client.get(url)
    assert response.status_code == 200, (url, response.content)
    return response.json()


def read_at(client, url) -> tuple:
    """Its status, its body but for the query, which echoes the caller's own
    words, and the statements it took."""
    with CaptureQueriesContext(connection) as queries:
        response = client.get(url)
    body = response.json()
    body.pop("query", None)
    return response.status_code, body, len(queries)


def browsed(client, url, slug) -> tuple:
    """What tells the URL's answer under browser=slug from another's: its
    status, its body with the slug, which the query echoes, written SLUG,
    the headers of a 404, whose body names no slug, and the statements it
    took."""
    with CaptureQueriesContext(connection) as queries:
        response = client.get(under(url, browser=slug))
    body = response.content.decode().replace(slug, "SLUG")
    headers = sorted(response.headers.items()) if response.status_code == 404 else None
    return response.status_code, body, headers, len(queries)


def test_browser_reads_the_releases_an_instance_selects_on_the_demonstration(demo):
    tf1, g1 = demo.entity("SYN-TF1"), demo.entity("SYN-G1")
    forward = find_relationship(canonical_relationship(TR, tf1, g1))
    v1, v2 = ref(demo.v1), ref(demo.v2)
    urls = (
        relationship_url(forward),
        evidence_url(forward),
        network_url(tf1, direction="out"),
    )
    # v1 alone: its effect, flagged superseded; v2 alone: its correction;
    # both: mixed, two assertions of one observation.
    for selected, effect, rows in (
        ((demo.v1,), "activating", [(v1, "activating")]),
        ((demo.v2,), "inhibiting", [(v2, "inhibiting")]),
        ((demo.v1, demo.v2), "mixed", [(v1, "activating"), (v2, "inhibiting")]),
    ):
        selecting = instance(*selected)
        refs = [ref(release) for release in selected]
        flagged = [(ref(release), release == demo.v1) for release in selected]
        relationship, evidence, network = (
            body_at(demo.staff, under(url, browser=selecting.slug)) for url in urls
        )
        edge = relationship["edge"]
        assert (edge["effect"], edge["releases"]) == (effect, refs)
        if effect == "mixed":
            assert (edge["assertion_count"], edge["evidence_count"]) == (2, 1)
        assert [(item["release"], item["effect"]) for item in evidence["evidence"]] == (
            rows
        )
        [edge] = [
            item
            for item in network["edges"]
            if item["id"] == f"relationship:{forward.pk}"
        ]
        assert (edge["effect"], edge["releases"]) == (effect, refs)
        for body in (relationship, evidence, network):
            assert [
                (entry["id"], entry["superseded"]) for entry in body["releases"]
            ] == flagged
            assert (body["query"]["browser"], body["query"]["releases"]) == (
                selecting.slug,
                None,
            )
        # Each reads what releases= naming the same releases reads.
        for url in urls:
            assert read_at(demo.staff, under(url, browser=selecting.slug))[:2] == (
                read_at(demo.staff, under(url, **pin(*selected)))[:2]
            ), url


def test_browser_reads_nothing_outside_the_releases_an_instance_selects(demo):
    outside = regulation()
    query = {"browser": instance(demo.v2).slug}
    tf1 = demo.entity("SYN-TF1")
    # What v2 names reads as it does with no selection.
    resolved = body_at(demo.staff, resolve_url("SYN-TF1", **query))
    assert resolved["results"][0]["status"] == "resolved"
    assert ids(resolved["releases"]) == [ref(demo.v2)]
    assert ids(body_at(demo.staff, entity_url(tf1, **query))["releases"]) == [
        ref(demo.v2)
    ]
    # What only another release names, staff read with no selection, and not
    # at all through the instance: its evidence is not counted.
    label = outside.regulator.label
    for parameters, status in (({}, "candidate"), (query, "unmatched")):
        body = body_at(demo.staff, resolve_url(label, **parameters))
        assert body["results"][0]["status"] == status
    for url in (
        entity_url(outside.regulator),
        relationship_url(outside.relationship),
        evidence_url(outside.relationship),
    ):
        assert demo.staff.get(url).status_code == 200
        assert demo.staff.get(under(url, **query)).json() == NOT_FOUND, url
    network = body_at(demo.staff, network_url(outside.regulator, **query))
    assert (network["nodes"], network["edges"]) == ([], [])


def test_a_selection_does_not_scope_the_labels_a_caller_may_read(
    registry, visitor_client
):
    written = regulation()
    # The instance's release only names the regulator: its label stays that
    # of the release that wrote it, which the visitor may read.
    ev = evidence_release(records=0)
    state(ev, link(TR, written.regulator, entity_of(created_by_release=ev.release)))
    approve(ev.release, PUBLIC_VIEW)
    advance(ev.release, PUBLISHED)
    selecting = instance(ev.release)
    url = entity_url(written.regulator, browser=selecting.slug)
    assert body_at(visitor_client, url)["entity"]["label"] == written.regulator.label


def test_the_demonstrations_papers_carry_a_year(demo):
    # What graph_load_demo records, for staff only.
    slugs = [
        row["slug"] for row in body_at(demo.staff, browsers_url(year=2026))["instances"]
    ]
    assert slugs == ["synthetic-regulation"]
    assert body_at(demo.staff, browsers_url(year=2025))["instances"] == []


def test_browser_pins_each_selected_release_whatever_its_role_or_the_status(
    registry, visitor_client
):
    first, second = regulation(), regulation()
    # Out of the gallery and retired, but reachable by its slug.
    selecting = instance(
        first.release, second.release, status="retired", visibility="unlisted"
    )
    BrowserInstanceRelease.objects.filter(
        instance=selecting, release=second.release
    ).update(role="context")
    terms = (first.regulator.label, second.regulator.label)
    body = body_at(visitor_client, resolve_url(*terms, browser=selecting.slug))
    assert [result["status"] for result in body["results"]] == [
        "candidate",
        "candidate",
    ]
    assert ids(body["releases"]) == [ref(first.release), ref(second.release)]


@pytest.mark.parametrize("caller", ["visitor", "staff"])
def test_a_slug_pinning_nothing_the_caller_may_see_reads_as_an_unknown_one(
    caller, registry, visitor_client, staff_client
):
    client = staff_client if caller == "staff" else visitor_client
    world = regulation()
    other_kind = instance(kind="metadata_only")
    # A bare save past rules.validate_selection, which no write path makes:
    # only a typed instance's selection is read.
    BrowserInstanceRelease.objects.create(instance=other_kind, release=world.release)
    slugs = [
        other_kind.slug,
        instance(status="planned").slug,
        # Published and never approved, so hidden from staff too.
        instance(published()).slug,
    ]
    if caller == "visitor":
        # Its release is public: being out of the caller's reach alone hides
        # it.
        slugs.append(instance(world.release, visibility="staff").slug)
    statuses = {}
    for path, url in readers(world).items():
        unknown = browsed(client, url, NO_INSTANCE)
        statuses[path] = unknown[0]
        for slug in slugs:
            assert browsed(client, url, slug) == unknown, (path, slug)
    # Each reads nothing: resolve matches nothing, the network is empty and
    # the pages are the 404.
    assert statuses == {
        RESOLVE: 200,
        ENTITY: 404,
        NETWORK: 200,
        RELATIONSHIP: 404,
        EVIDENCE: 404,
    }
    nothing = {"browser": NO_INSTANCE}
    body = body_at(client, resolve_url(world.regulator.label, **nothing))
    assert body["results"][0]["status"] == "unmatched"
    network = body_at(client, network_url(world.regulator, **nothing))
    assert (network["nodes"], network["edges"]) == ([], [])


@pytest.mark.parametrize("caller", ["visitor", "staff"])
def test_browser_costs_one_statement_more_than_releases_naming_its_refs(
    caller, registry, visitor_client, staff_client
):
    client = staff_client if caller == "staff" else visitor_client
    world = regulation()
    absent = {path: read_at(client, url) for path, url in readers(world).items()}
    selecting = instance(world.release)
    # A ref no release has.
    nowhere = {"releases": f"release:{world.release.pk + 1_000_000}"}
    for path, url in readers(world).items():
        # An instance changes nothing for a request that names none.
        assert read_at(client, url) == absent[path], path
        by_slug = read_at(client, under(url, browser=selecting.slug))
        by_refs = read_at(client, under(url, **pin(world.release)))
        assert by_slug[:2] == by_refs[:2], path
        assert by_slug[2] == by_refs[2] + 1, path
        # Pinning nothing, it reads what a ref naming no release reads.
        unknown = read_at(client, under(url, browser=NO_INSTANCE))
        named = read_at(client, under(url, **nowhere))
        assert unknown[:2] == named[:2], path
        assert unknown[2] == named[2] + 1, path


def test_the_query_echoes_the_slug_as_given(registry, visitor_client):
    world = regulation()
    selecting = instance(world.release)
    for path, url in readers(world).items():
        query = body_at(visitor_client, under(url, browser=selecting.slug))["query"]
        assert (query["browser"], query["releases"]) == (selecting.slug, None), path
    # A slug that names nothing is echoed as it was given.
    for url in (resolve_url("x"), network_url(world.regulator)):
        query = body_at(visitor_client, under(url, browser=NO_INSTANCE))["query"]
        assert (query["browser"], query["releases"]) == (NO_INSTANCE, None)


def test_a_merged_entitys_redirect_keeps_browser(registry, visitor_client):
    release = public()
    survivor = entity_of(created_by_release=release)
    merged = merged_into(survivor, release)
    selecting = instance(release)
    response = visitor_client.get(entity_url(merged, browser=selecting.slug))
    assert response.status_code == 301
    assert response["Location"] == entity_url(survivor, browser=selecting.slug)
    body = body_at(visitor_client, response["Location"])
    assert body["query"]["browser"] == selecting.slug


@pytest.mark.parametrize("path", READERS)
@pytest.mark.parametrize(
    "query,error,detail",
    [
        *(
            (f"browser={sent}", problem("browser", given), SLUG_SENTENCE)
            for sent, given in (
                ("Synthetic", "Synthetic"),
                ("a_b", "a_b"),
                ("a--b", "a--b"),
                ("-a", "-a"),
                ("a-", "a-"),
                ("a%20b", "a b"),
                ("", ""),
                ("a%00b", "a\x00b"),
                ("a%1Fb", "a\x1fb"),
                ("%FF", "\ufffd"),
                ("a" * 51, "a" * 51),
            )
        ),
        ("browser=a&browser=b", problem("browser"), "browser is given once."),
        (
            "browser=an-instance&releases=release:1",
            problem("browser", "an-instance"),
            "browser and releases are not given together.",
        ),
    ],
)
def test_a_bad_browser_is_a_400_that_reads_nothing(
    path, query, error, detail, switch_on, visitor_client, django_assert_num_queries
):
    with django_assert_num_queries(0):
        response = visitor_client.get(reader_url(path, query))
    assert response.status_code == 400, response.content
    assert response.json() == {"detail": detail, "errors": [error]}


@pytest.mark.parametrize("path", READERS)
def test_browser_is_read_after_releases(path, switch_on, visitor_client):
    # The query string runs backwards, so the order shown is the route's.
    response = visitor_client.get(reader_url(path, "browser=Bad&releases=bad"))
    assert response.status_code == 400
    assert response.json() == {
        "detail": f"releases: a release ref looks like release:3. {SLUG_SENTENCE}",
        "errors": [problem("releases", "bad"), problem("browser", "Bad")],
    }


def test_staff_preview_a_staged_selected_release_through_browser(
    registry, visitor_client, staff_client
):
    world = regulation(status=STAGED)
    selecting = instance(world.release)
    # Not yet live, for staff too ...
    assert page(staff_client, selecting)["instance"]["availability"] == (
        "metadata_only"
    )
    # ... while browser= shows staff its data, as a pin does.
    url = relationship_url(world.relationship, browser=selecting.slug)
    body = body_at(staff_client, url)
    assert body["edge"]["releases"] == [ref(world.release)]
    [entry] = body["releases"]
    assert (entry["id"], entry["status"], entry["superseded"]) == (
        ref(world.release),
        "staged",
        False,
    )
    # Nothing staged is in the default set, and a visitor sees none of it.
    assert staff_client.get(relationship_url(world.relationship)).json() == NOT_FOUND
    assert visitor_client.get(url).json() == NOT_FOUND

"""GET /api/graph/v1/entities/{entity_id}: one entity, as the caller may see it
(architecture §7.3).

Its path and parameters; the page on the demonstration, for staff, as the
selection changes; identifiers and their links; who sees what, on factory
rows; merged and retired rows; entity text; and what a page costs. The walk of
every route over every hidden release is in test_api_visibility, and what the
functions behind the page promise is in test_entity_visibility.

Non-primary identifiers are factory rows, as in test_resolution: nothing
imports them before RF1a.
"""

import json

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework_simplejwt.tokens import RefreshToken

from graph.api import views
from graph.api.limits import API_VERSION, MAX_RELEASES
from graph.api.serializers import EdgeSerializer
from graph.demo import DATASET, DATASET_SLUG, DEMO_LICENCE, LICENCE_URL
from graph.ingest.registry import get_adapter
from graph.models import (
    ApprovalScope,
    CasePolicy,
    EntityStatus,
    IdentifierNamespace,
    IdentifierRole,
    Relationship,
    VersionPolicy,
)
from graph.releases import withdraw_release
from graph.tests.api_helpers import (
    BASE,
    HIDDEN_KINDS,
    entity_url,
    hidden_evidence,
    hidden_release,
    hide,
    ids,
    link,
    pin,
    public,
    public_release,
    published,
    state,
)
from graph.tests.factories import (
    EntityFactory,
    EntityIdentifierFactory,
    EvidenceAssertionFactory,
    IdentifierNamespaceFactory,
    RelationshipFactory,
    approvable_release,
    approve,
    entity_of,
    evidence_release,
    mapped_record,
)
from graph.tests.test_provenance import PUBLISHED, STAGED, SUPERSEDED, advance

INTERNAL = ApprovalScope.INTERNAL
PUBLIC_VIEW = ApprovalScope.PUBLIC_VIEW
PRIMARY = IdentifierRole.PRIMARY
ENTITIES = f"{BASE}entities/"
NOT_FOUND = {"detail": "Not found."}


def page(client, entity, **parameters) -> dict:
    """The body of a 200 for this entity."""
    response = client.get(entity_url(entity, **parameters))
    assert response.status_code == 200, response.content
    return response.json()


def problem(parameter, value=None, allowed=None, limit=None) -> dict:
    return {"parameter": parameter, "value": value, "allowed": allowed, "limit": limit}


def refs(*releases) -> list:
    return [f"release:{release.pk}" for release in releases]


def answered(client, url) -> tuple:
    """What tells one response from another: its status, its body and the
    queries it took."""
    with CaptureQueriesContext(connection) as queries:
        response = client.get(url)
    return response.status_code, response.content, len(queries)


def nowhere(entity) -> str:
    """The page of an id no row has."""
    return f"{ENTITIES}entity:{entity.pk + 1_000}"


def identified(value, **fields):
    """A gene whose primary identifier is this HGNC ID."""
    entity = entity_of(**fields)
    EntityIdentifierFactory(
        entity=entity,
        namespace=IdentifierNamespace.objects.get(slug="hgnc"),
        value=value,
        role=PRIMARY,
    )
    return entity


def shown_evidence():
    """A draft that will be public once advanced: a test states on it first."""
    ev = evidence_release(records=0)
    approve(ev.release, PUBLIC_VIEW)
    return ev


# ── The path and the parameters ─────────────────────────────────────────────


def test_while_off_it_is_404(db, settings, visitor_client, django_assert_num_queries):
    settings.TYPED_GRAPH_ENABLED = False
    with django_assert_num_queries(0):
        response = visitor_client.get(f"{ENTITIES}entity:1")
    assert response.status_code == 404
    assert response.json() == NOT_FOUND


@pytest.mark.parametrize(
    "value,given",
    [
        ("12", "12"),
        ("release:3", "release:3"),
        ("relationship:3", "relationship:3"),
        ("entity:x", "entity:x"),
        ("entity:012", "entity:012"),
        ("entity:00", "entity:00"),
        ("entity:", "entity:"),
        (":1", ":1"),
        ("ENTITY:1", "ENTITY:1"),
        ("entity:-1", "entity:-1"),
        ("entity:+1", "entity:+1"),
        ("entity:1.0", "entity:1.0"),
        ("entity:1%20", "entity:1 "),
        ("entity:1%0A", "entity:1\n"),
        ("entity:1%00", "entity:1\x00"),
        ("entity:%FF", "entity:%FF"),
        ("entity:%D9%A1", "entity:\u0661"),
        (f"entity:{'1' * 20}", f"entity:{'1' * 20}"),
    ],
)
def test_a_malformed_ref_is_400(value, given, switch_on, visitor_client):
    response = visitor_client.get(f"{ENTITIES}{value}")
    assert response.status_code == 400
    assert response.json() == {
        "detail": "entity_id looks like entity:12.",
        "errors": [problem("entity_id", given)],
    }


def test_a_percent_encoded_colon_names_the_same_entity(registry, visitor_client):
    entity = EntityFactory(created_by_release=public())
    body = visitor_client.get(f"{ENTITIES}entity%3A{entity.pk}").json()
    assert body["query"]["entity_id"] == f"entity:{entity.pk}"
    assert body["entity"]["id"] == f"entity:{entity.pk}"


def test_a_ref_beyond_64_bits_asks_for_no_row(switch_on, visitor_client):
    with CaptureQueriesContext(connection) as queries:
        response = visitor_client.get(f"{ENTITIES}entity:{'9' * 19}")
    assert response.status_code == 404
    assert response.json() == NOT_FOUND
    assert [query for query in queries if '"graph_entity"' in query["sql"]] == []


@pytest.mark.parametrize("pk", (0, 2**63 - 1))
def test_a_well_formed_ref_that_names_nothing_is_a_404(pk, switch_on, visitor_client):
    response = visitor_client.get(f"{ENTITIES}entity:{pk}")
    assert response.status_code == 404
    assert response.json() == NOT_FOUND


def test_a_404_follows_the_negotiation_while_on(switch_on, visitor_client):
    response = visitor_client.get(f"{ENTITIES}entity:1?format=api")
    assert response.status_code == 404
    assert response["Content-Type"].startswith("text/html")


def test_every_problem_is_listed_in_order(switch_on, visitor_client):
    response = visitor_client.get(f"{ENTITIES}12?browser=x&releases=bad")
    assert response.status_code == 400
    assert response.json() == {
        "detail": (
            "Unknown parameter 'browser'. Allowed: releases. "
            "entity_id looks like entity:12. "
            "releases: a release ref looks like release:3."
        ),
        "errors": [
            problem("browser", allowed=["releases"]),
            problem("entity_id", "12"),
            problem("releases", "bad"),
        ],
    }


@pytest.mark.parametrize("query", ["releases=", "releases=,", "releases=3"])
def test_a_bad_selection_is_400_before_anything_is_read(
    query, switch_on, visitor_client, django_assert_num_queries
):
    with django_assert_num_queries(0):
        response = visitor_client.get(f"{ENTITIES}entity:1?{query}")
    assert response.status_code == 400
    assert [entry["parameter"] for entry in response.json()["errors"]] == ["releases"]


def test_an_invisible_entity_reads_like_a_nonexistent_one(
    registry, staff_client, visitor_client
):
    creator = published()
    hidden = EntityFactory(created_by_release=creator)
    for client in (visitor_client, staff_client):
        status, content, queries = answered(client, entity_url(hidden))
        assert status == 404
        assert json.loads(content) == NOT_FOUND
        assert (status, content, queries) == answered(client, nowhere(hidden))
        assert client.get(entity_url(hidden, **pin(creator))).json() == NOT_FOUND


def test_options_says_nothing_of_a_row(registry, visitor_client):
    shown = EntityFactory(created_by_release=public())
    hidden = EntityFactory(created_by_release=published())
    answers = [
        visitor_client.options(url)
        for url in (entity_url(shown), entity_url(hidden), nowhere(hidden))
    ]
    assert [answer.status_code for answer in answers] == [200, 200, 200]
    assert len({answer.content for answer in answers}) == 1


# ── The demonstration ───────────────────────────────────────────────────────


def test_the_page_of_syn_tf1(demo, visitor_client):
    tf1 = demo.entity("SYN-TF1")
    p1 = demo.entity("SYN-P1")
    encodes = Relationship.objects.get(
        relationship_type="encodes", subject=tf1, object=p1
    )
    assert visitor_client.get(entity_url(tf1)).status_code == 404

    expected = {
        "api_version": API_VERSION,
        "query": {"entity_id": f"entity:{tf1.pk}", "releases": None},
        "entity": {
            "id": f"entity:{tf1.pk}",
            "type": "gene",
            "label": "SYN-TF1",
            "taxon": 32630,
            "status": "provisional",
            "primary_identifier": {"namespace": "synthetic_gene", "value": "SYN-TF1"},
        },
        "description": "",
        # v1 created it, and v1 is outside the default set.
        "created_by_release": None,
        "identifiers": [
            {
                "namespace": "synthetic_gene",
                "value": "SYN-TF1",
                "version": None,
                "role": "primary",
                "url": None,
            }
        ],
        "reference_relationships": [
            {
                "id": f"relationship:{encodes.pk}",
                "type": "encodes",
                "family": "reference",
                "orientation": "role_ordered",
                "source": f"entity:{tf1.pk}",
                "target": f"entity:{p1.pk}",
                "releases": refs(demo.v2),
                "is_synthetic": True,
                "qualifiers": {},
                "shown": True,
                "effect": "not_applicable",
                "effect_counts": {
                    "activating": 0,
                    "inhibiting": 0,
                    "unknown": 0,
                    "not_stated": 1,
                },
                "genetic_sign": "not_applicable",
                "sign_counts": {"negative": 0, "positive": 0, "not_stated": 1},
                "outcome_counts": {
                    "supported": 1,
                    "tested_absent": 0,
                    "inconclusive": 0,
                    "not_significant": 0,
                },
                "independent_outcome_counts": {
                    "supported": 1,
                    "tested_absent": 0,
                    "inconclusive": 0,
                    "not_significant": 0,
                },
                "assertion_count": 1,
                "evidence_count": 1,
                "distinct": {"publications": 0, "releases": 1, "datasets": 1},
                "by_basis": {"unknown": 1},
                "level": None,
                "level_counts": {},
                "level_assigned": None,
                "tested_levels": {},
                "outside_filters": 0,
                "outside_outcome_counts": {
                    "supported": 0,
                    "tested_absent": 0,
                    "inconclusive": 0,
                    "not_significant": 0,
                },
            }
        ],
        "nodes": [
            {
                "id": f"entity:{tf1.pk}",
                "type": "gene",
                "label": "SYN-TF1",
                "taxon": 32630,
                "status": "provisional",
                "primary_identifier": {
                    "namespace": "synthetic_gene",
                    "value": "SYN-TF1",
                },
            },
            {
                "id": f"entity:{p1.pk}",
                "type": "protein",
                "label": "SYN-P1",
                "taxon": 32630,
                "status": "provisional",
                "primary_identifier": {
                    "namespace": "synthetic_protein",
                    "value": "SYN-P1",
                },
            },
        ],
        # The tested-absent-only edge SYN-TF1 to SYN-G2 is not counted.
        "relationship_counts": {"encodes": 1, "transcriptional_regulation": 3},
        "releases": [
            {
                "id": f"release:{demo.v2.pk}",
                "dataset": {"slug": DATASET_SLUG, "title": DATASET["title"]},
                "version": "v2",
                "status": "published",
                "superseded": False,
                "is_synthetic": True,
                "licence": {"name": "MIT License", "spdx_id": None, "url": LICENCE_URL},
                "attribution": DEMO_LICENCE["attribution"],
                "changes_note": get_adapter(demo.v2.adapter).spec.changes_note,
                "scopes": ["internal"],
            }
        ],
    }
    body = page(demo.staff, tf1)
    assert body == expected
    # The key order of the page's example, at every level.
    assert json.dumps(body) == json.dumps(expected)


def test_reference_relationships_keep_their_stored_direction(demo):
    p1 = demo.entity("SYN-P1")
    isoform = demo.entity("SYN-P1-1")
    tf1 = demo.entity("SYN-TF1")
    v2 = refs(demo.v2)

    entry = page(demo.staff, p1)
    assert entry["created_by_release"] is None
    assert {
        (edge["type"], edge["source"], edge["target"], tuple(edge["releases"]))
        for edge in entry["reference_relationships"]
    } == {
        ("encodes", f"entity:{tf1.pk}", f"entity:{p1.pk}", tuple(v2)),
        ("isoform_of", f"entity:{isoform.pk}", f"entity:{p1.pk}", tuple(v2)),
    }
    assert ids(entry["nodes"]) == [
        f"entity:{pk}" for pk in sorted((p1.pk, isoform.pk, tf1.pk))
    ]
    assert entry["relationship_counts"] == {
        "encodes": 1,
        "isoform_of": 1,
        "physical_interaction": 1,
    }

    body = page(demo.staff, isoform)
    [edge] = body["reference_relationships"]
    assert (edge["type"], edge["source"], edge["target"]) == (
        "isoform_of",
        f"entity:{isoform.pk}",
        f"entity:{p1.pk}",
    )
    assert body["entity"]["type"] == "protein_isoform"
    assert body["relationship_counts"] == {"isoform_of": 1}


def test_a_selection_changes_what_the_page_names(demo):
    tf1 = demo.entity("SYN-TF1")
    default = page(demo.staff, tf1)

    only_v1 = page(demo.staff, tf1, **pin(demo.v1))
    assert only_v1["query"]["releases"] == refs(demo.v1)
    assert only_v1["created_by_release"] == f"release:{demo.v1.pk}"
    [entry] = only_v1["releases"]
    assert entry["id"] == f"release:{demo.v1.pk}"
    assert entry["status"] == "superseded"
    assert entry["superseded"] is True
    assert entry["scopes"] == ["internal"]
    assert only_v1["relationship_counts"] == default["relationship_counts"]
    [edge] = only_v1["reference_relationships"]
    assert edge["releases"] == refs(demo.v1)

    both = page(demo.staff, tf1, **pin(demo.v1, demo.v2))
    assert both["created_by_release"] == f"release:{demo.v1.pk}"
    assert ids(both["releases"]) == refs(demo.v1, demo.v2)
    assert [entry["superseded"] for entry in both["releases"]] == [True, False]
    [edge] = both["reference_relationships"]
    assert edge["releases"] == refs(demo.v1, demo.v2)


def test_after_v1_is_withdrawn_a_selection_of_it_shows_nothing(demo):
    tf1 = demo.entity("SYN-TF1")
    withdraw_release(demo.v1, reason="Test only.", withdrawn_by=demo.alice)
    response = demo.staff.get(entity_url(tf1, **pin(demo.v1)))
    assert response.status_code == 404
    assert response.json() == NOT_FOUND

    default = page(demo.staff, tf1)
    assert default["created_by_release"] is None
    assert ids(default["releases"]) == refs(demo.v2)

    both = page(demo.staff, tf1, **pin(demo.v1, demo.v2))
    assert both["query"]["releases"] == refs(demo.v1, demo.v2)
    assert ids(both["releases"]) == refs(demo.v2)
    assert both["created_by_release"] is None


# ── Identifiers ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "template,value,url",
    [
        (
            "https://example.org/x/{lookup}",
            "a/b:c d",
            "https://example.org/x/a%2Fb:c%20d",
        ),
        ("http://example.org/{lookup}", "a", "http://example.org/a"),
        ("HTTPS://EXAMPLE.ORG/{lookup}", "a", "HTTPS://EXAMPLE.ORG/a"),
        # What the template holds, whatever fills it, is what counts.
        ("javascript:alert('{lookup}')", "a", None),
        ("{lookup}", "javascript:alert(1)", None),
        ("{lookup}", "https://example.org/", None),
        ("ftp://example.org/{lookup}", "a", None),
        ("https:///{lookup}", "a", None),
        ("https://[/{lookup}", "a", None),
        ("", "a", None),
    ],
)
def test_a_url_is_http_with_a_host_or_null(
    template, value, url, registry, visitor_client
):
    namespace = IdentifierNamespaceFactory(url_template=template, pattern=".+")
    entity = EntityFactory(created_by_release=public())
    EntityIdentifierFactory(entity=entity, namespace=namespace, value=value)
    assert page(visitor_client, entity)["identifiers"] == [
        {
            "namespace": namespace.slug,
            "value": value,
            "version": None,
            "role": "alias",
            "url": url,
        }
    ]


def test_a_url_keeps_the_colon_of_an_hgnc_id(registry, visitor_client):
    entity = identified("HGNC:5", created_by_release=public())
    [entry] = page(visitor_client, entity)["identifiers"]
    assert entry["url"] == (
        "https://www.genenames.org/data/gene-symbol-report/#!/hgnc_id/HGNC:5"
    )


def test_a_url_is_built_from_the_lookup(registry, visitor_client):
    namespace = IdentifierNamespaceFactory(
        url_template="https://example.org/x/{lookup}",
        case_policy=CasePolicy.UPPER,
        version_policy=VersionPolicy.SPLIT,
        pattern="ENSX[0-9]{4}",
    )
    entity = EntityFactory(created_by_release=public())
    EntityIdentifierFactory(entity=entity, namespace=namespace, value="ensx0001.4")
    [entry] = page(visitor_client, entity)["identifiers"]
    assert entry["value"] == "ensx0001.4"
    assert entry["url"] == "https://example.org/x/ENSX0001"


def test_an_identifier_states_its_version(test_namespaces, visitor_client):
    entity = entity_of(
        taxid=test_namespaces.taxon_d, created_by_release=public(), label="VERSIONED"
    )
    EntityIdentifierFactory(
        entity=entity,
        namespace=test_namespaces.versioned,
        value="ENSX0001.4",
        role=PRIMARY,
    )
    [entry] = page(visitor_client, entity)["identifiers"]
    assert entry == {
        "namespace": "test_versioned",
        "value": "ENSX0001.4",
        "version": "4",
        "role": "primary",
        "url": None,
    }


# ── Who sees what ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", ["unapproved", "withdrawn"])
def test_a_hidden_release_shows_neither_its_entity_nor_its_edges(
    kind, registry, staff_user, visitor_client, staff_client
):
    shown = public()
    visible = EntityFactory(created_by_release=shown)
    ev = hidden_evidence(kind)
    partner = EntityFactory(created_by_release=ev.release)
    state(ev, RelationshipFactory(subject=visible, object=partner))
    hide(ev, kind, staff_user)
    for client in (visitor_client, staff_client):
        assert client.get(entity_url(partner)).status_code == 404
        body = page(client, visible)
        assert body["relationship_counts"] == {}
        assert ids(body["releases"]) == refs(shown)


def test_an_entity_visible_only_through_an_assertion(registry, visitor_client):
    named = EntityFactory(label="NAMED")
    release = public_release(named, EntityFactory(label="PARTNER"))
    body = page(visitor_client, named)
    assert body["created_by_release"] is None
    assert ids(body["releases"]) == refs(release)
    assert body["relationship_counts"] == {"test_regulation": 1}


def test_an_entity_no_release_created_or_named_is_invisible_to_everyone(
    registry, visitor_client, staff_client
):
    # Decision S5: RF1a shows such a row to staff.
    entity = EntityFactory()
    assert visitor_client.get(entity_url(entity)).status_code == 404
    assert staff_client.get(entity_url(entity)).status_code == 404


def test_an_entity_named_by_a_superseded_release_only(
    registry, visitor_client, staff_client
):
    old = approvable_release(status=SUPERSEDED)
    approve(old, INTERNAL, PUBLIC_VIEW)
    entity = EntityFactory(label="OLD-ONLY", created_by_release=old)
    for client in (visitor_client, staff_client):
        assert client.get(entity_url(entity)).status_code == 404
        body = page(client, entity, **pin(old))
        assert body["created_by_release"] == f"release:{old.pk}"
        [entry] = body["releases"]
        assert entry["id"] == f"release:{old.pk}"
        assert entry["status"] == "superseded"
        assert entry["superseded"] is True


def test_a_staged_release_is_pinned_by_staff_only(
    registry, staff_user, visitor_client, staff_client
):
    release = hidden_release("staged", staff_user)
    entity = EntityFactory(label="STAGED-ONLY", created_by_release=release)
    pinned = entity_url(entity, **pin(release))
    assert visitor_client.get(entity_url(entity)).status_code == 404
    assert visitor_client.get(pinned).status_code == 404
    assert staff_client.get(entity_url(entity)).status_code == 404
    body = page(staff_client, entity, **pin(release))
    assert body["created_by_release"] == f"release:{release.pk}"
    [entry] = body["releases"]
    assert entry["status"] == "staged"
    assert entry["superseded"] is False


def test_staff_preview_needs_internal(registry, staff_client):
    ev = evidence_release(records=0, approved=False)
    advance(ev.release, STAGED)
    entity = EntityFactory(label="STAGED-ONLY", created_by_release=ev.release)
    assert staff_client.get(entity_url(entity, **pin(ev.release))).status_code == 404


def test_an_embargoed_release_hides_from_visitors_only(
    registry, staff_user, visitor_client, staff_client
):
    release = hidden_release("embargoed", staff_user)
    entity = EntityFactory(label="EMBARGOED", created_by_release=release)
    # Whatever public_view says, and whatever is pinned.
    assert visitor_client.get(entity_url(entity)).status_code == 404
    assert visitor_client.get(entity_url(entity, **pin(release))).status_code == 404
    body = page(staff_client, entity)
    assert body["created_by_release"] == f"release:{release.pk}"
    [entry] = body["releases"]
    assert entry["scopes"] == ["internal"]


def test_a_page_never_names_a_creator_the_caller_may_not_see(
    registry, staff_user, visitor_client
):
    # Visible for another reason: an embargoed creator, then one outside the
    # default set.
    embargoed = hidden_release("embargoed", staff_user)
    old = approvable_release(status=SUPERSEDED)
    approve(old, INTERNAL, PUBLIC_VIEW)
    for creator in (embargoed, old):
        entity = EntityFactory(created_by_release=creator)
        release = public_release(entity, EntityFactory())
        body = page(visitor_client, entity)
        assert body["created_by_release"] is None
        assert ids(body["releases"]) == refs(release)


def test_a_pinned_set_replaces_the_default_set(registry, visitor_client):
    entity = EntityFactory()
    first = public_release(entity, EntityFactory())
    second = public_release(entity, EntityFactory())
    assert ids(page(visitor_client, entity)["releases"]) == refs(first, second)
    body = page(visitor_client, entity, **pin(second))
    assert ids(body["releases"]) == refs(second)
    assert body["relationship_counts"] == {"test_regulation": 1}
    nothing = entity_url(entity, releases="release:9223372036854775808")
    assert visitor_client.get(nothing).status_code == 404


def test_a_release_that_only_states_an_edge_is_listed(registry, visitor_client):
    # Every assertion a commit writes has its participants, so its release
    # names the entity too. The page lists what an edge shows all the same.
    shown = public()
    entity = entity_of("protein", created_by_release=shown)
    ev = shown_evidence()
    edge = link("isoform_of", entity_of("protein_isoform"), entity)
    EvidenceAssertionFactory(
        relationship=edge,
        release=ev.release,
        import_run=ev.run,
        source_record=mapped_record(ev.release, ev.run),
    )
    advance(ev.release, PUBLISHED)
    body = page(visitor_client, entity)
    assert body["created_by_release"] == f"release:{shown.pk}"
    assert ids(body["releases"]) == refs(shown, ev.release)
    assert body["reference_relationships"][0]["releases"] == refs(ev.release)
    assert body["reference_relationships"][0]["is_synthetic"] is False


def test_a_reference_edge_has_the_shared_edge_shape(registry, visitor_client):
    # One edge shape for every route: the eight keys of the first version
    # first, with the values they had, then the summary's figures.
    shown = public()
    entity = entity_of("protein", created_by_release=shown)
    ev = shown_evidence()
    isoform = entity_of("protein_isoform")
    edge = link("isoform_of", isoform, entity)
    state(ev, edge)
    advance(ev.release, PUBLISHED)
    [listed] = page(visitor_client, entity)["reference_relationships"]
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
    assert (listed["id"], listed["source"], listed["target"]) == (
        f"relationship:{edge.pk}",
        f"entity:{isoform.pk}",
        f"entity:{entity.pk}",
    )
    assert (listed["releases"], listed["is_synthetic"]) == (refs(ev.release), False)
    assert (listed["shown"], listed["assertion_count"], listed["effect"]) == (
        True,
        1,
        "not_applicable",
    )
    assert (listed["level"], listed["level_counts"], listed["tested_levels"]) == (
        None,
        {},
        {},
    )


def test_a_release_withdrawn_while_the_page_is_read_is_named_nowhere(
    registry, staff_user, visitor_client, monkeypatch
):
    # release_entries reads each status again, and whatever a page names comes
    # from what it returned.
    ev = shown_evidence()
    entity = entity_of("protein", created_by_release=ev.release)
    state(ev, link("isoform_of", entity_of("protein_isoform"), entity))
    advance(ev.release, PUBLISHED)
    entries_of = views.release_entries

    def withdrawn_first(pks, releases):
        withdraw_release(ev.release, reason="Test only.", withdrawn_by=staff_user)
        return entries_of(pks, releases)

    monkeypatch.setattr(views, "release_entries", withdrawn_first)
    body = page(visitor_client, entity)
    assert body["created_by_release"] is None
    assert body["releases"] == []
    [edge] = body["reference_relationships"]
    assert edge["releases"] == []
    assert edge["is_synthetic"] is False


def test_nodes_are_sorted_by_numeric_id(registry, visitor_client):
    ev = shown_evidence()
    entry = entity_of("protein", pk=1_000_000_000, created_by_release=ev.release)
    isoform = entity_of("protein_isoform", pk=999_999_999)
    state(ev, link("isoform_of", isoform, entry))
    advance(ev.release, PUBLISHED)
    assert ids(page(visitor_client, entry)["nodes"]) == [
        "entity:999999999",
        "entity:1000000000",
    ]


def test_a_page_with_no_reference_relationship_has_no_nodes(registry, visitor_client):
    entity = EntityFactory(created_by_release=public())
    public_release(entity, EntityFactory())
    body = page(visitor_client, entity)
    assert body["reference_relationships"] == []
    assert body["nodes"] == []
    assert body["relationship_counts"] == {"test_regulation": 1}


# ── Merged and retired ──────────────────────────────────────────────────────


def merged_into(survivor, creator):
    """A merged row, written by a factory: nothing else writes one before P5."""
    return EntityFactory(
        status=EntityStatus.MERGED, merged_into=survivor, created_by_release=creator
    )


def test_a_merged_entity_redirects_to_its_survivor(registry, visitor_client):
    release = public()
    survivor = EntityFactory(label="SURVIVOR", created_by_release=release)
    merged = merged_into(survivor, release)
    target = f"{ENTITIES}entity:{survivor.pk}"
    query = f"releases=release:{release.pk}"

    response = visitor_client.get(f"{ENTITIES}entity:{merged.pk}")
    assert response.status_code == 301
    assert response["Location"] == target
    assert response.content == b""
    assert "Authorization" in response["Vary"]

    # A pinned selection survives the redirect.
    pinned = f"{ENTITIES}entity:{merged.pk}?{query}"
    response = visitor_client.get(pinned)
    assert response.status_code == 301
    assert response["Location"] == f"{target}?{query}"
    assert response.content == b""

    followed = visitor_client.get(pinned, follow=True)
    assert followed.redirect_chain == [(f"{target}?{query}", 301)]
    assert followed.status_code == 200
    assert followed.json()["entity"]["id"] == f"entity:{survivor.pk}"

    # No page, whatever the request asks for.
    browsable = visitor_client.get(f"{ENTITIES}entity:{merged.pk}?format=api")
    assert browsable.status_code == 301
    assert browsable["Location"] == f"{target}?format=api"
    assert browsable.content == b""


def test_a_merged_entity_costs_what_a_nonexistent_one_does(registry, visitor_client):
    release = public()
    survivor = EntityFactory(created_by_release=release)
    merged = merged_into(survivor, release)
    assert (
        answered(visitor_client, entity_url(merged))[2]
        == answered(visitor_client, nowhere(merged))[2]
    )


@pytest.mark.parametrize("hidden", ["row", "survivor", "both"])
def test_a_merged_entity_the_caller_may_not_follow_is_a_404(
    hidden, registry, visitor_client, staff_client
):
    shown, secret = public(), published()
    survivor = EntityFactory(
        created_by_release=secret if hidden in ("survivor", "both") else shown
    )
    merged = merged_into(survivor, secret if hidden in ("row", "both") else shown)
    for client in (visitor_client, staff_client):
        status, content, queries = answered(client, entity_url(merged))
        assert status == 404
        assert json.loads(content) == NOT_FOUND
        assert (status, content, queries) == answered(client, nowhere(merged))


def test_a_selection_decides_whether_a_merged_entity_redirects(
    registry, visitor_client
):
    # Pinned to the merged row's release alone, the survivor is outside the set.
    first, second = public(), public()
    survivor = EntityFactory(created_by_release=second)
    merged = merged_into(survivor, first)
    assert visitor_client.get(entity_url(merged)).status_code == 301
    assert visitor_client.get(entity_url(merged, **pin(first))).status_code == 404
    assert visitor_client.get(entity_url(merged, **pin(first, second))).status_code == (
        301
    )


def test_a_retired_entity_answers_with_its_status(registry, visitor_client):
    entity = EntityFactory(status=EntityStatus.RETIRED, created_by_release=public())
    assert page(visitor_client, entity)["entity"]["status"] == "retired"


# ── Entity text ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", HIDDEN_KINDS)
def test_a_hidden_creators_text_is_replaced(kind, registry, staff_user, visitor_client):
    entity = identified(
        "HGNC:801",
        label="SECRET-CODENAME",
        description="Written by a hidden release.",
        created_by_release=hidden_release(kind, staff_user),
    )
    release = public_release(entity, EntityFactory())
    body = page(visitor_client, entity)
    assert body["entity"]["label"] == "HGNC:801"
    assert body["description"] == ""
    assert body["created_by_release"] is None
    assert ids(body["releases"]) == refs(release)
    assert "SECRET-CODENAME" not in json.dumps(body)


def test_a_readable_creators_text_is_shown(registry, visitor_client):
    entity = identified(
        "HGNC:801",
        label="dorsal",
        description="Written by a public release.",
        created_by_release=public(),
    )
    body = page(visitor_client, entity)
    assert body["entity"]["label"] == "dorsal"
    assert body["description"] == "Written by a public release."


def test_a_row_no_release_created_keeps_its_text(registry, visitor_client):
    entity = EntityFactory(label="no creator", description="Entered by hand.")
    public_release(entity, EntityFactory())
    body = page(visitor_client, entity)
    assert body["entity"]["label"] == "no creator"
    assert body["description"] == "Entered by hand."


def test_a_pinned_view_keeps_a_reference_releases_text(registry, visitor_client):
    # The writer is outside the selection, and the caller may read it.
    entity = EntityFactory(label="dorsal", created_by_release=public())
    other = public_release(entity, EntityFactory())
    body = page(visitor_client, entity, **pin(other))
    assert body["entity"]["label"] == "dorsal"
    assert body["created_by_release"] is None


def test_an_edges_node_shows_only_text_the_caller_may_read(
    registry, staff_user, visitor_client
):
    ev = shown_evidence()
    gene = entity_of(created_by_release=ev.release)
    secret = entity_of(
        "protein",
        label="SECRET-CODENAME",
        created_by_release=hidden_release("unapproved", staff_user),
    )
    EntityIdentifierFactory(
        entity=secret,
        namespace=IdentifierNamespace.objects.get(slug="uniprotkb"),
        value="P04637",
        role=PRIMARY,
    )
    state(ev, link("encodes", gene, secret))
    advance(ev.release, PUBLISHED)
    labels = {node["id"]: node["label"] for node in page(visitor_client, gene)["nodes"]}
    assert labels[f"entity:{secret.pk}"] == "P04637"


# ── What a page costs ───────────────────────────────────────────────────────


def counted(client, url) -> int:
    status, content, queries = answered(client, url)
    assert status == 200, content
    return queries


def test_a_hidden_reference_link_does_not_change_the_query_count(
    registry, visitor_client
):
    shown = public()
    plain = entity_of(label="SAME", created_by_release=shown)
    linked = entity_of(label="SAME", created_by_release=shown)
    ev = evidence_release(records=0, approved=False)
    state(ev, link("encodes", linked, entity_of("protein")))
    advance(ev.release, PUBLISHED)

    def body_apart_from_its_id(entity):
        return page(visitor_client, entity) | {"query": None, "entity": None}

    assert body_apart_from_its_id(plain) == body_apart_from_its_id(linked)
    assert counted(visitor_client, entity_url(plain)) == counted(
        visitor_client, entity_url(linked)
    )


def test_the_entity_page_costs_the_same_at_any_degree(registry, visitor_client):
    # Reference edges are the only loop the entity's degree drives. A default
    # factory relationship is a regulation type, which that loop never sees.
    drafts = [shown_evidence() for _ in range(3)]
    one = entity_of("protein", created_by_release=drafts[0].release)
    many = entity_of("protein", created_by_release=drafts[0].release)
    state(drafts[0], link("isoform_of", entity_of("protein_isoform"), one))
    for index in range(30):
        state(
            drafts[index % 3],
            link("isoform_of", entity_of("protein_isoform"), many),
        )
    for ev in drafts:
        advance(ev.release, PUBLISHED)

    body = page(visitor_client, many)
    assert len(body["reference_relationships"]) == 30
    assert len(body["nodes"]) == 31
    assert len(body["releases"]) == 3
    assert body["relationship_counts"] == {"isoform_of": 30}
    assert len(page(visitor_client, one)["reference_relationships"]) == 1
    first = counted(visitor_client, entity_url(one))
    assert first == counted(visitor_client, entity_url(many))
    assert first <= 22


def test_the_entity_page_costs_the_same_at_any_number_of_identifiers(
    registry, visitor_client
):
    # An identifier's link reads its namespace, which must come with the row.
    shown = public()
    one = entity_of(created_by_release=shown)
    many = entity_of(created_by_release=shown)
    EntityIdentifierFactory(entity=one, namespace=IdentifierNamespaceFactory())
    for _ in range(10):
        EntityIdentifierFactory(entity=many, namespace=IdentifierNamespaceFactory())

    assert len(page(visitor_client, many)["identifiers"]) == 10
    first = counted(visitor_client, entity_url(one))
    assert first == counted(visitor_client, entity_url(many))
    assert first <= 22


def test_60_regulation_edges_are_counted_for_the_cost_of_one(registry, visitor_client):
    regulation = "transcriptional_regulation"
    ev = shown_evidence()
    one = entity_of(created_by_release=ev.release)
    many = entity_of(created_by_release=ev.release)
    state(ev, link(regulation, one, entity_of()))
    for _ in range(60):
        state(ev, link(regulation, many, entity_of()))
    advance(ev.release, PUBLISHED)
    assert page(visitor_client, many)["relationship_counts"] == {regulation: 60}
    assert counted(visitor_client, entity_url(one)) == counted(
        visitor_client, entity_url(many)
    )


def test_a_real_token_and_the_largest_selection_stay_under_the_bound(
    registry, staff_user, visitor_client
):
    releases = [public() for _ in range(3)]
    ev = shown_evidence()
    entry = entity_of("protein", created_by_release=releases[0])
    state(ev, link("isoform_of", entity_of("protein_isoform"), entry))
    advance(ev.release, PUBLISHED)
    token = RefreshToken.for_user(staff_user).access_token
    visitor_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    named = [release.pk for release in (*releases, ev.release)]
    named += list(range(10_000_000, 10_000_000 + MAX_RELEASES - len(named)))
    query = ",".join(f"release:{pk}" for pk in named)
    assert counted(visitor_client, entity_url(entry, releases=query)) <= 22

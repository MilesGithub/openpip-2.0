"""GET /api/graph/v1/resolve: which entities a term names, of those the caller
may see (architecture §7.3).

Its parameters and its envelope; who sees what, on factory rows; statuses and
what shadows what, counted inside the query, over visible entities and
readable labels only; truncation; the releases a selection reads; and the G6
label cases through the route, on the demonstration, for every kind of caller.

Non-primary identifiers are factory rows, as in test_resolution: nothing
imports them before RF1a, which gates each row by the release that states it.
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework_simplejwt.tokens import RefreshToken

from graph.api import views
from graph.approvals import record_decision
from graph.demo import DATASET, DATASET_SLUG, DEMO_LICENCE, LICENCE_URL
from graph.ingest.registry import get_adapter
from graph.models import (
    ApprovalScope,
    Decision,
    EntityStatus,
    IdentifierRole,
)
from graph.releases import withdraw_release
from graph.tests.api_helpers import (
    HIDDEN_KINDS,
    RESOLVE,
    hidden_release,
    pin,
    public,
    public_release,
    published,
    resolve_url,
)
from graph.tests.factories import (
    EntityFactory,
    EntityIdentifierFactory,
    approvable_release,
    approve,
    entity_of,
    evidence_release,
)
from graph.tests.test_provenance import STAGED, SUPERSEDED, advance

INTERNAL = ApprovalScope.INTERNAL
PUBLIC_VIEW = ApprovalScope.PUBLIC_VIEW
ALIAS = IdentifierRole.ALIAS
ENVELOPE = ["api_version", "query", "results", "releases", "page", "warnings"]
NOT_CUT = {"truncated": False, "max_candidates": 20, "omitted": {}}


def identified(namespace, value, taxid, **fields):
    """A gene whose primary identifier is `value`, labelled so unless told
    otherwise."""
    entity = entity_of("gene", taxid, **{"label": value, **fields})
    EntityIdentifierFactory(
        entity=entity,
        namespace=namespace,
        value=value,
        role=IdentifierRole.PRIMARY,
    )
    return entity


def ask(client, *terms, **parameters) -> dict:
    """The body of a 200 for these terms."""
    response = client.get(resolve_url(*terms, **parameters))
    assert response.status_code == 200, response.content
    return response.json()


def refused(client, query) -> dict:
    """The body of a 400 for this query."""
    response = client.get(RESOLVE, query)
    assert response.status_code == 400, response.content
    return response.json()


def problem(parameter, value=None, allowed=None, limit=None) -> dict:
    return {"parameter": parameter, "value": value, "allowed": allowed, "limit": limit}


def statuses(body) -> list:
    return [result["status"] for result in body["results"]]


def only_candidate(body) -> dict:
    [result] = body["results"]
    [candidate] = result["candidates"]
    return candidate


def kinds(candidate) -> list:
    return [match["kind"] for match in candidate["matches"]]


# ── Parameters ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value", ["A1,B1", "A1\tB1", "A1\nB1", "A1\r\nB1", " A1 ,\t B1 ,,"]
)
def test_q_splits_on_commas_tabs_and_new_lines(value, switch_on, visitor_client):
    body = visitor_client.get(RESOLVE, {"q": value}).json()
    assert body["query"]["q"] == ["A1", "B1"]
    assert [result["term"] for result in body["results"]] == ["A1", "B1"]


def test_q_does_not_split_on_spaces(registry, visitor_client):
    EntityFactory(label="gene one", created_by_release=public())
    body = ask(visitor_client, "gene one")
    assert body["query"]["q"] == ["gene one"]
    assert only_candidate(body)["entity"]["label"] == "gene one"


def test_q_may_be_repeated(switch_on, visitor_client):
    body = visitor_client.get(RESOLVE, {"q": ["A1,B1", "C1"]}).json()
    assert body["query"]["q"] == ["A1", "B1", "C1"]
    assert len(body["results"]) == 3


def test_a_term_is_at_most_200_characters(switch_on, visitor_client):
    assert visitor_client.get(RESOLVE, {"q": "x" * 200}).status_code == 200
    term = "x" * 201
    assert refused(visitor_client, {"q": term}) == {
        "detail": "q terms are at most 200 characters.",
        "errors": [problem("q", term, limit=200)],
    }


def test_q_names_at_most_25_terms_however_they_are_sent(switch_on, visitor_client):
    # 25 fifteen-character ids hold 399 characters: the cap is per term.
    terms = [f"ID{index:013d}" for index in range(26)]
    assert len(ask(visitor_client, *terms[:25])["results"]) == 25
    assert refused(visitor_client, {"q": ",".join(terms)}) == {
        "detail": "q names at most 25 terms.",
        "errors": [problem("q", limit=25)],
    }
    assert refused(visitor_client, {"q": terms}) == {
        "detail": "q names at most 25 terms.",
        "errors": [problem("q", limit=25)],
    }


@pytest.mark.parametrize(
    "query,value",
    [("", None), ("q=", None), ("q=,,,", ",,,"), ("q=%20", " "), ("q=%09", "\t")],
)
def test_q_names_at_least_one_term(query, value, switch_on, visitor_client):
    response = visitor_client.get(f"{RESOLVE}?{query}")
    assert response.status_code == 400
    assert response.json() == {
        "detail": "q names at least one term.",
        "errors": [problem("q", value)],
    }


@pytest.mark.parametrize(
    "name,value,allowed",
    [
        (
            "namespace",
            "UNIPROTKB",
            [
                "ensembl_gene",
                "hgnc",
                "ncbigene",
                "synthetic_gene",
                "synthetic_protein",
                "uniprotkb",
            ],
        ),
        ("entity_type", "chemical", ["gene", "protein", "protein_isoform"]),
        # Numerically sorted: as strings, 32630 would come first.
        ("taxon", "1", ["7227", "9606", "32630"]),
        ("taxon", "abc", ["7227", "9606", "32630"]),
    ],
)
def test_an_unknown_value_lists_the_allowed_ones(
    name, value, allowed, registry, visitor_client
):
    assert refused(visitor_client, {"q": "x", name: value}) == {
        "detail": f"Unknown {name} {value!r}. Allowed: {', '.join(allowed)}.",
        "errors": [problem(name, value, allowed)],
    }


@pytest.mark.parametrize(
    "name,value", [("namespace", "hgnc"), ("taxon", "9606"), ("entity_type", "gene")]
)
def test_before_any_sync_no_value_is_allowed(name, value, switch_on, visitor_client):
    assert refused(visitor_client, {"q": "x", name: value}) == {
        "detail": f"Unknown {name} {value!r}. Allowed: none yet.",
        "errors": [problem(name, value, [])],
    }


@pytest.mark.parametrize(
    "value,piece",
    [
        ("3", "3"),
        ("release:", "release:"),
        ("entity:3", "entity:3"),
        ("release:1,,release:2", ""),
        ("release:03", "release:03"),
    ],
)
def test_a_malformed_release_ref_is_400(value, piece, switch_on, visitor_client):
    assert refused(visitor_client, {"q": "x", "releases": value}) == {
        "detail": "releases: a release ref looks like release:3.",
        "errors": [problem("releases", piece)],
    }


@pytest.mark.parametrize("value", ["", ",", ",,,"])
def test_a_selection_names_at_least_one_release(value, switch_on, visitor_client):
    assert refused(visitor_client, {"q": "x", "releases": value}) == {
        "detail": "releases names at least one release.",
        "errors": [problem("releases")],
    }


def test_a_selection_names_at_most_50_distinct_releases(switch_on, visitor_client):
    refs = [f"release:{pk}" for pk in range(1, 52)]
    assert refused(visitor_client, {"q": "x", "releases": ",".join(refs)}) == {
        "detail": "releases names at most 50 releases.",
        "errors": [problem("releases", limit=50)],
    }
    body = ask(visitor_client, "x", releases=",".join(refs[:50]))
    assert body["query"]["releases"] == refs[:50]
    # Duplicates are no more releases.
    body = ask(visitor_client, "x", releases=",".join(refs[:50] * 2))
    assert body["query"]["releases"] == refs[:50]


def test_repeated_selections_merge_and_duplicates_collapse(switch_on, visitor_client):
    response = visitor_client.get(
        RESOLVE,
        {"q": "x", "releases": ["release:10,release:2", "release:2", "release:9"]},
    )
    assert response.json()["query"]["releases"] == [
        "release:2",
        "release:9",
        "release:10",
    ]


def test_a_hostile_term_is_an_answer(registry, visitor_client):
    terms = (
        "'",
        '"',
        "%",
        "_",
        "\\",
        ":",
        "::",
        "a:b:c",
        "uniprotkb:",
        ":P04637",
        "HGNC:",
        "hgnc:hgnc:5",
        "ß",
        "ﬁ",
        "İ",
        "🧬",
        "x" * 200,
        "ENSG0.1.2",
        "<script>",
        "--",
        "a b",
    )
    assert statuses(ask(visitor_client, *terms)) == ["unmatched"] * len(terms)


def test_every_problem_is_listed_in_order(switch_on, visitor_client):
    # The query string runs backwards, so the order shown is the route's.
    query = "releases=bad&entity_type=chemical&taxon=0&namespace=nowhere&q="
    response = visitor_client.get(f"{RESOLVE}?{query}")
    assert response.status_code == 400
    assert [entry["parameter"] for entry in response.json()["errors"]] == [
        "q",
        "namespace",
        "taxon",
        "entity_type",
        "releases",
    ]


@pytest.mark.parametrize(
    "query", ["releases=", "releases=,", "releases=3", "releases=release:1,3"]
)
def test_a_bad_selection_is_400_before_anything_is_read(
    query, switch_on, visitor_client, django_assert_num_queries
):
    with django_assert_num_queries(0):
        response = visitor_client.get(f"{RESOLVE}?q=x&{query}")
    assert response.status_code == 400
    assert [entry["parameter"] for entry in response.json()["errors"]] == ["releases"]


# ── The envelope ────────────────────────────────────────────────────────────


def test_the_envelope_and_its_exact_keys(test_namespaces, visitor_client):
    release = public()
    identified(
        test_namespaces.exact_a,
        "dl",
        test_namespaces.taxon_a,
        created_by_release=release,
    )
    body = ask(visitor_client, "dl")
    assert list(body) == ENVELOPE
    assert body["api_version"] == "graph/v1"
    assert list(body["query"]) == [
        "q",
        "namespace",
        "taxon",
        "entity_type",
        "releases",
        "browser",
    ]
    [result] = body["results"]
    assert list(result) == ["term", "status", "truncated", "candidates"]
    [candidate] = result["candidates"]
    assert list(candidate) == ["entity", "exact_primary", "matches", "releases"]
    assert candidate["entity"]["status"] == "provisional"
    assert list(candidate["entity"]) == [
        "id",
        "type",
        "label",
        "taxon",
        "status",
        "primary_identifier",
    ]
    assert candidate["entity"]["primary_identifier"] == {
        "namespace": "test_exact_a",
        "value": "dl",
    }
    assert [list(match) for match in candidate["matches"]] == [
        ["kind", "namespace", "value", "case_exact", "version_mismatch"]
    ] * len(candidate["matches"])
    assert candidate["releases"] == [f"release:{release.pk}"]
    [entry] = body["releases"]
    assert list(entry) == [
        "id",
        "dataset",
        "version",
        "status",
        "superseded",
        "is_synthetic",
        "licence",
        "attribution",
        "changes_note",
        "scopes",
    ]
    assert list(entry["dataset"]) == ["slug", "title"]
    assert list(entry["licence"]) == ["name", "spdx_id", "url"]
    assert entry["id"] == f"release:{release.pk}"
    assert entry["status"] == "published"
    assert entry["superseded"] is False
    assert entry["is_synthetic"] is False
    assert entry["changes_note"] is None
    assert entry["scopes"] == ["internal", "public_view"]
    assert body["page"] == NOT_CUT
    assert body["warnings"] == []


def test_an_entry_lists_every_effective_scope_in_their_order(registry, visitor_client):
    # Not by name, and not only the two scopes that decide who sees a release.
    scopes = ["internal", "public_view", "public_bulk", "external_services"]
    EntityFactory(label="ALL", created_by_release=published(*scopes))
    [entry] = ask(visitor_client, "ALL")["releases"]
    assert entry["scopes"] == scopes


def test_the_query_echoes_the_request_not_what_the_service_kept(
    registry, visitor_client
):
    unapproved = published()
    visible = public()
    body = ask(
        visitor_client,
        " uniprotkb:P04637 ",
        "x",
        "x",
        namespace="uniprotkb",
        taxon=9606,
        entity_type="protein",
        **pin(visible, unapproved),
    )
    assert body["query"] == {
        "q": ["uniprotkb:P04637", "x", "x"],
        "namespace": "uniprotkb",
        "taxon": 9606,
        "entity_type": "protein",
        "releases": [f"release:{unapproved.pk}", f"release:{visible.pk}"],
        "browser": None,
    }
    assert [result["term"] for result in body["results"]] == [
        "uniprotkb:P04637",
        "x",
        "x",
    ]


def test_with_nothing_given_the_query_is_null(switch_on, visitor_client):
    assert ask(visitor_client, "x")["query"] == {
        "q": ["x"],
        "namespace": None,
        "taxon": None,
        "entity_type": None,
        "releases": None,
        "browser": None,
    }


@pytest.mark.parametrize(
    "adapter,note",
    [
        ("", None),
        ("an_adapter_this_code_no_longer_has", None),
        ("canonical_tsv_v1", get_adapter("canonical_tsv_v1").spec.changes_note),
    ],
)
def test_an_entry_carries_the_note_of_changes_of_its_adapter(
    adapter, note, registry, visitor_client
):
    EntityFactory(label="NOTED", created_by_release=public(adapter=adapter))
    [entry] = ask(visitor_client, "NOTED")["releases"]
    assert entry["changes_note"] == note


def test_a_licence_is_always_an_object_with_blanks_as_null(registry, visitor_client):
    stated = public(
        terms={
            "licence_name": "CC BY 4.0",
            "spdx_id": "CC-BY-4.0",
            "licence_url": "https://example.org/by",
            "attribution": "Written by someone.",
        }
    )
    blank = public(
        terms={"licence_name": "", "licence_url": "", "spdx_id": "", "attribution": ""}
    )
    EntityFactory(label="STATED", created_by_release=stated)
    EntityFactory(label="BLANK", created_by_release=blank)
    body = ask(visitor_client, "STATED", "BLANK")
    assert [(entry["licence"], entry["attribution"]) for entry in body["releases"]] == [
        (
            {
                "name": "CC BY 4.0",
                "spdx_id": "CC-BY-4.0",
                "url": "https://example.org/by",
            },
            "Written by someone.",
        ),
        ({"name": None, "spdx_id": None, "url": None}, ""),
    ]


def test_every_unmatched_term_gives_no_releases(switch_on, visitor_client):
    body = ask(visitor_client, "x", "y")
    assert statuses(body) == ["unmatched", "unmatched"]
    assert [result["candidates"] for result in body["results"]] == [[], []]
    assert body["releases"] == []
    assert body["page"] == NOT_CUT
    assert body["warnings"] == []


# ── Who sees what ───────────────────────────────────────────────────────────


def test_visitors_see_public_view_releases_and_staff_internal_ones(
    registry, visitor_client, staff_client
):
    EntityFactory(label="SHOWN", created_by_release=public())
    EntityFactory(label="STAFF-ONLY", created_by_release=published(INTERNAL))
    assert statuses(ask(visitor_client, "SHOWN", "STAFF-ONLY")) == [
        "candidate",
        "unmatched",
    ]
    assert statuses(ask(staff_client, "SHOWN", "STAFF-ONLY")) == [
        "candidate",
        "candidate",
    ]


@pytest.mark.parametrize("kind", ["unapproved", "withdrawn", "draft", "no release"])
def test_an_entity_only_a_hidden_release_or_none_created_is_unmatched_for_everyone(
    kind, test_namespaces, staff_user, visitor_client, staff_client
):
    # "no release" pins decision S5: RF1a shows such a row to staff.
    creator = None if kind == "no release" else hidden_release(kind, staff_user)
    hidden = identified(
        test_namespaces.exact_a,
        "dl",
        test_namespaces.taxon_a,
        label="HIDDEN",
        created_by_release=creator,
    )
    EntityIdentifierFactory(
        entity=hidden,
        namespace=test_namespaces.global_alias,
        value="hry",
        role=ALIAS,
    )
    # By label, primary identifier, case variant and alias.
    terms = ("HIDDEN", "dl", "DL", "hry")
    assert statuses(ask(visitor_client, *terms)) == ["unmatched"] * 4
    assert statuses(ask(staff_client, *terms)) == ["unmatched"] * 4
    if creator is not None:
        assert (
            statuses(ask(visitor_client, *terms, **pin(creator))) == ["unmatched"] * 4
        )
        assert statuses(ask(staff_client, *terms, **pin(creator))) == ["unmatched"] * 4


def test_an_embargoed_release_hides_from_visitors_only(
    registry, staff_user, visitor_client, staff_client
):
    release = hidden_release("embargoed", staff_user)
    EntityFactory(label="EMBARGOED", created_by_release=release)
    # Whatever public_view says, and whatever is pinned.
    assert statuses(ask(visitor_client, "EMBARGOED")) == ["unmatched"]
    assert statuses(ask(visitor_client, "EMBARGOED", **pin(release))) == ["unmatched"]
    assert statuses(ask(staff_client, "EMBARGOED")) == ["candidate"]
    body = ask(staff_client, "EMBARGOED", **pin(release))
    assert statuses(body) == ["candidate"]
    [entry] = body["releases"]
    assert entry["id"] == f"release:{release.pk}"
    assert entry["scopes"] == ["internal"]


def test_an_entity_visible_only_through_an_assertion_resolves(registry, visitor_client):
    named = EntityFactory(label="NAMED")
    release = public_release(named, EntityFactory(label="PARTNER"))
    body = ask(visitor_client, "NAMED")
    candidate = only_candidate(body)
    assert candidate["entity"]["id"] == f"entity:{named.pk}"
    assert candidate["releases"] == [f"release:{release.pk}"]
    assert [entry["id"] for entry in body["releases"]] == [f"release:{release.pk}"]


def test_a_row_no_release_created_resolves_on_every_path_once_it_is_named(
    test_namespaces, visitor_client
):
    # The creator's join stays outer in each of the three queries.
    entity = identified(
        test_namespaces.exact_a,
        "ABC1",
        test_namespaces.taxon_a,
        label="no creator",
    )
    public_release(entity, EntityFactory(label="PARTNER"))
    assert statuses(ask(visitor_client, "ABC1", "abc1", "NO CREATOR")) == [
        "resolved",
        "candidate",
        "candidate",
    ]


def test_a_candidate_lists_only_releases_the_caller_may_see(
    test_namespaces, visitor_client
):
    entity = identified(
        test_namespaces.exact_a,
        "dl",
        test_namespaces.taxon_a,
        created_by_release=published(),
    )
    shown = public_release(entity, EntityFactory(label="PARTNER"))
    body = ask(visitor_client, "dl")
    assert only_candidate(body)["releases"] == [f"release:{shown.pk}"]
    assert [entry["id"] for entry in body["releases"]] == [f"release:{shown.pk}"]


def test_a_release_withdrawn_while_the_terms_are_read_is_named_nowhere(
    registry, staff_user, visitor_client, monkeypatch
):
    # release_entries reads each status again, and whatever a candidate names
    # comes from what it returned. naming_releases takes the creator from the
    # row in hand, so nothing else would drop it.
    release = public()
    EntityFactory(label="ONE", created_by_release=release)
    entries_of = views.release_entries

    def withdrawn_first(pks, releases):
        withdraw_release(release, reason="Test only.", withdrawn_by=staff_user)
        return entries_of(pks, releases)

    monkeypatch.setattr(views, "release_entries", withdrawn_first)
    body = ask(visitor_client, "ONE")
    assert only_candidate(body)["releases"] == []
    assert body["releases"] == []


def test_a_visitor_pinning_an_internal_only_release_gets_unmatched(
    registry, visitor_client, staff_client
):
    release = published(INTERNAL)
    EntityFactory(label="STAFF-ONLY", created_by_release=release)
    assert statuses(ask(visitor_client, "STAFF-ONLY", **pin(release))) == ["unmatched"]
    assert statuses(ask(staff_client, "STAFF-ONLY", **pin(release))) == ["candidate"]


def test_a_pinned_set_replaces_the_default_set(registry, visitor_client):
    first, second = public(), public()
    EntityFactory(label="FIRST", created_by_release=first)
    EntityFactory(label="SECOND", created_by_release=second)
    assert statuses(ask(visitor_client, "FIRST", "SECOND")) == ["candidate"] * 2
    assert statuses(ask(visitor_client, "FIRST", "SECOND", **pin(first))) == [
        "candidate",
        "unmatched",
    ]
    nothing = {"releases": "release:9223372036854775808"}
    assert statuses(ask(visitor_client, "FIRST", "SECOND", **nothing)) == [
        "unmatched",
        "unmatched",
    ]


def test_a_superseded_release_is_out_of_the_default_set(
    registry, visitor_client, staff_client
):
    old = approvable_release(status=SUPERSEDED)
    approve(old, INTERNAL, PUBLIC_VIEW)
    EntityFactory(label="OLD-ONLY", created_by_release=old)
    assert statuses(ask(visitor_client, "OLD-ONLY")) == ["unmatched"]
    assert statuses(ask(staff_client, "OLD-ONLY")) == ["unmatched"]
    for client in (visitor_client, staff_client):
        body = ask(client, "OLD-ONLY", **pin(old))
        assert statuses(body) == ["candidate"]
        [entry] = body["releases"]
        assert entry["id"] == f"release:{old.pk}"
        assert entry["status"] == "superseded"
        assert entry["superseded"] is True


def test_a_staged_release_is_pinned_by_staff_only(
    registry, staff_user, visitor_client, staff_client
):
    release = hidden_release("staged", staff_user)
    EntityFactory(label="STAGED-ONLY", created_by_release=release)
    assert statuses(ask(visitor_client, "STAGED-ONLY")) == ["unmatched"]
    assert statuses(ask(visitor_client, "STAGED-ONLY", **pin(release))) == ["unmatched"]
    assert statuses(ask(staff_client, "STAGED-ONLY")) == ["unmatched"]
    body = ask(staff_client, "STAGED-ONLY", **pin(release))
    assert statuses(body) == ["candidate"]
    [entry] = body["releases"]
    assert entry["status"] == "staged"
    assert entry["superseded"] is False


def test_staff_preview_needs_internal(registry, staff_client):
    ev = evidence_release(records=0, approved=False)
    advance(ev.release, STAGED)
    EntityFactory(label="STAGED-ONLY", created_by_release=ev.release)
    assert statuses(ask(staff_client, "STAGED-ONLY", **pin(ev.release))) == [
        "unmatched"
    ]


def test_a_hidden_term_reads_like_an_unknown_one(test_namespaces, visitor_client):
    identified(
        test_namespaces.exact_a,
        "dl",
        test_namespaces.taxon_a,
        created_by_release=published(),
    )

    def answered(term):
        with CaptureQueriesContext(connection) as queries:
            response = visitor_client.get(RESOLVE, {"q": term})
        return response.content.decode().replace(term, "TERM"), len(queries)

    assert answered("dl") == answered("zz")


# ── Statuses ────────────────────────────────────────────────────────────────


def test_the_four_statuses(test_namespaces, visitor_client):
    release = public()
    gene = identified(
        test_namespaces.exact_a,
        "dl",
        test_namespaces.taxon_a,
        label="dorsal",
        created_by_release=release,
    )
    EntityFactory(label="LABELLED", created_by_release=release)
    EntityFactory(label="TWIN", created_by_release=release)
    EntityFactory(label="TWIN", created_by_release=release)
    body = ask(visitor_client, "dl", "LABELLED", "TWIN", "NOTHING")
    assert statuses(body) == ["resolved", "candidate", "ambiguous", "unmatched"]
    [resolved, candidate, ambiguous, unmatched] = body["results"]
    assert [item["entity"]["id"] for item in resolved["candidates"]] == [
        f"entity:{gene.pk}"
    ]
    assert resolved["candidates"][0]["exact_primary"] is True
    assert kinds(resolved["candidates"][0]) == ["primary"]
    assert candidate["candidates"][0]["exact_primary"] is False
    assert kinds(candidate["candidates"][0]) == ["label"]
    assert len(ambiguous["candidates"]) == 2
    assert unmatched["candidates"] == []


def test_one_entity_matched_two_ways_is_a_candidate_not_ambiguous(
    test_namespaces, visitor_client
):
    entity = EntityFactory(label="hairy", created_by_release=public())
    EntityIdentifierFactory(
        entity=entity,
        namespace=test_namespaces.global_alias,
        value="hairy",
        role=ALIAS,
    )
    body = ask(visitor_client, "hairy")
    assert statuses(body) == ["candidate"]
    assert kinds(only_candidate(body)) == ["alias", "label"]


def test_a_hidden_entity_does_not_count(registry, visitor_client):
    shown = EntityFactory(label="TWIN", created_by_release=public())
    EntityFactory(label="TWIN", created_by_release=published())
    body = ask(visitor_client, "TWIN")
    assert statuses(body) == ["candidate"]
    assert only_candidate(body)["entity"]["id"] == f"entity:{shown.pk}"


def test_taxon_leaves_the_entities_of_that_taxon(registry, visitor_client):
    release = public()
    entity_of(taxid=9606, label="TWIN", created_by_release=release)
    fly = entity_of(taxid=7227, label="TWIN", created_by_release=release)
    assert statuses(ask(visitor_client, "TWIN")) == ["ambiguous"]
    body = ask(visitor_client, "TWIN", taxon=7227)
    assert statuses(body) == ["candidate"]
    assert only_candidate(body)["entity"]["id"] == f"entity:{fly.pk}"


def test_an_alias_a_case_variant_and_a_version_mismatch_are_candidates(
    test_namespaces, visitor_client
):
    release = public()
    namespaces = test_namespaces
    aliased = EntityFactory(label="ALIASED", created_by_release=release)
    EntityIdentifierFactory(
        entity=aliased, namespace=namespaces.global_alias, value="hry", role=ALIAS
    )
    identified(
        namespaces.exact_a,
        "ABC1",
        namespaces.taxon_a,
        label="CASED",
        created_by_release=release,
    )
    identified(
        namespaces.versioned,
        "ENSX0001.4",
        namespaces.taxon_d,
        label="VERSIONED",
        created_by_release=release,
    )
    body = ask(visitor_client, "hry", "abc1", "ENSX0001.3")
    assert statuses(body) == ["candidate", "candidate", "candidate"]
    [alias, case, version] = [
        result["candidates"][0]["matches"][0] for result in body["results"]
    ]
    assert alias == {
        "kind": "alias",
        "namespace": "test_global_alias",
        "value": "hry",
        "case_exact": True,
        "version_mismatch": False,
    }
    assert case == {
        "kind": "primary",
        "namespace": "test_exact_a",
        "value": "ABC1",
        "case_exact": False,
        "version_mismatch": False,
    }
    assert version == {
        "kind": "primary",
        "namespace": "test_versioned",
        "value": "ENSX0001.4",
        "case_exact": True,
        "version_mismatch": True,
    }


@pytest.mark.parametrize("status", (EntityStatus.MERGED, EntityStatus.RETIRED))
def test_a_merged_entity_is_returned_with_its_status(status, registry, visitor_client):
    release = public()
    survivor = EntityFactory(label="SURVIVOR", created_by_release=release)
    fields = {"merged_into": survivor} if status == EntityStatus.MERGED else {}
    EntityFactory(label="OLD", status=status, created_by_release=release, **fields)
    assert only_candidate(ask(visitor_client, "OLD"))["entity"]["status"] == status


def test_a_hidden_exact_match_shadows_no_visible_case_variant(
    test_namespaces, visitor_client
):
    # Filtered in the query. A filter applied afterwards would drop Abc1 and
    # answer unmatched, since an exact match stops the search for case variants.
    identified(
        test_namespaces.exact_a,
        "Abc1",
        test_namespaces.taxon_a,
        label="HIDDEN",
        created_by_release=published(),
    )
    visible = identified(
        test_namespaces.exact_a,
        "ABC1",
        test_namespaces.taxon_a,
        label="SHOWN",
        created_by_release=public(),
    )
    body = ask(visitor_client, "Abc1")
    assert statuses(body) == ["candidate"]
    candidate = only_candidate(body)
    assert candidate["entity"]["id"] == f"entity:{visible.pk}"
    [match] = candidate["matches"]
    assert match["value"] == "ABC1"
    assert match["case_exact"] is False


def test_a_hidden_exact_label_shadows_no_visible_case_variant(registry, visitor_client):
    EntityFactory(label="Mixed", created_by_release=published())
    shown = EntityFactory(label="MIXED", created_by_release=public())
    candidate = only_candidate(ask(visitor_client, "Mixed"))
    assert candidate["entity"]["id"] == f"entity:{shown.pk}"
    assert candidate["matches"][0]["case_exact"] is False


# ── Truncation ──────────────────────────────────────────────────────────────


def many(count):
    release = public()
    for _ in range(count):
        EntityFactory(label="MANY", created_by_release=release)


def test_21_matches_are_cut_to_20_and_the_cut_is_reported(registry, visitor_client):
    many(21)
    body = ask(visitor_client, "MANY")
    [result] = body["results"]
    assert result["status"] == "ambiguous"
    assert len(result["candidates"]) == 20
    assert result["truncated"] is True
    assert body["page"] == {
        "truncated": True,
        "max_candidates": 20,
        "omitted": {"terms": 1},
    }
    assert body["warnings"] == [
        {
            "code": "candidates_truncated",
            "detail": "MANY: more than 20 entities match, 20 shown.",
        }
    ]


def test_a_release_only_the_cut_candidate_names_is_not_listed(registry, visitor_client):
    shown, past_the_cut = public(), public()
    for _ in range(20):
        EntityFactory(label="MANY", created_by_release=shown)
    # Equal matches sort by id, so this one is the twenty-first.
    EntityFactory(label="MANY", created_by_release=past_the_cut)
    body = ask(visitor_client, "MANY")
    [result] = body["results"]
    assert result["truncated"] is True
    assert [entry["id"] for entry in body["releases"]] == [f"release:{shown.pk}"]


def test_20_matches_are_not_cut(registry, visitor_client):
    many(20)
    body = ask(visitor_client, "MANY")
    [result] = body["results"]
    assert result["status"] == "ambiguous"
    assert len(result["candidates"]) == 20
    assert result["truncated"] is False
    assert body["page"] == NOT_CUT
    assert body["warnings"] == []


def test_each_term_says_whether_it_was_cut(registry, visitor_client):
    many(21)
    EntityFactory(label="ONE", created_by_release=public())
    body = ask(visitor_client, "ONE", "MANY")
    assert [result["truncated"] for result in body["results"]] == [False, True]
    assert body["page"]["omitted"] == {"terms": 1}


def test_the_status_counts_the_entities_past_the_cut(registry, visitor_client):
    release = public()
    for _ in range(25):
        EntityFactory(label="MANY", created_by_release=release)
    # Hidden matches are not among them: a hidden twenty-first cuts nothing.
    for _ in range(5):
        EntityFactory(label="FEW", created_by_release=release)
    unapproved = published()
    for _ in range(20):
        EntityFactory(label="FEW", created_by_release=unapproved)
    body = ask(visitor_client, "MANY", "FEW")
    assert statuses(body) == ["ambiguous", "ambiguous"]
    assert [len(result["candidates"]) for result in body["results"]] == [20, 5]
    assert [result["truncated"] for result in body["results"]] == [True, False]


def test_a_repeated_term_is_resolved_once(registry, visitor_client, monkeypatch):
    many(21)
    calls = []
    resolve_many = views.resolve_many

    def spy(terms, **options):
        calls.append(list(terms))
        return resolve_many(terms, **options)

    monkeypatch.setattr(views, "resolve_many", spy)
    body = ask(visitor_client, "x", "x", "x")
    assert calls == [["x"]]
    assert body["query"]["q"] == ["x", "x", "x"]
    assert body["results"] == [body["results"][0]] * 3

    calls.clear()
    body = ask(visitor_client, "MANY", "x", "MANY")
    assert calls == [["MANY", "x"]]
    assert [result["term"] for result in body["results"]] == ["MANY", "x", "MANY"]
    assert body["results"][0] == body["results"][2]
    # One warning for a term cut, however often it is given.
    assert len(body["warnings"]) == 1
    assert body["page"]["omitted"] == {"terms": 1}


# ── Entity text ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", HIDDEN_KINDS)
def test_a_hidden_creators_label_is_neither_shown_nor_matched(
    kind, test_namespaces, staff_user, visitor_client
):
    secret = identified(
        test_namespaces.exact_a,
        "dl",
        test_namespaces.taxon_a,
        label="SECRET-CODENAME",
        created_by_release=hidden_release(kind, staff_user),
    )
    public_release(secret, EntityFactory(label="PARTNER"))
    assert statuses(ask(visitor_client, "SECRET-CODENAME")) == ["unmatched"]
    assert statuses(ask(visitor_client, "secret-codename")) == ["unmatched"]
    candidate = only_candidate(ask(visitor_client, "dl"))
    assert candidate["entity"]["id"] == f"entity:{secret.pk}"
    assert candidate["entity"]["label"] == "dl"
    assert kinds(candidate) == ["primary"]


def test_a_row_with_no_primary_is_named_by_its_ref(test_namespaces, visitor_client):
    entity = EntityFactory(label="SECRET-CODENAME", created_by_release=published())
    EntityIdentifierFactory(
        entity=entity,
        namespace=test_namespaces.global_alias,
        value="hry",
        role=ALIAS,
    )
    public_release(entity, EntityFactory(label="PARTNER"))
    node = only_candidate(ask(visitor_client, "hry"))["entity"]
    assert node["label"] == f"entity:{entity.pk}"
    assert node["primary_identifier"] is None


def test_a_pinned_view_keeps_a_reference_releases_labels(
    test_namespaces, visitor_client
):
    # The label's writer is outside the selection, and the caller may read it.
    reference = public()
    entity = identified(
        test_namespaces.exact_a,
        "dl",
        test_namespaces.taxon_a,
        label="dorsal",
        created_by_release=reference,
    )
    other = public_release(entity, EntityFactory(label="PARTNER"))
    body = ask(visitor_client, "dorsal", **pin(other))
    candidate = only_candidate(body)
    assert candidate["entity"]["label"] == "dorsal"
    assert candidate["releases"] == [f"release:{other.pk}"]


def test_staff_read_a_staged_creators_text_with_no_selection(
    test_namespaces, staff_user, staff_client
):
    # May read ignores the selection. The staged release is still named by
    # nothing a response shows.
    entity = identified(
        test_namespaces.exact_a,
        "dl",
        test_namespaces.taxon_a,
        label="WORKING-NAME",
        created_by_release=hidden_release("staged", staff_user),
    )
    shown = public_release(entity, EntityFactory(label="PARTNER"))
    body = ask(staff_client, "WORKING-NAME")
    candidate = only_candidate(body)
    assert candidate["entity"]["label"] == "WORKING-NAME"
    assert candidate["releases"] == [f"release:{shown.pk}"]
    assert [entry["id"] for entry in body["releases"]] == [f"release:{shown.pk}"]


# ── Query counts ────────────────────────────────────────────────────────────


def counted(client, url) -> int:
    with CaptureQueriesContext(connection) as queries:
        response = client.get(url)
    assert response.status_code == 200, response.content
    return len(queries)


def test_terms_of_one_shape_cost_the_same_at_1_and_at_25(
    test_namespaces, visitor_client
):
    release = public()
    values = [f"gene{index}" for index in range(25)]
    labels = [f"LABEL-{index}" for index in range(25)]
    for value, label in zip(values, labels):
        identified(
            test_namespaces.exact_a,
            value,
            test_namespaces.taxon_a,
            label=label,
            created_by_release=release,
        )
    for terms in (values, labels, [values[0]] * 25):
        one = counted(visitor_client, resolve_url(terms[0]))
        all_of_them = counted(visitor_client, resolve_url(*terms))
        assert one == all_of_them <= 20, terms[0]


def test_a_candidate_costs_no_query_of_its_own(registry, visitor_client):
    many(21)
    EntityFactory(label="ONE", created_by_release=public())
    assert counted(visitor_client, resolve_url("MANY")) == counted(
        visitor_client, resolve_url("ONE")
    )


def test_a_parameter_not_given_costs_no_query(registry, visitor_client):
    # One to validate it, and one where the resolver checks it again.
    plain = counted(visitor_client, resolve_url("x"))
    assert counted(visitor_client, resolve_url("x", taxon=9606)) == plain + 2
    assert counted(visitor_client, resolve_url("x", entity_type="gene")) == plain + 2


def test_every_parameter_and_a_real_token_stay_under_the_bound(
    registry, staff_user, visitor_client
):
    release = public()
    entity = EntityFactory(label="ONE", created_by_release=release)
    named = public_release(entity, EntityFactory(label="PARTNER"))
    token = RefreshToken.for_user(staff_user).access_token
    visitor_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    url = resolve_url("ONE", taxon=9606, entity_type="gene", **pin(release, named))
    assert counted(visitor_client, url) <= 20
    url = resolve_url(
        "ONE",
        namespace="hgnc",
        taxon=9606,
        entity_type="gene",
        **pin(release, named),
    )
    assert counted(visitor_client, url) <= 20


# ── The demonstration ───────────────────────────────────────────────────────

TERMS = ("SYN-TF1", "SYN-ALPHA", "SYN-SHARED", "syn-tf1", "SYN-G3")
# Per term: its status, and each candidate's primary value with its matches as
# (kind, case_exact). The last term asks only for its node's label.
EVERY_LABEL = [
    ("resolved", [("SYN-TF1", [("primary", True), ("label", True)])]),
    ("candidate", [("SYN-G3", [("label", True)])]),
    ("ambiguous", [("SYN-G6", [("label", True)]), ("SYN-P3", [("label", True)])]),
    ("candidate", [("SYN-TF1", [("primary", False), ("label", False)])]),
]
NO_LABEL = [
    ("resolved", [("SYN-TF1", [("primary", True)])]),
    ("unmatched", []),
    ("unmatched", []),
    ("candidate", [("SYN-TF1", [("primary", False)])]),
]
NOTHING = [("unmatched", [])] * 4


def answers(body) -> list:
    return [
        (
            result["status"],
            [
                (
                    candidate["entity"]["primary_identifier"]["value"],
                    [
                        (match["kind"], match["case_exact"])
                        for match in candidate["matches"]
                    ],
                )
                for candidate in result["candidates"]
            ],
        )
        for result in body["results"][:4]
    ]


def node_label(body):
    """The label a node shows for the last term, SYN-G3."""
    return only_candidate({"results": body["results"][4:]})["entity"]["label"]


def public_view(release, alice):
    record_decision(
        release,
        PUBLIC_VIEW,
        Decision.APPROVED,
        rationale="Public.",
        decided_by=alice,
        evidence="Test evidence.",
    )


def test_staff_on_the_demonstration(demo):
    body = ask(demo.staff, *TERMS)
    assert answers(body) == EVERY_LABEL
    assert node_label(body) == "SYN-ALPHA"
    [entry] = body["releases"]
    assert entry == {
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
    tf1 = demo.entity("SYN-TF1")
    assert body["results"][0]["candidates"][0]["entity"] == {
        "id": f"entity:{tf1.pk}",
        "type": "gene",
        "label": "SYN-TF1",
        "taxon": 32630,
        "status": "provisional",
        "primary_identifier": {"namespace": "synthetic_gene", "value": "SYN-TF1"},
    }
    assert body["results"][0]["candidates"][0]["releases"] == [f"release:{demo.v2.pk}"]


def test_a_visitor_with_public_view_on_v1_and_v2(demo, visitor_client):
    public_view(demo.v1, demo.alice)
    public_view(demo.v2, demo.alice)
    body = ask(visitor_client, *TERMS)
    assert answers(body) == EVERY_LABEL
    assert node_label(body) == "SYN-ALPHA"


def test_a_visitor_with_public_view_on_v2_only_reads_no_label_of_v1(
    demo, visitor_client
):
    # The rule has no walk up supersedes: a change of that rule changes this
    # test, and only this.
    public_view(demo.v2, demo.alice)
    body = ask(visitor_client, *TERMS)
    assert answers(body) == NO_LABEL
    assert node_label(body) == "SYN-G3"


def test_after_v1_is_withdrawn_nobody_reads_its_labels(demo, visitor_client):
    public_view(demo.v2, demo.alice)
    withdraw_release(demo.v1, reason="Test only.", withdrawn_by=demo.alice)
    for client in (demo.staff, visitor_client):
        body = ask(client, *TERMS)
        assert answers(body) == NO_LABEL
        assert node_label(body) == "SYN-G3"


def test_a_visitor_before_any_public_view_sees_nothing(demo, visitor_client):
    body = ask(visitor_client, *TERMS)
    assert answers(body) == NOTHING
    assert body["results"][4]["status"] == "unmatched"
    assert body["releases"] == []


def test_the_filters_and_prefixes_on_the_demonstration(demo):
    shared = ask(demo.staff, "SYN-SHARED", entity_type="gene")
    assert statuses(shared) == ["candidate"]
    assert only_candidate(shared)["entity"]["primary_identifier"]["value"] == "SYN-G6"
    assert statuses(ask(demo.staff, "SYN-TF1", namespace="synthetic_protein")) == [
        "unmatched"
    ]
    for term, options in (
        ("synthetic_gene:SYN-TF1", {}),
        ("SYNTHETIC_GENE:SYN-TF1", {}),
        ("SYN-TF1", {"namespace": "synthetic_gene"}),
    ):
        assert statuses(ask(demo.staff, term, **options)) == ["resolved"], term


def test_staff_pinning_v1_get_the_same_answers_flagged(demo):
    body = ask(demo.staff, *TERMS, **pin(demo.v1))
    assert answers(body) == EVERY_LABEL
    assert body["query"]["releases"] == [f"release:{demo.v1.pk}"]
    [entry] = body["releases"]
    assert entry["id"] == f"release:{demo.v1.pk}"
    assert entry["status"] == "superseded"
    assert entry["superseded"] is True
    assert entry["scopes"] == ["internal"]

    body = ask(demo.staff, "SYN-TF1", **pin(demo.v1, demo.v2))
    assert only_candidate(body)["releases"] == [
        f"release:{demo.v1.pk}",
        f"release:{demo.v2.pk}",
    ]
    assert [entry["superseded"] for entry in body["releases"]] == [True, False]

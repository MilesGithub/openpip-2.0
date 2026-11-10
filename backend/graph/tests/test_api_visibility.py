"""Nothing from a hidden release appears, on any route (architecture D9).

api_helpers.SAMPLES names the requests of every served route, so a route with
no sample fails here. Each kind of hidden release is walked by a visitor and
by staff, with no selection, with a selection of that release by its ref,
and with the slug of an instance that selects it alone, which pins the same
set, over one set of rows:

- H, created and named only by the hidden release, with a label, a primary
  identifier and an alias;
- S, created by the hidden release and named by a public one, the case that
  would leak through Entity.label: S is visible, its text is not;
- V, visible, whose edge to S the hidden release states too, with a hidden
  regulation assertion and a hidden encodes edge to H. Both kinds of edge are
  built because only a reference type exercises reference_relationships.

An unapproved release is published, so its missing approval is the only thing
hiding it. A draft is a separate kind, hidden by its status alone, and proves
less. A discarded release is a draft again with its assertions gone: H and S
keep their creator, and V's hidden edges have no assertion left.

Staff see an embargoed release, which only loses its public scopes, and a
staged one once they pin it: those are asserted as seen. The walk skips the
caller's own words, which query and results[].term return by contract, and
asserts that no other value holds a forbidden one. A hidden term must also cost
what an unknown term does, and a hidden id what a nonexistent id does; for a
visitor, an instance they may not reach reads as an unknown slug, on its page
and through browser= on every route that takes it.

Three browser instances stand beside them: a public one selecting only the
hidden release, one only staff reach selecting only the public one, and a
public planned one selecting none. The hidden release declares a taxon no
other release does, and both releases carry their stats, so a facet an
instance derives from a release would show. Both browsers routes pin each
instance to its own selection, whatever the request selects: the gallery
names no release, so its samples run in every pass, and an instance's page
runs only for a caller who may not see the hidden release pinned.
"""

import json
import re
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext

from graph.models import ApprovalScope, IdentifierNamespace, IdentifierRole
from graph.tests.api_helpers import (
    BASE,
    BROWSER,
    BROWSERS,
    HIDDEN_KINDS,
    RESOLVE,
    SAMPLES,
    browser_url,
    browsers_url,
    entity_url,
    evidence_url,
    hidden_evidence,
    hide,
    ids,
    instance,
    link,
    network_url,
    pin,
    relationship_url,
    resolve_url,
    state,
    typed_routes,
    walk_json,
    with_stats,
)
from graph.tests.factories import (
    EntityIdentifierFactory,
    ReleaseTaxonFactory,
    approve,
    entity_of,
    evidence_release,
    taxon_of,
)
from graph.tests.test_provenance import PUBLISHED, STAGED, advance

NOT_FOUND = {"detail": "Not found."}
# The selections that show a hidden release to staff, as whether it is pinned:
# an embargo removes only the public scopes, and staff preview by pinning.
STAFF_SEE = {"embargoed": (False, True), "staged": (True,)}
# A test-only taxon, which only the hidden release declares.
HIDDEN_TAXON = 999_999_911
# The parameters that select releases, of which a request gives one.
SELECTIONS = {"releases", "browser"}
# A slug no instance has.
NO_INSTANCE = "no-such-instance"


def protein(release, label, primary, **fields):
    entity = entity_of("protein", label=label, created_by_release=release, **fields)
    EntityIdentifierFactory(
        entity=entity,
        namespace=IdentifierNamespace.objects.get(slug="uniprotkb"),
        value=primary,
        role=IdentifierRole.PRIMARY,
    )
    return entity


def world_of(kind, staff) -> SimpleNamespace:
    """The rows of the module docstring, with a release of this kind. Declared
    taxa change only while a release is a draft, and stats while it is a
    draft or staged, so both are written before the releases move on. The
    public release's stats name only encodes, a reference type, so the staff
    instance derives no type."""
    ev = hidden_evidence(kind)
    ReleaseTaxonFactory(release=ev.release, taxon=taxon_of(HIDDEN_TAXON))
    h = protein(ev.release, "HIDDEN-LABEL", "Q9Y6K9")
    EntityIdentifierFactory(entity=h, value="HIDDEN-ALIAS", role=IdentifierRole.ALIAS)
    s = protein(ev.release, "SECRET-LABEL", "P38398", description="SECRET-DESCRIPTION")

    shown = evidence_release(records=0)
    approve(shown.release, ApprovalScope.PUBLIC_VIEW)
    v = entity_of(label="VISIBLE-LABEL", created_by_release=shown.release)
    both_state = link("encodes", v, s)
    state(shown, both_state)
    with_stats(shown.release)
    advance(shown.release, PUBLISHED)

    hidden_edges = (
        link("encodes", v, h),
        link("transcriptional_regulation", h, v),
    )
    for edge in (both_state, *hidden_edges):
        state(ev, edge)
    with_stats(ev.release)
    hidden = hide(ev, kind, staff)
    return SimpleNamespace(
        hidden=hidden,
        public=shown.release,
        h=h,
        h_primary="Q9Y6K9",
        h_alias="HIDDEN-ALIAS",
        s=s,
        s_primary="P38398",
        v=v,
        both_state=both_state,
        hidden_edges=hidden_edges,
        hidden_taxon=HIDDEN_TAXON,
        instance=instance(hidden, title="WORLD-INSTANCE"),
        staff_instance=instance(
            shown.release, visibility="staff", title="STAFF-INSTANCE"
        ),
        empty_instance=instance(status="planned", title="EMPTY-INSTANCE"),
    )


def forbidden_words(world, *, reads_text) -> set:
    """What no response may hold. `reads_text`: the caller may read S's text,
    which is only ever S's own."""
    words = {
        f"entity:{world.h.pk}",
        world.h.label,
        world.h_primary,
        world.h_alias,
        f"release:{world.hidden.pk}",
        *(f"relationship:{edge.pk}" for edge in world.hidden_edges),
    }
    if not reads_text:
        words |= {world.s.label, world.s.description}
    return words


def echoed(path) -> bool:
    """The caller's own words, which a response returns by contract."""
    return path[:1] == ("query",) or (
        path[:1] == ("results",) and path[2:] == ("term",)
    )


def leaks(body, words) -> list:
    """Where a parsed body holds one of the words, the caller's own words
    apart. A ref ends where its digits do: entity:12 is not in entity:123."""
    return [
        (path, word)
        for path, text in walk_json(body)
        if not echoed(path)
        for word in words
        if re.search(rf"{re.escape(word)}(?!\d)", text, re.IGNORECASE)
    ]


def with_query(url, query) -> str:
    return f"{url}{'&' if '?' in url else '?'}{urlencode(query)}"


def selects(url) -> bool:
    """Whether the URL already selects releases."""
    given = parse_qs(urlsplit(url).query, keep_blank_values=True)
    return not SELECTIONS.isdisjoint(given)


def sample_urls(world, query, *, sees_pinned):
    """Every sample, each under this selection, appended to the samples of
    every view that takes it. A sample that already selects gets no second
    selection, which would be the 400 of releases with browser. status and
    vocabularies take no parameters, so a selected request to them is only
    the 400 of test_api_base. The browsers routes take no selection: they
    pin each instance to its own. The gallery names no release, so its
    samples come in every pass; an instance's page names those the caller
    may see of its selection, so its samples come only when `sees_pinned` is
    false, the caller not seeing the hidden release pinned."""
    views = {path: view for _, path, view in typed_routes()}
    for path, builders in SAMPLES.items():
        if path == BROWSER and sees_pinned:
            continue
        for build in builders:
            url = build(world)
            if not query or path in (BROWSERS, BROWSER):
                yield url
            elif set(query) <= set(views[path].allowed_params) and not selects(url):
                yield with_query(url, query)


def body_of(client, url) -> dict:
    response = client.get(url)
    assert response.status_code == 200, response.content
    return response.json()


def assert_seen(client, world, query):
    """The release is in the caller's set, so everything of it shows."""
    hidden = f"release:{world.hidden.pk}"
    terms = (world.h.label, world.h_primary, world.h_alias, world.s.label)
    body = body_of(client, resolve_url(*terms, **query))
    assert [result["status"] for result in body["results"]] == [
        "candidate",
        "resolved",
        "candidate",
        "candidate",
    ]
    assert hidden in ids(body["releases"])

    h = body_of(client, entity_url(world.h, **query))
    assert h["entity"]["label"] == world.h.label
    assert h["created_by_release"] == hidden
    assert [row["value"] for row in h["identifiers"]] == [
        world.h_primary,
        world.h_alias,
    ]

    s = body_of(client, entity_url(world.s, **query))
    assert s["entity"]["label"] == world.s.label
    assert s["description"] == world.s.description
    assert s["created_by_release"] == hidden

    v = body_of(client, entity_url(world.v, **query))
    assert v["relationship_counts"] == {
        "encodes": 2,
        "transcriptional_regulation": 1,
    }
    assert hidden in ids(v["releases"])
    assert f"entity:{world.h.pk}" in ids(v["nodes"])

    # The network: the hidden regulation edge under the default layers, both
    # encodes edges when named, the hidden release listed.
    network = body_of(client, network_url(world.v, **query))
    assert ids(network["edges"]) == [f"relationship:{world.hidden_edges[1].pk}"]
    assert f"entity:{world.h.pk}" in ids(network["nodes"])
    layered = body_of(client, network_url(world.v, layers="encodes", **query))
    assert ids(layered["edges"]) == [
        f"relationship:{world.both_state.pk}",
        f"relationship:{world.hidden_edges[0].pk}",
    ]
    assert hidden in ids(layered["releases"])
    page = body_of(client, relationship_url(world.hidden_edges[1], **query))
    assert ids(page["releases"]) == [hidden]
    listed = body_of(client, evidence_url(world.hidden_edges[1], **query))
    assert [row["release"] for row in listed["evidence"]] == [hidden]

    # The instance that selects it names it, though a staged release is not
    # yet live.
    detail = body_of(client, browser_url(world.instance.slug))
    assert [item["release"] for item in detail["instance"]["selection"]] == [hidden]
    assert ids(detail["releases"]) == [hidden]
    assert detail["instance"]["availability"] == (
        "metadata_only" if world.hidden.status == STAGED else "live"
    )


def facets_of(client, world) -> list:
    """The world instance's taxa and relationship types, on the gallery and
    on its page."""
    [item] = [
        item
        for item in body_of(client, BROWSERS)["instances"]
        if item["slug"] == world.instance.slug
    ]
    page = body_of(client, browser_url(world.instance.slug))["instance"]
    return [(shown["taxa"], shown["relationship_types"]) for shown in (item, page)]


def listed_slugs(client, url) -> list:
    return [item["slug"] for item in body_of(client, url)["instances"]]


def assert_hidden(client, world, query, *, reads_text, sees_pinned):
    """The release is outside the caller's set. A selection of it alone,
    `query`, names nothing the caller may see; with none, S and V show
    through the public release, and show nothing of the hidden one.
    `sees_pinned`: the caller sees it pinned, as staff see a staged release,
    which the browsers routes pin whatever the request selects."""
    pinned = bool(query)
    forbidden = forbidden_words(world, reads_text=reads_text)
    for url in sample_urls(world, query, sees_pinned=sees_pinned):
        response = client.get(url)
        assert response.status_code in (200, 404), (url, response.content)
        assert leaks(response.json(), forbidden) == [], url

    # What the world instance derives is what its pinned selection shows.
    by_taxon = browsers_url(taxon=world.hidden_taxon)
    by_type = browsers_url(relationship_type="transcriptional_regulation")
    if sees_pinned:
        derived = ([world.hidden_taxon], ["transcriptional_regulation"])
        assert facets_of(client, world) == [derived, derived]
        assert listed_slugs(client, by_taxon) == [world.instance.slug]
    else:
        assert facets_of(client, world) == [([], []), ([], [])]
        assert listed_slugs(client, by_taxon) == []
        assert listed_slugs(client, by_type) == []

    assert client.get(entity_url(world.h, **query)).json() == NOT_FOUND
    if pinned:
        assert client.get(entity_url(world.s, **query)).json() == NOT_FOUND
        assert client.get(entity_url(world.v, **query)).json() == NOT_FOUND
        # The selection names only the hidden release, so the caller's set is
        # empty: no seed is visible and no relationship exists.
        for layers in ({}, {"layers": "encodes"}):
            network = body_of(client, network_url(world.v, **layers, **query))
            assert (network["nodes"], network["edges"]) == ([], [])
        for build in (relationship_url, evidence_url):
            assert client.get(build(world.both_state, **query)).json() == NOT_FOUND
        return

    public = f"release:{world.public.pk}"
    s = body_of(client, entity_url(world.s))
    assert s["created_by_release"] is None
    assert s["entity"]["label"] == (world.s.label if reads_text else world.s_primary)
    assert s["description"] == (world.s.description if reads_text else "")
    assert [row["value"] for row in s["identifiers"]] == [world.s_primary]
    assert ids(s["releases"]) == [public]

    v = body_of(client, entity_url(world.v))
    assert v["relationship_counts"] == {"encodes": 1}
    assert ids(v["releases"]) == [public]
    assert [edge["releases"] for edge in v["reference_relationships"]] == [[public]]
    assert ids(v["nodes"]) == [f"entity:{world.s.pk}", f"entity:{world.v.pk}"]

    # The network around V: under the default layers its only candidate is
    # the hidden regulation edge, so V stands alone; the encodes layer lists
    # the edge both releases state, through the public one alone, with S's
    # text as the caller may read it.
    network = body_of(client, network_url(world.v))
    assert (ids(network["nodes"]), network["edges"]) == ([f"entity:{world.v.pk}"], [])
    layered = body_of(client, network_url(world.v, layers="encodes"))
    [edge] = layered["edges"]
    assert edge["id"] == f"relationship:{world.both_state.pk}"
    assert edge["releases"] == [public]
    assert ids(layered["nodes"]) == [f"entity:{world.s.pk}", f"entity:{world.v.pk}"]
    labels = {node["id"]: node["label"] for node in layered["nodes"]}
    assert labels[f"entity:{world.s.pk}"] == (
        world.s.label if reads_text else world.s_primary
    )
    # H is dropped without a word, so its network is empty under any layers.
    for layers in ({}, {"layers": "encodes"}):
        empty = body_of(client, network_url(world.h, **layers))
        assert (empty["nodes"], empty["edges"], empty["warnings"]) == ([], [], [])
    # The page and the evidence of the edge both releases state show the
    # public release's part alone.
    page = body_of(client, relationship_url(world.both_state))
    assert page["edge"]["releases"] == [public]
    assert [row["release"] for row in page["by_release"]] == [public]
    assert ids(page["releases"]) == [public]
    listed = body_of(client, evidence_url(world.both_state))
    assert [row["release"] for row in listed["evidence"]] == [public]
    assert ids(listed["releases"]) == [public]


def answer_to(client, term, query) -> tuple:
    """A term's answer, the term itself apart, and what it cost."""
    with CaptureQueriesContext(connection) as queries:
        response = client.get(resolve_url(term, **query))
    return response.content.decode().replace(term, "TERM"), len(queries)


def page_at(client, url) -> tuple:
    with CaptureQueriesContext(connection) as queries:
        response = client.get(url)
    return response.status_code, response.content, len(queries)


def headed_page_at(client, url) -> tuple:
    """page_at's answer with the response's headers."""
    with CaptureQueriesContext(connection) as queries:
        response = client.get(url)
    headers = sorted(response.headers.items())
    return response.status_code, response.content, headers, len(queries)


def browsed_at(client, url, slug) -> tuple:
    """page_at's answer to the URL under browser=slug, the slug itself apart,
    since the query echoes it."""
    with CaptureQueriesContext(connection) as queries:
        response = client.get(with_query(url, {"browser": slug}))
    body = response.content.decode().replace(slug, "SLUG")
    return response.status_code, body, len(queries)


def assert_costs_alike(client, world, query, *, caller):
    """H reads like nothing at all. For a visitor, once per test: an instance
    the caller may not reach reads like an unknown slug, on its page and
    through browser= on every sample of the routes that take it, and a
    selection hidden from the caller costs what an empty one does."""
    unknown = answer_to(client, "UNKNOWN-TERM", query)
    for term in (world.h.label, world.h_primary, world.h_alias):
        assert answer_to(client, term, query) == unknown, term
    nowhere = f"{BASE}entities/entity:{world.v.pk + 1_000}"
    if query:
        nowhere = f"{nowhere}?{urlencode(query)}"
    assert page_at(client, entity_url(world.h, **query)) == page_at(client, nowhere)
    # A hidden edge's page and evidence read like a nonexistent id's.
    missing = SimpleNamespace(pk=max(edge.pk for edge in world.hidden_edges) + 1_000)
    for build in (relationship_url, evidence_url):
        absent = page_at(client, build(missing, **query))
        assert (absent[0], json.loads(absent[1])) == (404, NOT_FOUND)
        for edge in world.hidden_edges:
            assert page_at(client, build(edge, **query)) == absent, edge
    if caller != "visitor" or query:
        return
    unknown = headed_page_at(client, browser_url(NO_INSTANCE))
    assert (unknown[0], json.loads(unknown[1])) == (404, NOT_FOUND)
    assert headed_page_at(client, browser_url(world.staff_instance.slug)) == unknown
    views = {path: view for _, path, view in typed_routes()}
    for path, builders in SAMPLES.items():
        if "browser" in views[path].allowed_params:
            for build in builders:
                url = build(world)
                assert browsed_at(client, url, world.staff_instance.slug) == (
                    browsed_at(client, url, NO_INSTANCE)
                ), url
    # Their bodies differ, by the instance shown or named, so only what they
    # cost is compared.
    for hidden, empty in (
        (browser_url(world.instance.slug), browser_url(world.empty_instance.slug)),
        (
            browsers_url(q=world.instance.title),
            browsers_url(q=world.empty_instance.title),
        ),
    ):
        assert page_at(client, hidden)[2] == page_at(client, empty)[2], hidden


@pytest.mark.parametrize("caller", ("visitor", "staff"))
@pytest.mark.parametrize("kind", HIDDEN_KINDS)
def test_nothing_from_a_hidden_release_appears(
    kind, caller, registry, staff_user, visitor_client, staff_client
):
    assert set(SAMPLES) == {path for _, path, _ in typed_routes()}
    client = staff_client if caller == "staff" else visitor_client
    world = world_of(kind, staff_user)
    seen = STAFF_SEE.get(kind, ()) if caller == "staff" else ()
    # May read ignores the selection, so staff read a staged creator's text
    # with none: it stays S's own text, and the release is named nowhere.
    reads_text = caller == "staff" and kind == "staged"
    # The instance selects the hidden release alone, so its slug pins what
    # the ref does.
    for query in ({}, pin(world.hidden), {"browser": world.instance.slug}):
        # Empty counters for each pass, as conftest.fresh_throttles gives
        # each test: a visitor's passes send 35, 15 and 15 requests at
        # graph_network's rate of 60 a minute.
        cache.clear()
        pinned = bool(query)
        if pinned in seen:
            assert_seen(client, world, query)
        else:
            assert_hidden(
                client,
                world,
                query,
                reads_text=reads_text,
                sees_pinned=True in seen,
            )
            assert_costs_alike(client, world, query, caller=caller)


def test_every_served_route_has_a_sample():
    """A key with an empty list passes the walk's check of the keys and
    takes its route out of the walk."""
    assert all(SAMPLES.values())


def test_the_walk_finds_a_word_anywhere_but_in_the_callers_own():
    body = {
        "query": {"q": ["SECRET"], "releases": ["release:12"]},
        "results": [
            {
                "term": "SECRET",
                "status": "candidate",
                "candidates": [{"entity": {"label": "a secret one"}}],
            }
        ],
        "SECRET": None,
        "releases": [{"id": "release:12"}, {"id": "release:123"}],
    }
    assert leaks(body, {"SECRET", "release:12"}) == [
        (("results", 0, "candidates", 0, "entity", "label"), "SECRET"),
        (("SECRET",), "SECRET"),
        (("releases", 0, "id"), "release:12"),
    ]


def test_a_sample_that_selects_already_is_given_no_second_selection(monkeypatch):
    # No sample selects today. One that did would otherwise be the 400 of
    # releases with browser.
    plain = resolve_url("x")
    by_ref = resolve_url("x", releases="release:3")
    by_slug = resolve_url("x", browser="an-instance")
    monkeypatch.setattr(
        f"{__name__}.SAMPLES",
        {RESOLVE: [lambda world: by_ref, lambda world: by_slug, lambda world: plain]},
    )
    for query in ({"releases": "release:3"}, {"browser": "an-instance"}):
        assert list(sample_urls(None, query, sees_pinned=False)) == [
            f"{plain}&{urlencode(query)}"
        ]

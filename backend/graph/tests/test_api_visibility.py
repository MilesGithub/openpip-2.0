"""Nothing from a hidden release appears, on any route (architecture D9).

api_helpers.SAMPLES names the requests of every served route, so a route with
no sample fails here. Each kind of hidden release is walked by a visitor and
by staff, with no selection and with a selection of that release, over one set
of rows:

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
what an unknown term does, and a hidden id what a nonexistent id does.
"""

import json
import re
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from graph.models import ApprovalScope, IdentifierNamespace, IdentifierRole
from graph.tests.api_helpers import (
    BASE,
    HIDDEN_KINDS,
    SAMPLES,
    entity_url,
    evidence_url,
    hidden_evidence,
    hide,
    ids,
    link,
    network_url,
    pin,
    relationship_url,
    resolve_url,
    state,
    typed_routes,
    walk_json,
)
from graph.tests.factories import (
    EntityIdentifierFactory,
    approve,
    entity_of,
    evidence_release,
)
from graph.tests.test_provenance import PUBLISHED, advance

NOT_FOUND = {"detail": "Not found."}
# The selections that show a hidden release to staff, as whether it is pinned:
# an embargo removes only the public scopes, and staff preview by pinning.
STAFF_SEE = {"embargoed": (False, True), "staged": (True,)}


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
    """The rows of the module docstring, with a release of this kind."""
    ev = hidden_evidence(kind)
    h = protein(ev.release, "HIDDEN-LABEL", "Q9Y6K9")
    EntityIdentifierFactory(entity=h, value="HIDDEN-ALIAS", role=IdentifierRole.ALIAS)
    s = protein(ev.release, "SECRET-LABEL", "P38398", description="SECRET-DESCRIPTION")

    shown = evidence_release(records=0)
    approve(shown.release, ApprovalScope.PUBLIC_VIEW)
    v = entity_of(label="VISIBLE-LABEL", created_by_release=shown.release)
    both_state = link("encodes", v, s)
    state(shown, both_state)
    advance(shown.release, PUBLISHED)

    hidden_edges = (
        link("encodes", v, h),
        link("transcriptional_regulation", h, v),
    )
    for edge in (both_state, *hidden_edges):
        state(ev, edge)
    return SimpleNamespace(
        hidden=hide(ev, kind, staff),
        public=shown.release,
        h=h,
        h_primary="Q9Y6K9",
        h_alias="HIDDEN-ALIAS",
        s=s,
        s_primary="P38398",
        v=v,
        both_state=both_state,
        hidden_edges=hidden_edges,
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


def sample_urls(world, query):
    """Every sample, each under this selection. status and vocabularies take
    no parameters, so a pinned request to them is only the 400 of
    test_api_base."""
    views = {path: view for _, path, view in typed_routes()}
    for path, builders in SAMPLES.items():
        for build in builders:
            url = build(world)
            if not query:
                yield url
            elif "releases" in views[path].allowed_params:
                yield f"{url}{'&' if '?' in url else '?'}{urlencode(query)}"


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


def assert_hidden(client, world, *, pinned, reads_text):
    """The release is outside the caller's set. A selection of it alone names
    nothing the caller may see; with none, S and V show through the public
    release, and show nothing of the hidden one."""
    query = pin(world.hidden) if pinned else {}
    forbidden = forbidden_words(world, reads_text=reads_text)
    for url in sample_urls(world, query):
        response = client.get(url)
        assert response.status_code in (200, 404), (url, response.content)
        assert leaks(response.json(), forbidden) == [], url

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


def assert_costs_alike(client, world, query):
    """H reads like nothing at all."""
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
    for pinned in (False, True):
        query = pin(world.hidden) if pinned else {}
        if pinned in seen:
            assert_seen(client, world, query)
        else:
            assert_hidden(client, world, pinned=pinned, reads_text=reads_text)
            assert_costs_alike(client, world, query)


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

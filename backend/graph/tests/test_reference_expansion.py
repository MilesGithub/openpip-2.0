"""graph.reference_closure and neighbourhood(expand="reference"): the typed
paths a network follows from its seeds before its lookups (architecture
§4.6), on X1's factory worlds (api_helpers.reference_world and capped_world)
and on the demonstration, through visible_release_ids. The paths and their
one or two statements; which derived seeds the cap on looked-up seeds keeps,
what it drops and the row bound; the joins, read under the default filter
whatever the request's; derived seeds in the lookups; a hidden or superseded
mapping release; the deadline; what a request costs. The route is tested in
test_api_network_expand.
"""

import dataclasses
import logging
import re
import time

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

import graph.api.limits
import graph.neighbourhood
import graph.reference_closure
from graph.models import SYNTHETIC_TAXON, EvidenceBasis, Outcome, RelationshipType
from graph.neighbourhood import (
    EXPANSIONS,
    MAX_EXPANDED_SEEDS,
    Neighbourhood,
    QueryTimedOut,
    neighbourhood,
)
from graph.reference_closure import (
    CLOSURE_ROW_LIMIT,
    EMPTY_CLOSURE,
    PATH_TYPES,
    REFERENCE_PATHS,
    Derivation,
    ReferenceClosure,
    reference_closure,
)
from graph.summary import ALL_OUTCOMES, ContextMatch, EvidenceFilter, ScoreRange
from graph.tests.api_helpers import (
    HIDDEN_KINDS,
    capped_world,
    link,
    mapping_release,
    reference_world,
    state,
    uniprot_release,
)
from graph.tests.factories import entity_of
from graph.tests.test_api_network import PI, TR, stored
from graph.tests.test_provenance import PUBLISHED, advance
from graph.tests.test_summary import staff, visitor
from graph.visibility import ReleaseSet

NOTHING_HIDDEN = {outcome: 0 for outcome in Outcome.values}
EMPTY = Neighbourhood((), (), frozenset(), NOTHING_HIDDEN, (), False, 0, 0, None)
# The timed block's own statements inside a test transaction: on PostgreSQL
# set_config and the savepoint pair, elsewhere none.
BLOCK = 3 if connection.vendor == "postgresql" else 0
# An EXISTS-backed filter of each kind, which the filtered world's evidence
# matches: a basis, a detection method, a score range and a context.
FILTERS = EvidenceFilter(
    evidence_bases={"experimental"},
    detection_methods={"MI:0018"},
    scores=(ScoreRange("stats:test_value", 0, 1),),
    contexts=(ContextMatch("tissue", raw="liver"),),
)


def hidden(**counts) -> dict:
    return {**NOTHING_HIDDEN, **counts}


def path(entity, seed, *steps) -> Derivation:
    """The Derivation of these rows: the entity reached, the named seed, and
    the relationships in order from the seed."""
    return Derivation(entity.pk, seed.pk, tuple(step.pk for step in steps))


def pks(*rows) -> frozenset:
    return frozenset(row.pk for row in rows)


def ids(found) -> list:
    return [edge.relationship_id for edge in found.edges]


def join_ids(found) -> list:
    return [join.relationship_id for join in found.joins]


def derived(found) -> frozenset:
    return frozenset(derivation.entity_id for derivation in found.derivations)


def never():
    raise AssertionError("a deadline was checked where no statement is sent")


def closure(*seeds, releases, limit=None) -> ReferenceClosure:
    """The closure of these rows under `releases`, as the service runs it:
    their own cap unless `limit` is given, and a deadline that never passes."""
    return reference_closure(
        [seed.pk for seed in seeds],
        releases=releases,
        check_deadline=lambda: None,
        limit=MAX_EXPANDED_SEEDS - len(seeds) if limit is None else limit,
    )


def expanded(*seeds, releases, **parameters) -> Neighbourhood:
    return neighbourhood(
        releases=releases,
        seeds=[seed.pk for seed in seeds],
        expand="reference",
        **parameters,
    )


def cost(call) -> tuple:
    """What a call returns, and how many statements it sent."""
    with CaptureQueriesContext(connection) as queries:
        result = call()
    return result, len(queries)


def made(kind, ev):
    """An entity of this type in the synthetic taxon, created by ev's release."""
    return entity_of(kind, SYNTHETIC_TAXON, created_by_release=ev.release)


def stated(ev, *edges):
    """Each relationship stated by ev, its basis unknown."""
    for edge in edges:
        state(ev, edge, evidence_basis=EvidenceBasis.UNKNOWN)


def published(*evs):
    """Each release advanced to published, so a visitor sees what it states."""
    for ev in evs:
        advance(ev.release, PUBLISHED)


def slower(seconds, module, name, calls):
    """The function `name` of `module`, which first records its arguments in
    `calls` and sleeps."""
    function = getattr(module, name)

    def slow(*args, **kwargs):
        calls.append(args)
        time.sleep(seconds)
        return function(*args, **kwargs)

    return slow


def recorder(sent):
    """Wrappers for graph.neighbourhood's later steps, which note each call."""

    def recorded(name):
        function = getattr(graph.neighbourhood, name)

        def record(*args, **kwargs):
            sent.append(name)
            return function(*args, **kwargs)

        return record

    return recorded


# ── The paths and the interface ─────────────────────────────────────────────


def test_the_paths_are_the_typed_paths():
    assert REFERENCE_PATHS == (
        (("encodes", "out"),),
        (("encodes", "out"), ("isoform_of", "in")),
        (("isoform_of", "in"),),
        (("encodes", "in"),),
        (("isoform_of", "out"),),
        (("isoform_of", "out"), ("encodes", "in")),
    )
    assert PATH_TYPES == frozenset({"encodes", "isoform_of"})
    # The prefix rule: a two-step path's first step is a path of its own.
    assert all(
        len(steps) == 1 or (steps[0],) in REFERENCE_PATHS for steps in REFERENCE_PATHS
    )
    # No path of more than two steps: the closure sends at most two statements.
    assert all(len(steps) in (1, 2) for steps in REFERENCE_PATHS)
    assert {way for steps in REFERENCE_PATHS for _, way in steps} == {"out", "in"}
    assert CLOSURE_ROW_LIMIT == 1000
    assert MAX_EXPANDED_SEEDS == graph.api.limits.MAX_EXPANDED_SEEDS == 26
    # Above the named seeds' own cap, so the published cap stays a true bound.
    assert MAX_EXPANDED_SEEDS > graph.api.limits.MAX_SEEDS
    assert EXPANSIONS == ("reference",)
    assert EMPTY_CLOSURE == ReferenceClosure((), 0) == ReferenceClosure((), 0, ())


def test_the_registry_fixes_each_paths_ends(registry):
    for slug, subjects, objects in (
        ("encodes", ["gene"], ["protein"]),
        ("isoform_of", ["protein_isoform"], ["protein"]),
    ):
        row = RelationshipType.objects.get(slug=slug)
        assert (
            sorted(row.allowed_subject_types.values_list("slug", flat=True)) == subjects
        )
        assert (
            sorted(row.allowed_object_types.values_list("slug", flat=True)) == objects
        )
        assert row.is_reference and not row.self_pairs_allowed
    assert (
        set(
            RelationshipType.objects.filter(is_reference=True).values_list(
                "slug", flat=True
            )
        )
        == PATH_TYPES
    )


def test_the_closure_refuses_a_hand_built_set_and_a_bad_limit(registry):
    with pytest.raises(TypeError):
        reference_closure(
            [1],
            releases=ReleaseSet(frozenset(), frozenset()),
            check_deadline=never,
            limit=25,
        )
    for limit in (-1, 1.5, True, "25", None):
        message = f"limit is a whole number of at least 0, not {limit!r}."
        with pytest.raises(ValueError, match=re.escape(message)):
            reference_closure(
                [1], releases=visitor(), check_deadline=never, limit=limit
            )


def test_the_service_refuses_an_unknown_expand_and_a_reference_layer(
    registry, settings
):
    releases = visitor()
    for on in (True, False):
        settings.TYPED_GRAPH_ENABLED = on
        with pytest.raises(
            ValueError,
            match=re.escape("expand is None or one of reference; not 'Reference'."),
        ):
            neighbourhood(releases=releases, seeds=[1], expand="Reference")
        with pytest.raises(
            ValueError,
            match=re.escape("layers names encodes, which expand follows itself."),
        ):
            neighbourhood(
                releases=releases,
                seeds=[1],
                expand="reference",
                layers=frozenset({TR, "encodes"}),
            )
        with pytest.raises(
            ValueError,
            match=re.escape(
                "layers names encodes, isoform_of, which expand follows itself."
            ),
        ):
            neighbourhood(
                releases=releases,
                seeds=[1],
                expand="reference",
                layers=frozenset({"isoform_of", "encodes"}),
            )
    settings.TYPED_GRAPH_ENABLED = True
    assert (
        neighbourhood(releases=visitor(), seeds=[1], layers=frozenset({"encodes"}))
        == EMPTY
    )


def test_the_switch_off_sends_nothing(registry, settings):
    w = reference_world()
    releases = visitor()
    settings.TYPED_GRAPH_ENABLED = False
    assert cost(
        lambda: reference_closure(
            [w.g1.pk], releases=releases, check_deadline=never, limit=25
        )
    ) == (EMPTY_CLOSURE, 0)
    assert cost(lambda: expanded(w.g1, releases=releases)) == (EMPTY, 0)


def test_no_seed_sends_no_closure_statement(registry):
    releases = visitor()
    assert cost(
        lambda: reference_closure([], releases=releases, check_deadline=never, limit=25)
    ) == (EMPTY_CLOSURE, 0)


# ── The typed paths ─────────────────────────────────────────────────────────


def test_from_a_gene_the_closure_reaches_its_entry_then_its_isoforms(registry):
    w = reference_world()
    releases = visitor()
    paths = (path(w.e, w.g1, w.enc1), path(w.i, w.g1, w.enc1, w.iso))
    assert cost(lambda: closure(w.g1, releases=releases)) == (
        ReferenceClosure(paths, 0),
        2,
    )
    found = expanded(w.g1, releases=releases)
    assert found.derivations == paths
    assert join_ids(found) == [w.enc1.pk, w.iso.pk]
    assert ids(found) == [w.phys.pk, w.reg1.pk]
    assert found.nodes == pks(w.g1, w.e, w.i, w.p, w.t)
    assert found.seeds == (w.g1.pk,)
    assert (found.dropped_derived_seeds, found.truncated_seeds, found.hidden) == (
        0,
        (),
        NOTHING_HIDDEN,
    )
    assert [
        (j.relationship_type, j.family, j.shown, j.assertion_count, j.by_basis)
        for j in found.joins
    ] == [
        ("encodes", "reference", True, 1, {"unknown": 1}),
        ("isoform_of", "reference", True, 1, {"unknown": 1}),
    ]


def test_from_an_entry_the_closure_reaches_its_isoform_and_every_gene_that_encodes_it(
    registry,
):
    w = reference_world()
    releases = visitor()
    assert cost(lambda: closure(w.e, releases=releases)) == (
        ReferenceClosure(
            (
                path(w.g1, w.e, w.enc1),
                path(w.g2, w.e, w.enc2),
                path(w.g3, w.e, w.enc3),
                path(w.i, w.e, w.iso),
            ),
            0,
        ),
        1,
    )
    found = expanded(w.e, releases=releases)
    assert ids(found) == [w.phys.pk, w.reg1.pk, w.reg2.pk]
    assert join_ids(found) == [w.enc1.pk, w.enc2.pk, w.enc3.pk, w.iso.pk]
    assert found.nodes == pks(w.g1, w.g2, w.g3, w.e, w.i, w.p, w.t, w.t2)


def test_from_an_isoform_the_closure_reaches_its_entry_then_the_genes_that_encode_it(
    registry,
):
    w = reference_world()
    releases = visitor()
    assert cost(lambda: closure(w.i, releases=releases)) == (
        ReferenceClosure(
            (
                path(w.g1, w.i, w.iso, w.enc1),
                path(w.g2, w.i, w.iso, w.enc2),
                path(w.g3, w.i, w.iso, w.enc3),
                path(w.e, w.i, w.iso),
            ),
            0,
        ),
        2,
    )
    found = expanded(w.i, releases=releases)
    other = expanded(w.e, releases=releases)
    assert (ids(found), join_ids(found), found.nodes) == (
        ids(other),
        join_ids(other),
        other.nodes,
    )


def test_a_gene_search_never_reaches_another_gene_through_a_shared_entry(registry):
    w = reference_world()
    releases = visitor()
    found = expanded(w.g1, releases=releases)
    assert derived(found) == pks(w.e, w.i)
    assert found.nodes.isdisjoint(pks(w.g2, w.g3, w.gp, w.t2))
    assert w.reg2.pk not in ids(found)
    assert set(join_ids(found)).isdisjoint({w.enc2.pk, w.enc3.pk, w.encp.pk})
    found = expanded(w.g2, releases=releases)
    assert derived(found) == pks(w.e, w.i)
    assert found.nodes.isdisjoint(pks(w.g1, w.g3, w.gp, w.t))
    assert ids(found) == [w.phys.pk, w.reg2.pk]
    assert join_ids(found) == [w.enc2.pk, w.iso.pk]


def test_a_partners_reference_links_are_never_followed(registry):
    w = reference_world()
    releases = visitor()
    assert closure(w.gp, releases=releases) == ReferenceClosure(
        (path(w.p, w.gp, w.encp),), 0
    )
    found = expanded(w.gp, releases=releases)
    assert found.nodes == pks(w.gp, w.e, w.p)
    assert join_ids(found) == [w.encp.pk]
    assert ids(found) == [w.phys.pk]
    found = expanded(w.g1, releases=releases)
    assert w.p.pk in found.nodes
    assert w.gp.pk not in found.nodes
    assert w.encp.pk not in join_ids(found)


def test_a_named_seed_is_never_derived_and_the_links_between_named_seeds_are_drawn(
    registry,
):
    w = reference_world()
    releases = visitor()
    assert cost(lambda: closure(w.g1, w.e, releases=releases)) == (
        ReferenceClosure(
            (
                path(w.g2, w.e, w.enc2),
                path(w.g3, w.e, w.enc3),
                path(w.i, w.g1, w.enc1, w.iso),
                path(w.i, w.e, w.iso),
            ),
            0,
            (path(w.g1, w.e, w.enc1), path(w.e, w.g1, w.enc1)),
        ),
        2,
    )
    found = expanded(w.g1, w.e, releases=releases)
    assert found.seeds == (w.g1.pk, w.e.pk)
    assert derived(found) == pks(w.g2, w.g3, w.i)
    assert join_ids(found) == [w.enc1.pk, w.enc2.pk, w.enc3.pk, w.iso.pk]
    assert found.nodes == pks(w.g1, w.g2, w.g3, w.e, w.i, w.p, w.t, w.t2)


def test_a_link_between_two_named_seeds_alone_is_a_join(registry):
    """A gene and the entry it encodes, named together, reach each other in
    one step: the closure derives nothing and returns their link both ways,
    and the joins hold it (Q15; review R-1)."""
    w = reference_world()
    releases = visitor()
    found_closure = closure(w.gp, w.p, releases=releases)
    assert found_closure.derivations == ()
    assert found_closure.dropped == 0
    assert set(found_closure.links) == {
        path(w.p, w.gp, w.encp),
        path(w.gp, w.p, w.encp),
    }
    found = expanded(w.gp, w.p, releases=releases)
    assert derived(found) == frozenset()
    assert join_ids(found) == [w.encp.pk]


def test_a_link_through_an_entity_the_cap_dropped_is_not_a_join(registry):
    """A link between two named seeds through an entity the cap dropped is
    not kept, so every join's ends are nodes (3.3 step 8; review R-1)."""
    m, u = mapping_release(), uniprot_release()
    g = made("gene", m)
    entries = [made("protein", u) for _ in range(MAX_EXPANDED_SEEDS)]
    i = made("protein_isoform", u)
    stated(m, *(link("encodes", g, entry) for entry in entries))
    iso = link("isoform_of", i, entries[-1])
    stated(u, iso)
    published(m, u)
    found = expanded(g, i, releases=visitor())
    assert found.dropped_derived_seeds == 2
    assert entries[-1].pk not in found.nodes
    assert iso.pk not in join_ids(found)
    assert all({j.subject_id, j.object_id} <= found.nodes for j in found.joins)


def test_each_step_reads_every_seed_in_one_statement(registry):
    w = reference_world()
    releases = visitor()
    assert cost(lambda: closure(w.g1, w.g2, w.g3, w.gp, releases=releases)) == (
        ReferenceClosure(
            (
                path(w.e, w.g1, w.enc1),
                path(w.e, w.g2, w.enc2),
                path(w.e, w.g3, w.enc3),
                path(w.i, w.g1, w.enc1, w.iso),
                path(w.i, w.g2, w.enc2, w.iso),
                path(w.i, w.g3, w.enc3, w.iso),
                path(w.p, w.gp, w.encp),
            ),
            0,
        ),
        2,
    )


# ── Visibility ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", HIDDEN_KINDS)
def test_a_hidden_mapping_release_adds_no_encodes_join(kind, registry, staff_user):
    w = reference_world(kind, staff=staff_user)
    releases = visitor()
    assert cost(lambda: closure(w.g1, releases=releases)) == (EMPTY_CLOSURE, 1)
    plain, before = cost(lambda: neighbourhood(releases=releases, seeds=[w.g1.pk]))
    found, after = cost(lambda: expanded(w.g1, releases=releases))
    assert found == plain
    assert ids(found) == [w.reg1.pk]
    assert before == 6 + BLOCK
    assert after == 7 + BLOCK


@pytest.mark.parametrize("kind", HIDDEN_KINDS)
def test_entry_isoform_joins_stay_while_the_mapping_release_is_hidden(
    kind, registry, staff_user
):
    w = reference_world(kind, staff=staff_user)
    releases = visitor()
    assert cost(lambda: closure(w.e, releases=releases)) == (
        ReferenceClosure((path(w.i, w.e, w.iso),), 0),
        1,
    )
    assert cost(lambda: closure(w.i, releases=releases)) == (
        ReferenceClosure((path(w.e, w.i, w.iso),), 0),
        2,
    )
    # No link: the gene's encodes edge is hidden.
    assert cost(lambda: closure(w.g1, w.e, releases=releases)) == (
        ReferenceClosure((path(w.i, w.e, w.iso),), 0),
        1,
    )
    found = expanded(w.e, releases=releases)
    assert join_ids(found) == [w.iso.pk]
    assert ids(found) == [w.phys.pk]
    assert found.nodes == pks(w.e, w.i, w.p)


def test_a_superseded_mapping_release_adds_nothing_to_the_default_set(registry):
    w = reference_world("superseded")
    for releases in (visitor(), staff()):
        assert closure(w.g1, releases=releases) == EMPTY_CLOSURE
        assert closure(w.e, releases=releases) == ReferenceClosure(
            (path(w.i, w.e, w.iso),), 0
        )
        assert expanded(w.g1, releases=releases) == neighbourhood(
            releases=releases, seeds=[w.g1.pk]
        )


def test_a_pinned_superseded_mapping_release_keeps_its_joins_flagged(registry):
    w = reference_world("superseded")
    pinned = [w.mapping.pk, w.uniprot.pk, w.evidence.pk]
    for releases in (visitor(pinned), staff(pinned)):
        found = expanded(w.g1, releases=releases)
        assert found.derivations == (
            path(w.e, w.g1, w.enc1),
            path(w.i, w.g1, w.enc1, w.iso),
        )
        assert [
            (j.relationship_id, j.release_ids, j.superseded_release_ids)
            for j in found.joins
        ] == [
            (w.enc1.pk, (w.mapping.pk,), (w.mapping.pk,)),
            (w.iso.pk, (w.uniprot.pk,), ()),
        ]


def test_a_hidden_seed_derives_nothing_and_costs_what_a_nonexistent_one_does(
    registry, staff_user
):
    w = reference_world("unapproved", staff=staff_user)
    releases = visitor()
    assert (
        cost(lambda: expanded(w.g3, releases=releases))
        == cost(
            lambda: neighbourhood(
                releases=releases, seeds=[w.g3.pk + 1_000_000], expand="reference"
            )
        )
        == cost(lambda: neighbourhood(releases=releases, seeds=[w.g3.pk]))
        == (EMPTY, 1 + BLOCK)
    )


# ── The cap and the row bound ───────────────────────────────────────────────


def test_an_entry_past_the_cap_keeps_the_lowest_ids_and_counts_the_rest(
    registry, monkeypatch
):
    monkeypatch.setattr(graph.neighbourhood, "STATEMENT_TIMEOUT_MS", 60_000)
    c = capped_world()
    releases = visitor()
    assert cost(lambda: closure(c.e, releases=releases)) == (
        ReferenceClosure(
            (
                path(c.g, c.e, c.enc),
                *(
                    path(iso, c.e, edge)
                    for iso, edge in zip(c.isoforms[:24], c.isoform_of[:24])
                ),
            ),
            6,
        ),
        1,
    )
    found, sent = cost(lambda: expanded(c.e, releases=releases))
    assert found.dropped_derived_seeds == 6
    assert derived(found) == pks(c.g, *c.isoforms[:24])
    assert found.nodes.isdisjoint(pks(*c.isoforms[24:]))
    assert join_ids(found) == [c.enc.pk, *(edge.pk for edge in c.isoform_of[:24])]
    assert ids(found) == [c.phys.pk, *(edge.pk for edge in c.regulation)]
    assert sent == 35 + BLOCK


def test_a_gene_past_the_cap_keeps_its_entry_before_any_isoform(registry, monkeypatch):
    monkeypatch.setattr(graph.neighbourhood, "STATEMENT_TIMEOUT_MS", 60_000)
    c = capped_world()
    releases = visitor()
    assert cost(lambda: closure(c.g, releases=releases)) == (
        ReferenceClosure(
            (
                path(c.e, c.g, c.enc),
                *(
                    path(iso, c.g, c.enc, edge)
                    for iso, edge in zip(c.isoforms[:24], c.isoform_of[:24])
                ),
            ),
            6,
        ),
        2,
    )
    found, sent = cost(lambda: expanded(c.g, releases=releases))
    assert found.dropped_derived_seeds == 6
    assert derived(found) == pks(c.e, *c.isoforms[:24])
    assert sent == 36 + BLOCK


def test_a_one_step_entity_is_kept_before_a_two_step_one_whatever_its_id(registry):
    m, u = mapping_release(), uniprot_release()
    g = made("gene", m)
    i = made("protein_isoform", u)
    e = made("protein", u)
    enc, iso = link("encodes", g, e), link("isoform_of", i, e)
    stated(m, enc)
    stated(u, iso)
    published(m, u)
    releases = visitor()
    assert i.pk < e.pk
    assert closure(g, releases=releases, limit=1) == ReferenceClosure(
        (path(e, g, enc),), 1
    )


def test_within_a_step_the_lowest_seed_keeps_its_entities_first(registry):
    u = uniprot_release()
    a, b = made("protein", u), made("protein", u)
    ib, ia = made("protein_isoform", u), made("protein_isoform", u)
    iso_a, iso_b = link("isoform_of", ia, a), link("isoform_of", ib, b)
    stated(u, iso_a, iso_b)
    published(u)
    releases = visitor()
    assert a.pk < b.pk and ib.pk < ia.pk
    assert closure(a, b, releases=releases, limit=1) == ReferenceClosure(
        (path(ia, a, iso_a),), 1
    )


def test_a_path_through_a_dropped_entity_is_not_drawn(registry, monkeypatch):
    m, u = mapping_release(), uniprot_release()
    a, b, x = made("protein", u), made("protein_isoform", u), made("protein", u)
    y = made("gene", m)
    enc_ya, enc_yx = link("encodes", y, a), link("encodes", y, x)
    iso_bx = link("isoform_of", b, x)
    stated(m, enc_ya, enc_yx)
    stated(u, iso_bx)
    published(m, u)
    releases = visitor()
    assert closure(a, b, releases=releases, limit=1) == ReferenceClosure(
        (path(y, a, enc_ya),), 1
    )
    monkeypatch.setattr(graph.neighbourhood, "MAX_EXPANDED_SEEDS", 3)
    found = expanded(a, b, releases=releases)
    assert join_ids(found) == [enc_ya.pk]
    assert all({j.subject_id, j.object_id} <= found.nodes for j in found.joins)


def test_the_second_step_runs_when_the_first_fills_the_cap(registry, monkeypatch):
    c = capped_world()
    releases = visitor()
    assert cost(lambda: closure(c.g, releases=releases, limit=1)) == (
        ReferenceClosure((path(c.e, c.g, c.enc),), 30),
        2,
    )
    assert cost(lambda: closure(c.g, releases=releases, limit=0)) == (
        ReferenceClosure((), 31),
        2,
    )
    monkeypatch.setattr(graph.neighbourhood, "MAX_EXPANDED_SEEDS", 2)
    found, sent = cost(lambda: expanded(c.g, releases=releases))
    assert found.derivations == (path(c.e, c.g, c.enc),)
    assert found.dropped_derived_seeds == 30
    assert sent == 12 + BLOCK


def test_a_statement_cut_at_its_row_bound_counts_one_drop(registry, monkeypatch):
    c = capped_world()
    releases = visitor()
    monkeypatch.setattr(graph.reference_closure, "CLOSURE_ROW_LIMIT", 2)
    limits = []
    rows = graph.reference_closure._closure_rows

    def recorded(arms, *, releases, limit):
        limits.append(limit)
        return rows(arms, releases=releases, limit=limit)

    monkeypatch.setattr(graph.reference_closure, "_closure_rows", recorded)
    assert closure(c.e, releases=releases) == ReferenceClosure(
        (
            path(c.g, c.e, c.enc),
            path(c.isoforms[0], c.e, c.isoform_of[0]),
        ),
        1,
    )
    assert closure(c.g, releases=releases) == ReferenceClosure(
        (
            path(c.e, c.g, c.enc),
            path(c.isoforms[0], c.g, c.enc, c.isoform_of[0]),
            path(c.isoforms[1], c.g, c.enc, c.isoform_of[1]),
        ),
        1,
    )
    assert expanded(c.e, releases=releases).dropped_derived_seeds == 1
    assert limits and all(limit == 2 for limit in limits)


def test_a_cut_statement_keeps_its_rows_by_the_entity_reached(registry, monkeypatch):
    u = uniprot_release()
    e = made("protein", u)
    i0, i1 = made("protein_isoform", u), made("protein_isoform", u)
    late, early = link("isoform_of", i1, e), link("isoform_of", i0, e)
    stated(u, late, early)
    published(u)
    releases = visitor()
    assert late.pk < early.pk and i0.pk < i1.pk
    monkeypatch.setattr(graph.reference_closure, "CLOSURE_ROW_LIMIT", 1)
    assert closure(e, releases=releases) == ReferenceClosure((path(i0, e, early),), 1)


def test_the_bound_holds_with_every_step_sent(
    registry, monkeypatch, django_assert_max_num_queries
):
    monkeypatch.setattr(graph.neighbourhood, "STATEMENT_TIMEOUT_MS", 60_000)
    c = capped_world()
    releases = visitor()
    seen = []
    rows = graph.neighbourhood._partner_rows

    def recorded(partners, **lookup):
        seen.append(frozenset(partners))
        return rows(partners, **lookup)

    monkeypatch.setattr(graph.neighbourhood, "_partner_rows", recorded)
    for seeds, kept, dropped, partners in (
        ([c.g], 25, 6, pks(c.p, *c.targets)),
        ([c.g, c.e, *c.targets[1:]], 16, 14, pks(c.p, c.targets[0])),
    ):
        seen.clear()
        with django_assert_max_num_queries(37 + BLOCK) as captured:
            found = expanded(*seeds, releases=releases, include_partner_edges=True)
        assert len(captured) == 37 + BLOCK
        assert len(derived(found)) == kept
        assert found.dropped_derived_seeds == dropped
        # No derived seed is a partner.
        assert seen == [partners]


def test_a_derived_seeds_lookup_takes_the_pre_limit(registry, monkeypatch):
    monkeypatch.setattr(graph.neighbourhood, "STATEMENT_TIMEOUT_MS", 60_000)
    c = capped_world()
    releases = visitor()
    found = expanded(c.e, releases=releases, candidates_per_seed=5)
    assert found.truncated_seeds == (c.g.pk,)
    assert ids(found) == [c.phys.pk, *(edge.pk for edge in c.regulation[:5])]


# ── The joins, the filters and the ranking ──────────────────────────────────


def test_the_closure_reads_the_default_filter_whatever_the_request_says(registry):
    w = reference_world()
    releases = visitor()
    for parameters, ranked, nodes, held in (
        (
            {"filters": EvidenceFilter(evidence_bases={"experimental"})},
            [w.phys, w.reg1],
            (w.g1, w.e, w.i, w.p, w.t),
            NOTHING_HIDDEN,
        ),
        (
            {"filters": EvidenceFilter(outcomes={"tested_absent"})},
            [],
            (w.g1, w.e, w.i),
            hidden(supported=2),
        ),
        (
            {"direction": "out"},
            [w.phys, w.reg1],
            (w.g1, w.e, w.i, w.p, w.t),
            NOTHING_HIDDEN,
        ),
        ({"taxon": 9606}, [], (w.g1, w.e, w.i), NOTHING_HIDDEN),
        ({"layers": frozenset({TR})}, [w.reg1], (w.g1, w.e, w.i, w.t), NOTHING_HIDDEN),
    ):
        found = expanded(w.g1, releases=releases, **parameters)
        assert found.derivations == (
            path(w.e, w.g1, w.enc1),
            path(w.i, w.g1, w.enc1, w.iso),
        ), parameters
        assert join_ids(found) == [w.enc1.pk, w.iso.pk], parameters
        assert all(j.shown and j.assertion_count == 1 for j in found.joins)
        assert ids(found) == [edge.pk for edge in ranked], parameters
        assert found.nodes == pks(*nodes), parameters
        assert found.hidden == held, parameters


def test_one_ranking_runs_over_every_looked_up_seed_and_the_joins_sit_outside_it(
    registry,
):
    w = reference_world()
    releases = visitor()
    found = expanded(w.g1, releases=releases, max_edges=1)
    # The derived entry's edge outranks the named gene's by id.
    assert ids(found) == [w.phys.pk]
    assert (found.omitted_edges, found.stopped_by) == (1, "edges")
    assert join_ids(found) == [w.enc1.pk, w.iso.pk]
    assert found.nodes == pks(w.g1, w.e, w.i, w.p)


@pytest.mark.parametrize("how", ["absent", "not shown"])
def test_a_path_whose_join_is_not_shown_is_discarded(how, registry, monkeypatch):
    w = reference_world()
    releases = visitor()
    calls = []
    summarize = graph.neighbourhood.summarize

    def joins_without_iso(relationship_ids, **kwargs):
        calls.append(list(relationship_ids))
        summaries = summarize(relationship_ids, **kwargs)
        if len(calls) == 1:
            if how == "absent":
                del summaries[w.iso.pk]
            else:
                summaries[w.iso.pk] = dataclasses.replace(
                    summaries[w.iso.pk], shown=False
                )
        return summaries

    monkeypatch.setattr(graph.neighbourhood, "summarize", joins_without_iso)
    found = expanded(w.g1, releases=releases)
    assert found.derivations == (path(w.e, w.g1, w.enc1),)
    assert join_ids(found) == [w.enc1.pk]
    assert found.nodes == pks(w.g1, w.e, w.p, w.t)
    assert found.dropped_derived_seeds == 0
    assert calls[0] == [w.enc1.pk, w.iso.pk]


def test_with_every_filter_kind_the_closure_costs_only_its_own_statements(
    registry, monkeypatch
):
    monkeypatch.setattr(graph.neighbourhood, "STATEMENT_TIMEOUT_MS", 60_000)
    w = reference_world(filtered=True)
    releases = visitor()
    assert (
        cost(
            lambda: neighbourhood(releases=releases, seeds=[w.g1.pk], filters=FILTERS)
        )[1]
        == 6 + BLOCK
    )
    found, sent = cost(lambda: expanded(w.g1, releases=releases, filters=FILTERS))
    assert sent == 13 + BLOCK
    assert ids(found) == [w.phys.pk, w.reg1.pk]
    assert all(j.shown and j.by_basis == {"unknown": 1} for j in found.joins)


# ── The deadline ────────────────────────────────────────────────────────────


def test_the_closure_checks_the_deadline_before_each_of_its_statements(
    registry, monkeypatch
):
    w = reference_world()
    releases = visitor()
    calls, sent = [], []
    monkeypatch.setattr(
        graph.reference_closure,
        "_closure_rows",
        slower(0.3, graph.reference_closure, "_closure_rows", calls),
    )
    for name in ("summarize", "_candidate_rows", "_hidden_counts", "_partner_rows"):
        monkeypatch.setattr(graph.neighbourhood, name, recorder(sent)(name))
    with pytest.raises(QueryTimedOut):
        expanded(w.g1, releases=releases, timeout_ms=200)
    # The first statement slept past the deadline, so the second step, the
    # joins and the lookups were never sent.
    assert len(calls) <= 1
    assert sent == []


def test_the_deadline_line_counts_every_seed_looked_up(registry, monkeypatch, caplog):
    w = reference_world()
    releases = visitor()
    calls = []
    monkeypatch.setattr(
        graph.neighbourhood,
        "_candidate_rows",
        slower(0.6, graph.neighbourhood, "_candidate_rows", calls),
    )
    # The one side this rests on: the seeds' statement and the two closure
    # statements finish inside the 1,000 ms deadline. Then the check before
    # the joins passes, looked_up holds the named seed and its two derived
    # seeds from there on, and the line is written at the second or third
    # lookup's check, or at the first should the joins' three statements pass
    # the deadline: the lookups' 0.6 s each make it pass by the third.
    with (
        caplog.at_level(logging.WARNING, logger="graph.neighbourhood"),
        pytest.raises(QueryTimedOut),
    ):
        expanded(w.g1, releases=releases, timeout_ms=1000)
    (line,) = caplog.messages
    assert re.fullmatch(
        r"graph network passed its deadline after \d+ ms: 3 seeds, 0 layers", line
    )


# ── Determinism and the demonstration ───────────────────────────────────────


def test_the_same_question_gets_the_same_answer(registry):
    w = reference_world()
    releases = visitor()
    assert closure(w.g1, w.e, releases=releases) == closure(
        w.e, w.g1, releases=releases
    )
    assert expanded(w.g1, w.e, releases=releases) == expanded(
        w.e, w.g1, releases=releases
    )
    assert expanded(w.i, releases=releases) == expanded(w.i, releases=releases)


def test_the_demonstrations_chain_from_each_of_its_ends(demo):
    releases = staff()
    tf1, g1, g4, p1, p1_1, p2 = (
        demo.entity(name)
        for name in ("SYN-TF1", "SYN-G1", "SYN-G4", "SYN-P1", "SYN-P1-1", "SYN-P2")
    )
    encodes = stored(demo, "encodes", "SYN-TF1", "SYN-P1")
    isoform_of = stored(demo, "isoform_of", "SYN-P1-1", "SYN-P1")
    pair = stored(demo, PI, "SYN-P1", "SYN-P2")
    tested = stored(demo, PI, "SYN-P3", "SYN-P2")
    forward = stored(demo, TR, "SYN-TF1", "SYN-G1")
    backward = stored(demo, TR, "SYN-G1", "SYN-TF1")
    unknown = stored(demo, TR, "SYN-TF1", "SYN-G4")
    for seed, derivations in (
        (tf1, (path(p1, tf1, encodes), path(p1_1, tf1, encodes, isoform_of))),
        (p1, (path(tf1, p1, encodes), path(p1_1, p1, isoform_of))),
        (p1_1, (path(tf1, p1_1, isoform_of, encodes), path(p1, p1_1, isoform_of))),
    ):
        found = expanded(seed, releases=releases)
        assert ids(found) == [
            pair.pk,
            *sorted(edge.pk for edge in (backward, forward, unknown)),
        ]
        assert join_ids(found) == [encodes.pk, isoform_of.pk]
        assert found.nodes == pks(g1, g4, tf1, p1, p1_1, p2)
        assert found.hidden == hidden(tested_absent=1)
        assert found.derivations == derivations
    assert expanded(p2, releases=releases) == neighbourhood(
        releases=releases, seeds=[p2.pk]
    )
    assert ids(expanded(p2, releases=releases)) == [pair.pk, tested.pk]
    pinned = expanded(tf1, releases=staff([demo.v1.pk]))
    assert [(j.release_ids, j.superseded_release_ids) for j in pinned.joins] == [
        ((demo.v1.pk,), (demo.v1.pk,))
    ] * 2


def test_the_demonstrations_counts(demo):
    releases = staff()
    pinned = staff([demo.v1.pk])
    tf1, p1, p1_1, p2 = (
        demo.entity(name) for name in ("SYN-TF1", "SYN-P1", "SYN-P1-1", "SYN-P2")
    )
    assert cost(lambda: expanded(tf1, releases=releases))[1] == 13 + BLOCK
    assert cost(lambda: expanded(p1, releases=releases))[1] == 12 + BLOCK
    assert cost(lambda: expanded(p1_1, releases=releases))[1] == 13 + BLOCK
    assert cost(lambda: expanded(p2, releases=releases))[1] == 7 + BLOCK
    assert cost(lambda: neighbourhood(releases=releases, seeds=[p2.pk]))[1] == 6 + BLOCK
    every = EvidenceFilter(outcomes=ALL_OUTCOMES)
    assert cost(lambda: expanded(tf1, releases=releases, filters=every))[1] == (
        12 + BLOCK
    )
    assert cost(lambda: expanded(tf1, releases=pinned))[1] == 13 + BLOCK

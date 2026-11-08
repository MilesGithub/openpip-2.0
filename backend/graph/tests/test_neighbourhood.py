"""graph.neighbourhood: the edges around a few seeds, found, ranked, capped and
summarized under the statement timeout and the deadline (architecture §4.5,
§7.3, D8), on factory rows and the bulk fixture, through visible_release_ids.

Evidence is built with api_helpers.state() or fan_out, which write the
participants that make an endpoint visible: an assertion without them would
list endpoints the entity route refuses, a world the loader cannot produce,
so no test asserts on one. The routes are tested in test_api_network and
test_api_relationships.
"""

import logging
import re
import time

import pytest
from django.db import OperationalError, ProgrammingError, connection
from django.test.utils import CaptureQueriesContext

import graph.neighbourhood
from graph.models import ContextFacet, InteractionLevel, Outcome
from graph.neighbourhood import (
    CANDIDATES_PER_SEED,
    DEFAULT_MAX_EDGES,
    Neighbourhood,
    QueryTimedOut,
    _candidate_query,
    neighbourhood,
    statement_timeout,
    timed,
)
from graph.summary import (
    ALL_OUTCOMES,
    DEFAULT_FILTER,
    ContextMatch,
    EvidenceFilter,
    ScoreRange,
)
from graph.tests.api_helpers import (
    HIDDEN_KINDS,
    fan_out,
    hidden_evidence,
    hide,
    link,
    state,
)
from graph.tests.factories import (
    EvidenceContextFactory,
    EvidenceMethodFactory,
    EvidenceScoreFactory,
    ScoreTypeFactory,
    TermFactory,
    entity_of,
    evidence_release,
    taxon_of,
)
from graph.tests.test_provenance import PUBLISHED, advance
from graph.tests.test_resolution import _plan
from graph.tests.test_summary import publish, release, visitor
from graph.visibility import ReleaseSet

TR = "transcriptional_regulation"
PI = "physical_interaction"
NOTHING_HIDDEN = {outcome: 0 for outcome in Outcome.values}
EMPTY = Neighbourhood((), (), frozenset(), NOTHING_HIDDEN, (), False, 0, 0, None)
# The timed block's own statements inside a test transaction: on PostgreSQL
# set_config and the savepoint pair, elsewhere none.
BLOCK = 3 if connection.vendor == "postgresql" else 0


def ids(found) -> list:
    return [edge.relationship_id for edge in found.edges]


def hidden(**counts) -> dict:
    return {**NOTHING_HIDDEN, **counts}


def found_ids(releases, seed, **parameters) -> list:
    return ids(neighbourhood(releases=releases, seeds=[seed.pk], **parameters))


# ── Direction, layers and taxon ─────────────────────────────────────────────


def test_direction_reads_the_stored_orientation(registry):
    ev = release()
    a, b = entity_of(), entity_of()
    forward, reverse, loop = link(TR, a, b), link(TR, b, a), link(TR, a, a)
    gene, protein = entity_of(), entity_of("protein")
    encodes = link("encodes", gene, protein)
    first, second = entity_of("protein"), entity_of("protein")
    pair = link(PI, first, second)
    # A symmetric pair is stored lower id first.
    assert (pair.subject_id, pair.object_id) == (first.pk, second.pk)
    for edge in (forward, reverse, loop, encodes):
        state(ev, edge)
    state(ev, pair, interaction_level=InteractionLevel.NOT_STATED)
    publish(ev)
    releases = visitor()
    # A directed pair is two edges, each matching one direction; a self-loop
    # matches every direction once.
    assert found_ids(releases, a, direction="out") == [forward.pk, loop.pk]
    assert found_ids(releases, a, direction="in") == [reverse.pk, loop.pk]
    assert found_ids(releases, a, direction="both") == [
        forward.pk,
        reverse.pk,
        loop.pk,
    ]
    # A role-ordered type behaves the same.
    reference = {"layers": frozenset({"encodes"})}
    assert found_ids(releases, gene, direction="out", **reference) == [encodes.pk]
    assert found_ids(releases, gene, direction="in", **reference) == []
    assert found_ids(releases, protein, direction="in", **reference) == [encodes.pk]
    assert found_ids(releases, protein, direction="out", **reference) == []
    # A symmetric pair matches from either endpoint whatever the direction:
    # here from its object side under out and its subject side under in.
    assert found_ids(releases, second, direction="out") == [pair.pk]
    assert found_ids(releases, first, direction="in") == [pair.pk]
    assert found_ids(releases, first, direction="out") == [pair.pk]
    assert found_ids(releases, second, direction="in") == [pair.pk]


def test_layers_default_to_every_non_reference_type(registry):
    ev = release()
    gene, protein, target = entity_of(), entity_of("protein"), entity_of()
    isoform = entity_of("protein_isoform")
    regulation, encodes = link(TR, gene, target), link("encodes", gene, protein)
    isoform_of = link("isoform_of", isoform, protein)
    for edge in (regulation, encodes, isoform_of):
        state(ev, edge)
    publish(ev)
    releases = visitor()
    assert found_ids(releases, gene) == [regulation.pk]
    assert found_ids(releases, protein) == []
    assert found_ids(releases, gene, layers=frozenset({"encodes"})) == [encodes.pk]
    # family:reference, as the view expands it.
    assert found_ids(
        releases, protein, layers=frozenset({"encodes", "isoform_of"})
    ) == [
        encodes.pk,
        isoform_of.pk,
    ]
    assert found_ids(releases, gene, layers=frozenset({TR, "encodes"})) == [
        regulation.pk,
        encodes.pk,
    ]


def test_taxon_keeps_the_partners_of_that_taxon(registry):
    ev = release()
    seed = entity_of(taxid=9606)
    human, fly = entity_of(taxid=9606), entity_of(taxid=7227)
    to_human, to_fly = link(TR, seed, human), link(TR, seed, fly)
    loop = link(TR, seed, seed)
    foreign = entity_of(taxid=7227)
    from_foreign = link(TR, foreign, human)
    for edge in (to_human, to_fly, loop, from_foreign):
        state(ev, edge)
    # A tested-absent edge to another fly: hidden, and counted unless the
    # taxon leaves its partner out.
    against_fly = link(TR, seed, entity_of(taxid=7227))
    state(ev, against_fly, outcome=Outcome.TESTED_ABSENT)
    publish(ev)
    releases = visitor()
    humans = neighbourhood(releases=releases, seeds=[seed.pk], taxon=9606)
    assert ids(humans) == [to_human.pk, loop.pk]
    assert humans.nodes == {seed.pk, human.pk}
    assert humans.hidden == NOTHING_HIDDEN
    # The seed stays a node whatever its own taxon, and its self-loop is kept
    # only when it is of the taxon.
    flies = neighbourhood(releases=releases, seeds=[seed.pk], taxon=7227)
    assert ids(flies) == [to_fly.pk]
    assert (flies.seeds, flies.nodes) == ((seed.pk,), {seed.pk, fly.pk})
    assert flies.hidden == hidden(tested_absent=1)
    # A seed of another taxon lists its partners of this one.
    assert found_ids(releases, foreign, taxon=9606) == [from_foreign.pk]
    every = neighbourhood(releases=releases, seeds=[seed.pk])
    assert ids(every) == [to_human.pk, to_fly.pk, loop.pk]
    assert every.hidden == hidden(tested_absent=1)


# ── Ranking, the pre-limit and the caps ─────────────────────────────────────


def test_the_ranking_counts_covered_assertions_and_breaks_ties_by_id(registry):
    ev = release()
    seed = entity_of()
    a, b, c, d, e = (link(TR, seed, entity_of()) for _ in range(5))
    for _ in range(2):
        state(ev, a, effect="activating")
    state(ev, b, effect="activating")
    for _ in range(2):
        state(ev, b, effect="inhibiting")
    for _ in range(2):
        state(ev, c, outcome=Outcome.TESTED_ABSENT)
    state(ev, d)
    state(ev, e)
    publish(ev)
    releases = visitor()
    # Three supporting assertions rank above two and two above one; ties by
    # ascending id; the tested-absent-only edge is absent by default.
    found = neighbourhood(releases=releases, seeds=[seed.pk])
    assert ids(found) == [b.pk, a.pk, d.pk, e.pk]
    assert [edge.assertion_count for edge in found.edges] == [3, 2, 1, 1]
    # Under a filter the matching counts rank, and only matching assertions
    # count: d and e state no effect.
    activating = EvidenceFilter(effects={"activating"})
    assert found_ids(releases, seed, filters=activating) == [a.pk, b.pk]
    absent = neighbourhood(
        releases=releases,
        seeds=[seed.pk],
        filters=EvidenceFilter(outcomes={"tested_absent"}),
    )
    assert ids(absent) == [c.pk]
    (edge,) = absent.edges
    assert (edge.shown, edge.assertion_count, edge.effect) == (True, 0, None)
    assert edge.outcome_counts["tested_absent"] == 2


def test_the_pre_limit_keeps_each_seeds_most_covered_candidates(registry):
    ev = release()
    seed = entity_of()
    singles = fan_out(seed, CANDIDATES_PER_SEED, ev)
    doubles = fan_out(seed, 5, ev, assertions_per_edge=2)
    publish(ev)
    releases = visitor()
    found = neighbourhood(releases=releases, seeds=[seed.pk])
    assert found.truncated_seeds == (seed.pk,)
    ranked = ids(found)
    assert len(ranked) == CANDIDATES_PER_SEED
    # The doubles are ranked although their ids are the highest; the five
    # highest-id singles are not.
    assert ranked[:5] == [edge.pk for edge in doubles]
    assert ranked[5:] == [edge.pk for edge in singles[:-5]]
    assert [edge.assertion_count for edge in found.edges[4:6]] == [2, 1]
    exact = neighbourhood(
        releases=releases, seeds=[seed.pk], candidates_per_seed=CANDIDATES_PER_SEED + 5
    )
    assert exact.truncated_seeds == ()
    assert len(exact.edges) == CANDIDATES_PER_SEED + 5


def test_the_edge_cap_ends_the_walk(registry):
    ev = release()
    seed = entity_of()
    edges = [link(TR, seed, entity_of()) for _ in range(4)]
    for edge in edges:
        state(ev, edge)
    publish(ev)
    releases = visitor()
    found = neighbourhood(releases=releases, seeds=[seed.pk], max_edges=2)
    assert ids(found) == [edges[0].pk, edges[1].pk]
    assert (found.omitted_edges, found.omitted_for_nodes, found.stopped_by) == (
        2,
        0,
        "edges",
    )
    whole = neighbourhood(releases=releases, seeds=[seed.pk], max_edges=4)
    assert (len(whole.edges), whole.omitted_edges, whole.stopped_by) == (4, 0, None)


def test_the_node_cap_refuses_an_edge_without_ending_the_walk(registry):
    ev = release()
    first, second = entity_of(), entity_of()
    x, y, z = entity_of(), entity_of(), entity_of()
    best, next_best = link(TR, first, x), link(TR, first, y)
    shared, last = link(TR, second, x), link(TR, second, z)
    for _ in range(3):
        state(ev, best)
    for _ in range(2):
        state(ev, next_best)
    state(ev, shared)
    state(ev, last)
    publish(ev)
    releases = visitor()
    seeds = [first.pk, second.pk]
    # Seeds count as nodes. With room for one more, the best edge adds x; the
    # next would add y and is refused; the shared edge, whose partner is a
    # node through the other seed, is kept; the last would add z.
    found = neighbourhood(releases=releases, seeds=seeds, max_nodes=3)
    assert ids(found) == [best.pk, shared.pk]
    assert found.nodes == {first.pk, second.pk, x.pk}
    assert (found.omitted_edges, found.omitted_for_nodes, found.stopped_by) == (
        2,
        2,
        "nodes",
    )
    # With the edge cap met by the shared edge, the last is refused for edges.
    mixed = neighbourhood(releases=releases, seeds=seeds, max_nodes=3, max_edges=2)
    assert ids(mixed) == [best.pk, shared.pk]
    assert (mixed.omitted_edges, mixed.omitted_for_nodes, mixed.stopped_by) == (
        2,
        1,
        "edges",
    )
    alone = neighbourhood(releases=releases, seeds=seeds, max_nodes=2)
    assert (ids(alone), alone.nodes) == ([], {first.pk, second.pk})
    assert (alone.omitted_edges, alone.omitted_for_nodes, alone.stopped_by) == (
        4,
        4,
        "nodes",
    )


def test_an_edge_between_two_seeds_is_one_edge(registry):
    ev = release()
    a, b = entity_of(), entity_of()
    between, other = link(TR, a, b), link(TR, a, entity_of())
    state(ev, between)
    state(ev, other)
    publish(ev)
    found = neighbourhood(releases=visitor(), seeds=[a.pk, b.pk])
    assert ids(found) == [between.pk, other.pk]
    assert found.nodes == {a.pk, b.pk, other.object_id}


# ── Hidden relationships ────────────────────────────────────────────────────


def test_hidden_relationships_are_counted_per_outcome(registry):
    ev = release()
    seed = entity_of()
    sa, sb, sc, sd = (link(TR, seed, entity_of()) for _ in range(4))
    for _ in range(3):
        state(ev, sa)
    state(ev, sb)
    state(ev, sc, outcome=Outcome.TESTED_ABSENT)
    state(ev, sc, outcome=Outcome.INCONCLUSIVE)
    state(ev, sd, outcome=Outcome.NOT_SIGNIFICANT)
    publish(ev)
    releases = visitor()
    plain = neighbourhood(releases=releases, seeds=[seed.pk])
    assert ids(plain) == [sa.pk, sb.pk]
    # A hidden relationship with evidence of two outcomes counts under both.
    assert plain.hidden == hidden(tested_absent=1, inconclusive=1, not_significant=1)
    assert plain.nodes == {seed.pk, sa.object_id, sb.object_id}
    # Under tested_absent the supported-only edges are hidden, and sc, now a
    # candidate, is not counted.
    absent = neighbourhood(
        releases=releases,
        seeds=[seed.pk],
        filters=EvidenceFilter(outcomes={"tested_absent"}),
    )
    assert ids(absent) == [sc.pk]
    assert absent.hidden == hidden(supported=2, not_significant=1)
    # Every outcome hides nothing, and sends no aggregate.
    with CaptureQueriesContext(connection) as every_outcome:
        every = neighbourhood(
            releases=releases,
            seeds=[seed.pk],
            filters=EvidenceFilter(outcomes=ALL_OUTCOMES),
        )
    assert ids(every) == [sa.pk, sc.pk, sb.pk, sd.pk]
    assert every.hidden == NOTHING_HIDDEN
    with CaptureQueriesContext(connection) as default:
        neighbourhood(releases=releases, seeds=[seed.pk])
    assert len(default) == len(every_outcome) + 1


def test_the_screen_case_ranks_every_supported_edge_and_counts_the_rest(registry):
    """2,800 tested-absent-only pairs written first, with the lowest ids, then
    200 supported pairs: the pre-limit sees only relationships with covered
    evidence, so every supported edge is ranked and the hidden count is exact."""
    ev = release()
    screen = entity_of()
    fan_out(screen, 2800, ev, outcome=Outcome.TESTED_ABSENT)
    supported = fan_out(screen, 200, ev)
    publish(ev)
    found = neighbourhood(releases=visitor(), seeds=[screen.pk])
    assert ids(found) == [edge.pk for edge in supported]
    assert all(edge.shown for edge in found.edges)
    assert found.truncated_seeds == ()
    assert found.hidden == hidden(tested_absent=2800)


# ── Partner edges ───────────────────────────────────────────────────────────


def test_partner_edges_are_added_among_the_partners_only_when_asked(registry):
    ev = release()
    seed = entity_of()
    a, b, c, x = (entity_of() for _ in range(4))
    sa, sb, sc = link(TR, seed, a), link(TR, seed, b), link(TR, seed, c)
    ab, ba, aa = link(TR, a, b), link(TR, b, a), link(TR, a, a)
    bc, ax = link(TR, b, c), link(TR, a, x)
    # The self-loop, the partner edge with the highest id, is the most
    # covered: it ranks first among them, and the edge cap and the pre-limit
    # keep it.
    for _ in range(2):
        state(ev, sa)
        state(ev, aa)
    for edge in (sb, sc, ab, ba, ax):
        state(ev, edge)
    state(ev, bc, outcome=Outcome.TESTED_ABSENT)
    publish(ev)
    releases = visitor()
    plain = neighbourhood(releases=releases, seeds=[seed.pk], direction="out")
    assert ids(plain) == [sa.pk, sb.pk, sc.pk]
    with CaptureQueriesContext(connection) as asked:
        found = neighbourhood(
            releases=releases,
            seeds=[seed.pk],
            direction="out",
            include_partner_edges=True,
        )
    # Among the partners, either way round and a self-loop, after the seed's
    # edges and in rank order; not to x, which is no node; not the hidden
    # b-c, which is not counted either.
    assert ids(found) == [sa.pk, sb.pk, sc.pk, aa.pk, ab.pk, ba.pk]
    assert found.nodes == plain.nodes
    assert found.hidden == plain.hidden == NOTHING_HIDDEN
    assert (found.omitted_edges, found.stopped_by) == (0, None)
    assert found.partner_candidates_truncated is False
    cut = neighbourhood(
        releases=releases, seeds=[seed.pk], include_partner_edges=True, max_edges=4
    )
    assert ids(cut) == [sa.pk, sb.pk, sc.pk, aa.pk]
    assert (cut.omitted_edges, cut.omitted_for_nodes, cut.stopped_by) == (
        2,
        0,
        "edges",
    )
    # Pre-limited: the seed keeps sa and sb, so the partners are a and b, and
    # the two most covered of their three edges are ranked, not the two
    # lowest ids.
    few = neighbourhood(
        releases=releases,
        seeds=[seed.pk],
        include_partner_edges=True,
        candidates_per_seed=2,
    )
    assert ids(few) == [sa.pk, sb.pk, aa.pk, ab.pk]
    assert few.truncated_seeds == (seed.pk,)
    assert few.partner_candidates_truncated is True
    # Not sent when the walk kept max_edges edges.
    with CaptureQueriesContext(connection) as full:
        stopped = neighbourhood(
            releases=releases, seeds=[seed.pk], include_partner_edges=True, max_edges=3
        )
    assert ids(stopped) == [sa.pk, sb.pk, sc.pk]
    assert (stopped.omitted_edges, stopped.stopped_by) == (0, None)
    assert len(full) == len(asked) - 1


# ── Visibility ──────────────────────────────────────────────────────────────


def test_an_invisible_seed_is_dropped_without_a_word(registry):
    unseen = evidence_release(records=0, approved=False)
    lonely = entity_of()
    state(unseen, link(TR, lonely, entity_of()))
    advance(unseen.release, PUBLISHED)
    releases = visitor()
    with CaptureQueriesContext(connection) as hidden_cost:
        dropped = neighbourhood(releases=releases, seeds=[lonely.pk])
    assert dropped == EMPTY
    # A hidden seed costs what a nonexistent one does.
    with CaptureQueriesContext(connection) as nowhere_cost:
        nowhere = neighbourhood(releases=releases, seeds=[lonely.pk + 1_000_000])
    assert nowhere == EMPTY
    assert len(nowhere_cost) == len(hidden_cost) == 1 + BLOCK
    # A ref beyond 64 bits asks for no row.
    with CaptureQueriesContext(connection) as beyond:
        assert neighbourhood(releases=releases, seeds=[2**63, -(2**63) - 1]) == EMPTY
    assert [query for query in beyond if "graph_entity" in query["sql"]] == []


def test_a_release_set_built_by_hand_is_refused(registry):
    with pytest.raises(TypeError):
        neighbourhood(releases=ReleaseSet(frozenset(), frozenset()), seeds=[1])
    with pytest.raises(TypeError):
        neighbourhood(
            releases=visitor(), seeds=[1], filters={"outcomes": {"supported"}}
        )


def test_caller_bugs_are_refused(registry):
    releases = visitor()
    with pytest.raises(ValueError):
        neighbourhood(releases=releases, seeds=[1], direction="sideways")
    for name in ("max_edges", "candidates_per_seed", "max_nodes", "timeout_ms"):
        with pytest.raises(ValueError):
            neighbourhood(releases=releases, seeds=[1], **{name: 0})


def test_the_switch_off_returns_the_empty_result_with_no_query(registry, settings):
    ev = release()
    seed = entity_of()
    state(ev, link(TR, seed, entity_of()))
    publish(ev)
    releases = visitor()
    settings.TYPED_GRAPH_ENABLED = False
    with CaptureQueriesContext(connection) as queries:
        assert neighbourhood(releases=releases, seeds=[seed.pk]) == EMPTY
    assert len(queries) == 0


@pytest.mark.parametrize("kind", HIDDEN_KINDS)
def test_a_hidden_release_states_nothing(kind, registry, staff_user):
    """Each kind's state is the only thing hiding it from a visitor
    (api_helpers.HIDDEN_KINDS)."""
    concealed = hidden_evidence(kind)
    shown = release()
    seed = entity_of()
    a, b, c, d = (link(TR, seed, entity_of()) for _ in range(4))
    state(shown, a)
    for _ in range(2):
        state(shown, b)
    for _ in range(5):
        state(concealed, a)
    state(concealed, a, outcome=Outcome.TESTED_ABSENT)
    state(concealed, c)
    state(concealed, d, outcome=Outcome.TESTED_ABSENT)
    hide(concealed, kind, staff_user)
    publish(shown)
    found = neighbourhood(releases=visitor(), seeds=[seed.pk])
    # b outranks a: the hidden rows count for nothing, and a's hidden evidence
    # against it is not counted; c and d are absent, and d is not hidden.
    assert ids(found) == [b.pk, a.pk]
    assert [edge.assertion_count for edge in found.edges] == [2, 1]
    assert found.edges[1].outcome_counts == {
        "supported": 1,
        "tested_absent": 0,
        "inconclusive": 0,
        "not_significant": 0,
    }
    assert found.nodes == {seed.pk, a.object_id, b.object_id}
    assert found.hidden == NOTHING_HIDDEN


# ── Queries ─────────────────────────────────────────────────────────────────


def test_the_query_count_is_independent_of_the_degree(registry):
    ev = release()
    one, hub = entity_of(), entity_of()
    fan_out(one, 1, ev)
    fan_out(hub, 3000, ev)
    publish(ev)
    releases = visitor()
    counts = []
    for seed in (one, hub):
        with CaptureQueriesContext(connection) as queries:
            found = neighbourhood(releases=releases, seeds=[seed.pk])
        counts.append(len(queries))
        assert found.edges and found.hidden == NOTHING_HIDDEN
    # The seeds, the seed's lookup, the hidden aggregate and summarize's three.
    assert counts == [6 + BLOCK] * 2
    assert len(found.edges) == CANDIDATES_PER_SEED <= DEFAULT_MAX_EDGES
    assert found.truncated_seeds == (hub.pk,)
    assert (found.omitted_edges, found.stopped_by) == (0, None)


def rich(made, other, cell):
    """Every filter kind on one assertion."""
    EvidenceMethodFactory(assertion=made)
    EvidenceScoreFactory(assertion=made)
    EvidenceScoreFactory(assertion=made, score_type=other, raw_value="0.7", value=0.7)
    EvidenceContextFactory(assertion=made, raw_value="liver")
    EvidenceContextFactory(
        assertion=made,
        facet=ContextFacet.HOST_TAXON,
        taxon=taxon_of(9606),
        raw_value="9606",
    )
    EvidenceContextFactory(
        assertion=made, facet=ContextFacet.CELL_LINE, term=cell, raw_value="HeLa"
    )
    EvidenceContextFactory(assertion=made, facet=ContextFacet.OTHER, raw_value="x")


def test_ten_seeds_with_every_filter_kind_cost_the_enumerated_statements(registry):
    ev = release()
    other = ScoreTypeFactory(slug="stats:test_other")
    cell = TermFactory(curie="MI:9002")
    seeds = [entity_of() for _ in range(10)]
    partners = [entity_of() for _ in range(10)]
    for seed, partner in zip(seeds, partners):
        rich(state(ev, link(TR, seed, partner), effect="activating"), other, cell)
    rich(state(ev, link(TR, partners[0], partners[1])), other, cell)
    publish(ev)
    filters = EvidenceFilter(
        outcomes={"supported", "tested_absent"},
        evidence_bases={"experimental"},
        effects={"activating", "not_stated"},
        detection_methods={"MI:0018"},
        levels={"association"},
        scores=(
            ScoreRange("stats:test_value", 0, 1),
            ScoreRange("stats:test_other", None, None),
        ),
        contexts=(
            ContextMatch("tissue", raw="liver"),
            ContextMatch("host_taxon", taxid=9606),
            ContextMatch("cell_line", term="MI:9002"),
            ContextMatch("other", raw="x"),
        ),
    )
    releases = visitor()
    with CaptureQueriesContext(connection) as queries:
        found = neighbourhood(
            releases=releases,
            seeds=[seed.pk for seed in seeds],
            direction="out",
            filters=filters,
            taxon=9606,
            include_partner_edges=True,
        )
    assert len(found.edges) == 11
    assert found.hidden == NOTHING_HIDDEN
    # The seeds, ten lookups, the hidden aggregate, the partner lookup and
    # summarize's three.
    assert len(queries) == 16 + BLOCK


# ── The timeout and the deadline ────────────────────────────────────────────


@pytest.mark.postgres
def test_a_statement_past_the_timeout_is_cancelled(db, caplog):
    with pytest.raises(OperationalError) as raised:
        with statement_timeout(20):
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_sleep(1)")
    assert raised.value.__cause__.pgcode == "57014"
    # Caught outside the block, which rolled back: the connection serves the
    # next statement.
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        assert cursor.fetchone() == (1,)
    with pytest.raises(QueryTimedOut):
        with timed(20, what="probe"):
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_sleep(1)")
    # Another database error is not a timeout: one of another class, an
    # OperationalError of another SQLSTATE (repeat()'s length check, raised
    # before anything is allocated), and one with none, as a lost
    # connection's, all leave the block unchanged, and log nothing.
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="graph.neighbourhood"):
        with pytest.raises(ProgrammingError):
            with timed(20):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT * FROM no_such_table_here")
        with pytest.raises(OperationalError) as raised:
            with timed(20):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT repeat('xx', 1073741824)")
        assert raised.value.__cause__.pgcode == "54000"
        with pytest.raises(OperationalError):
            with timed(20):
                raise OperationalError("server closed the connection unexpectedly")
    assert caplog.messages == []


@pytest.mark.postgres
def test_a_cancelled_lookup_ends_the_network_as_a_timeout(
    registry, monkeypatch, caplog
):
    ev = release()
    seed = entity_of()
    edge = link(TR, seed, entity_of())
    state(ev, edge)
    publish(ev)
    releases = visitor()
    rows = graph.neighbourhood._candidate_rows

    def slow(seed, **lookup):
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_sleep(0.2)")
        return rows(seed, **lookup)

    monkeypatch.setattr(graph.neighbourhood, "_candidate_rows", slow)
    monkeypatch.setattr(graph.neighbourhood, "STATEMENT_TIMEOUT_MS", 20)
    with caplog.at_level(logging.WARNING, logger="graph.neighbourhood"):
        with pytest.raises(QueryTimedOut):
            neighbourhood(releases=releases, seeds=[seed.pk])
    assert caplog.messages == ["graph network timed out after 20 ms"]
    # The connection is usable afterwards.
    monkeypatch.setattr(graph.neighbourhood, "_candidate_rows", rows)
    assert found_ids(releases, seed, timeout_ms=5000) == [edge.pk]


def test_the_deadline_stops_the_lookups(registry, monkeypatch, caplog):
    ev = release()
    seeds = [entity_of() for _ in range(10)]
    for seed in seeds:
        state(ev, link(TR, seed, entity_of()))
    publish(ev)
    releases = visitor()
    rows = graph.neighbourhood._candidate_rows
    calls = []

    def slow(seed, **lookup):
        calls.append(seed)
        time.sleep(0.3)
        return rows(seed, **lookup)

    monkeypatch.setattr(graph.neighbourhood, "_candidate_rows", slow)
    with caplog.at_level(logging.WARNING, logger="graph.neighbourhood"):
        with pytest.raises(QueryTimedOut):
            neighbourhood(
                releases=releases, seeds=[seed.pk for seed in seeds], timeout_ms=1000
            )
    # The clock passes one second between the third and the fifth lookup
    # whatever the runner's speed.
    assert 3 <= len(calls) <= 4
    (line,) = caplog.messages
    assert re.fullmatch(
        r"graph network passed its deadline after \d+ ms: 10 seeds, 0 layers", line
    )


@pytest.mark.parametrize(
    "outcomes,partners",
    [
        # The check before the hidden aggregate, before the partner lookup,
        # and before summarize: each case's first statement after the lookup.
        (DEFAULT_FILTER.outcomes, False),
        (ALL_OUTCOMES, True),
        (ALL_OUTCOMES, False),
    ],
)
def test_no_statement_is_sent_past_the_deadline(
    outcomes, partners, registry, monkeypatch
):
    ev = release()
    seed, a, b = entity_of(), entity_of(), entity_of()
    edges = [link(TR, seed, a), link(TR, seed, b), link(TR, a, b)]
    for edge in edges:
        state(ev, edge)
    publish(ev)
    releases = visitor()
    asked = {
        "releases": releases,
        "seeds": [seed.pk],
        "filters": EvidenceFilter(outcomes=outcomes),
        "include_partner_edges": partners,
    }
    # Unhurried, the case reaches its step.
    expected = edges if partners else edges[:2]
    assert ids(neighbourhood(**asked)) == [edge.pk for edge in expected]
    rows, sent = graph.neighbourhood._candidate_rows, []

    def slow(seed, **lookup):
        time.sleep(0.3)
        return rows(seed, **lookup)

    def recorded(name):
        function = getattr(graph.neighbourhood, name)

        def record(*args, **kwargs):
            sent.append(name)
            return function(*args, **kwargs)

        return record

    monkeypatch.setattr(graph.neighbourhood, "_candidate_rows", slow)
    for name in ("_hidden_counts", "_partner_rows", "summarize"):
        monkeypatch.setattr(graph.neighbourhood, name, recorded(name))
    with pytest.raises(QueryTimedOut):
        neighbourhood(**asked, timeout_ms=200)
    assert sent == []


def test_the_timeout_is_set_on_postgresql_alone(db):
    with CaptureQueriesContext(connection) as queries:
        with statement_timeout(20):
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
    set_config = [query for query in queries if "set_config" in query["sql"]]
    assert len(set_config) == (1 if connection.vendor == "postgresql" else 0)
    assert len(queries) == 1 + BLOCK


@pytest.mark.postgres
@pytest.mark.django_db(transaction=True)
class TestTheTimeoutDoesNotPersist:
    """SET LOCAL ends with its transaction, which only a real transaction
    shows: under the plain db fixture the block is a savepoint inside the
    test's transaction, and a completed subtransaction's SET LOCAL lasts to
    the test's end."""

    def test_the_session_default_holds_after_a_completed_and_a_fired_block(
        self, visitor_client
    ):
        def shown():
            with connection.cursor() as cursor:
                cursor.execute("SHOW statement_timeout")
                return cursor.fetchone()[0]

        with statement_timeout(20):
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
        assert shown() == "0"
        with pytest.raises(QueryTimedOut):
            with timed(20):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_sleep(1)")
        assert shown() == "0"
        # The same connection serves a legacy request with no timeout in force.
        assert visitor_client.get("/api/search?q=x").status_code == 200


# ── The plan ────────────────────────────────────────────────────────────────


@pytest.mark.postgres
def test_the_candidate_statement_reads_indexes(registry):
    ev = release()
    hub = entity_of()
    fan_out(hub, 3000, ev)
    # The plan rests on fan_out's statistics: without them it depends on the
    # dead rows earlier tests left. pg_stats rolls back with each test, so no
    # earlier test can leave it filled; pg_class.reltuples does not.
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT DISTINCT tablename FROM pg_stats WHERE tablename IN"
            " ('graph_relationship', 'graph_evidenceassertion')"
        )
        analyzed = {name for (name,) in cursor.fetchall()}
    assert analyzed == {"graph_relationship", "graph_evidenceassertion"}
    publish(ev)
    releases = visitor()
    for layers in (None, frozenset({TR})):
        plan = _plan(
            _candidate_query(
                hub.pk,
                releases=releases,
                filters=DEFAULT_FILTER,
                layers=layers,
                direction="both",
                taxon=None,
                limit=CANDIDATES_PER_SEED,
            )
        )
        assert "Seq Scan on graph_relationship" not in plan, plan
        assert "Seq Scan on graph_evidenceassertion" not in plan, plan
        assert plan.startswith("Limit"), plan
        # The count's SubPlan reads an index led by the relationship; which
        # index serves the endpoint condition is the planner's choice.
        subplan = plan[plan.index("SubPlan") :]
        assert re.search(
            r"Index (?:Only )?Scan using "
            r"(graph_evidenceassertion_relationship_id\w*|graph_assert_rel_release)",
            subplan,
        ), plan


# ── Determinism ─────────────────────────────────────────────────────────────


def test_two_calls_give_the_same_result(registry):
    ev = release()
    seed = entity_of()
    partners = [entity_of() for _ in range(3)]
    for partner in partners:
        state(ev, link(TR, seed, partner))
    state(ev, link(TR, partners[0], partners[1]))
    publish(ev)
    releases = visitor()
    first = neighbourhood(
        releases=releases, seeds=[seed.pk], include_partner_edges=True
    )
    second = neighbourhood(
        releases=releases, seeds=[seed.pk], include_partner_edges=True
    )
    assert first == second
    assert len(first.edges) == 4

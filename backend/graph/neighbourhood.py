"""The neighbourhood of a few seeds: the edges around them, found, ranked,
capped and summarized through graph.summary (architecture §4.5, §7.3, D8).

Every read goes through the ReleaseSet visible_release_ids issued, so an
unapproved, embargoed, withdrawn or discarded release never shapes a network,
and a set built by hand is refused. A candidate of a seed is a relationship of
the layers that matches the direction (and, when one is given, whose partner
is of the taxon) and has a covered assertion: one that passes every filter and
has an outcome the filter asks for. Its ranking count is the number of its
covered assertions. A hidden relationship matches the same predicates but has
only evidence of other outcomes; it is never ranked, and is counted per
outcome in one aggregate over every candidate of the request.

Statements, in order: the visible seeds; one per visible seed, its candidates
the most covered first and ties to the lowest id, cut by a SQL LIMIT; one
aggregate of the hidden relationships unless every outcome is asked for; one
for the edges among the partners when asked; summarize's three. A step whose
input is empty sends nothing. So the count is bounded by the number of seeds
and independent of any seed's degree.

On PostgreSQL every statement runs under a statement timeout, and the service
sends no further statement once the timeout's span has passed since its first
(the deadline), so a request ends within about twice the timeout. Both end as
QueryTimedOut, which the API answers as a 503. The timeout helpers are public:
the relationship routes, the exports and the reference expansion use them too.

This module imports nothing from graph.api, which re-exports its constants
(graph.api.limits), so each number lives once. While the switch is off,
neighbourhood() returns the empty result with no query.
"""

import dataclasses
import logging
import time
from contextlib import contextmanager

from django.db import OperationalError, connection, transaction
from django.db.models import Count, Exists, IntegerField, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce

from .entity_visibility import visible_entity_q
from .models import Entity, Outcome, Relationship
from .preconditions import storable, typed_graph_enabled
from .summary import (
    ALL_OUTCOMES,
    DEFAULT_FILTER,
    EdgeSummary,
    covered_assertions,
    summarize,
)
from .visibility import issued_release_set

logger = logging.getLogger(__name__)

# Candidates ranked per seed: its most covered relationships.
CANDIDATES_PER_SEED = 200
# Nodes a network holds, seeds included.
MAX_NODES = 500
# Edges a network holds unless asked, and the most it may be asked for.
DEFAULT_MAX_EDGES = 250
MAX_EDGES = 1000
# Each statement's bound on PostgreSQL, and the span of the request's deadline.
STATEMENT_TIMEOUT_MS = 5000
DIRECTIONS = ("out", "in", "both")
# PostgreSQL's SQLSTATE for a statement cancelled by the timeout.
QUERY_CANCELED = "57014"


@dataclasses.dataclass(frozen=True)
class Neighbourhood:
    """What the service found. `edges` are the kept summaries in rank order,
    the seeds' edges then the partners'; `nodes` every endpoint of a kept edge
    plus every visible seed; `hidden` {outcome: hidden relationships}, every
    outcome a key; `truncated_seeds` the seeds whose lookup the pre-limit cut;
    `omitted_edges` every ranked candidate a cap refused and
    `omitted_for_nodes` those among them the node cap refused; `stopped_by`
    "edges", "nodes" or None."""

    seeds: tuple[int, ...]
    edges: tuple[EdgeSummary, ...]
    nodes: frozenset[int]
    hidden: dict[str, int]
    truncated_seeds: tuple[int, ...]
    partner_candidates_truncated: bool
    omitted_edges: int
    omitted_for_nodes: int
    stopped_by: str | None


class QueryTimedOut(Exception):
    """A statement ran past the timeout on PostgreSQL, or the request passed
    its deadline."""


@contextmanager
def statement_timeout(milliseconds):
    """SET LOCAL statement_timeout for the block, on PostgreSQL only, inside
    transaction.atomic() so that it ends with the block, committed or rolled
    back (D8). On another database the block runs with no timeout."""
    if connection.vendor != "postgresql":
        yield
        return
    with transaction.atomic():
        with connection.cursor() as cursor:
            # set_config is SET LOCAL as a function call, which takes a bind
            # parameter; SET itself does not.
            cursor.execute(
                "SELECT set_config('statement_timeout', %s, true)",
                [str(int(milliseconds))],
            )
        yield


@contextmanager
def timed(timeout_ms=None, *, what="network"):
    """statement_timeout() plus the mapping of a cancellation to QueryTimedOut
    and one warning line, which holds counts only. None reads
    STATEMENT_TIMEOUT_MS when called. The cancellation is caught outside the
    atomic block, which has rolled back, so the connection stays usable; any
    other OperationalError propagates. The relationship routes use it too."""
    milliseconds = STATEMENT_TIMEOUT_MS if timeout_ms is None else timeout_ms
    if milliseconds < 1:
        raise ValueError("timeout_ms must be at least 1")
    try:
        with statement_timeout(milliseconds):
            yield
    except OperationalError as error:
        if getattr(error.__cause__, "pgcode", None) != QUERY_CANCELED:
            raise
        logger.warning("graph %s timed out after %d ms", what, milliseconds)
        raise QueryTimedOut() from error


def neighbourhood(
    *,
    releases,
    seeds,
    layers=None,
    direction="both",
    filters=DEFAULT_FILTER,
    taxon=None,
    include_partner_edges=False,
    max_edges=DEFAULT_MAX_EDGES,
    candidates_per_seed=CANDIDATES_PER_SEED,
    max_nodes=MAX_NODES,
    timeout_ms=None,
) -> Neighbourhood:
    """The network around `seeds` (entity ids) under `releases`, a ReleaseSet
    visible_release_ids issued, and `filters`, an EvidenceFilter whose values
    the caller has validated. `layers` is a frozenset of relationship type
    slugs, or None for every non-reference type; `direction` one of
    DIRECTIONS; `taxon` a taxid every partner must have, or None. A seed the
    caller may not see, or that names nothing, is dropped without a word. The
    caps and the timeout are the module's unless given; a value below 1 or an
    unknown direction is a caller bug (ValueError).

    Ranked candidates are walked most covered first, ties by ascending id: an
    edge is kept while fewer than `max_edges` are kept and it leaves at most
    `max_nodes` nodes. The first edge refused for `max_edges` ends the walk;
    one refused for `max_nodes` is omitted and the walk goes on, so every
    edge between two kept nodes that the edge budget allows is kept. Partner
    edges, when asked for, follow in rank order while the edge budget has
    room. Every statement runs under timed(); before each one after the first
    the deadline is checked, and past it QueryTimedOut is raised without
    sending it.
    """
    releases = issued_release_set(releases)
    # Refuses a filter that is no EvidenceFilter before any statement.
    covered_assertions(releases=releases, filters=filters)
    if direction not in DIRECTIONS:
        raise ValueError(
            f"direction is one of {', '.join(DIRECTIONS)}; not {direction!r}."
        )
    for name, value in (
        ("max_edges", max_edges),
        ("candidates_per_seed", candidates_per_seed),
        ("max_nodes", max_nodes),
    ):
        if value < 1:
            raise ValueError(f"{name} is at least 1, not {value!r}.")
    milliseconds = STATEMENT_TIMEOUT_MS if timeout_ms is None else timeout_ms
    if milliseconds < 1:
        raise ValueError("timeout_ms must be at least 1")
    hidden = {outcome: 0 for outcome in Outcome.values}
    if not typed_graph_enabled():
        return Neighbourhood((), (), frozenset(), hidden, (), False, 0, 0, None)
    lookup = dict(releases=releases, filters=filters, layers=layers, taxon=taxon)
    with timed(milliseconds):
        started = time.monotonic()
        visible = tuple(
            sorted(
                Entity.objects.filter(pk__in=storable(set(seeds)))
                .filter(visible_entity_q(releases))
                .values_list("pk", flat=True)
            )
        )

        def check_deadline():
            elapsed = time.monotonic() - started
            if elapsed > milliseconds / 1000:
                logger.warning(
                    "graph network passed its deadline after %d ms: %d seeds, "
                    "%d layers",
                    elapsed * 1000,
                    len(visible),
                    0 if layers is None else len(layers),
                )
                raise QueryTimedOut()

        candidates, truncated = {}, []
        for seed in visible:
            check_deadline()
            rows = _candidate_rows(
                seed, direction=direction, limit=candidates_per_seed, **lookup
            )
            if len(rows) > candidates_per_seed:
                truncated.append(seed)
                rows = rows[:candidates_per_seed]
            for pk, count, subject, object_ in rows:
                candidates[pk] = (count, subject, object_)
        if visible and filters.outcomes != ALL_OUTCOMES:
            check_deadline()
            hidden = _hidden_counts(visible, direction=direction, **lookup)
        ranked = sorted(candidates.items(), key=lambda item: (-item[1][0], item[0]))
        nodes, kept = set(visible), []
        omitted = omitted_for_nodes = 0
        edges_full = False
        for index, (pk, (count, subject, object_)) in enumerate(ranked):
            if len(kept) >= max_edges:
                edges_full = True
                omitted += len(ranked) - index
                break
            grown = nodes | {subject, object_}
            if len(grown) > max_nodes:
                omitted += 1
                omitted_for_nodes += 1
                continue
            kept.append(pk)
            nodes = grown
        partners = nodes - set(visible)
        partners_truncated = False
        if include_partner_edges and len(kept) < max_edges and partners:
            check_deadline()
            rows = _partner_rows(
                partners, exclude=kept, limit=candidates_per_seed, **lookup
            )
            if len(rows) > candidates_per_seed:
                partners_truncated = True
                rows = rows[:candidates_per_seed]
            for index, (pk, count, subject, object_) in enumerate(rows):
                if len(kept) >= max_edges:
                    edges_full = True
                    omitted += len(rows) - index
                    break
                kept.append(pk)
        summaries = {}
        if kept:
            check_deadline()
            summaries = summarize(kept, releases=releases, filters=filters)
    if edges_full:
        stopped_by = "edges"
    elif omitted_for_nodes:
        stopped_by = "nodes"
    else:
        stopped_by = None
    return Neighbourhood(
        visible,
        tuple(summaries[pk] for pk in kept if pk in summaries),
        frozenset(nodes),
        hidden,
        tuple(truncated),
        partners_truncated,
        omitted,
        omitted_for_nodes,
        stopped_by,
    )


# ── The statements ──────────────────────────────────────────────────────────


def _candidate_query(seed, *, releases, filters, layers, direction, taxon, limit):
    """The sliced statement of one seed's candidates: `limit` + 1 rows of
    (relationship id, ranking count, subject id, object id), the most covered
    first and ties to the lowest id; the extra row says the pre-limit cut. The
    plan test reads this queryset's EXPLAIN."""
    covered = covered_assertions(releases=releases, filters=filters)
    return (
        Relationship.objects.filter(
            _direction_q(seed, direction, taxon), _type_q(layers)
        )
        .filter(Exists(covered.filter(relationship_id=OuterRef("pk"))))
        .annotate(n=_count(covered))
        .order_by("-n", "pk")
        .values_list("pk", "n", "subject_id", "object_id")[: limit + 1]
    )


def _candidate_rows(seed, *, releases, filters, layers, direction, taxon, limit):
    """One seed's statement, sent: the seam a test patches."""
    return list(
        _candidate_query(
            seed,
            releases=releases,
            filters=filters,
            layers=layers,
            direction=direction,
            taxon=taxon,
            limit=limit,
        )
    )


def _hidden_counts(seeds, *, releases, filters, layers, direction, taxon) -> dict:
    """{outcome: relationships of these seeds, within the layers, direction
    and taxon, that have evidence passing every other filter and none of the
    requested outcomes}, every outcome a key, from one aggregate over every
    such relationship and never over the ranked rows. Every evidence row of a
    hidden relationship has an outcome outside the requested set, so none is
    excluded; a relationship with evidence of two outcomes counts under both.
    """
    covered = covered_assertions(releases=releases, filters=filters)
    filtered = covered_assertions(
        releases=releases,
        filters=dataclasses.replace(filters, outcomes=ALL_OUTCOMES),
    )
    hidden_ids = (
        Relationship.objects.filter(
            _direction_q_all(sorted(seeds), direction, taxon), _type_q(layers)
        )
        .filter(Exists(filtered.filter(relationship_id=OuterRef("pk"))))
        .filter(~Exists(covered.filter(relationship_id=OuterRef("pk"))))
        .values("pk")
    )
    rows = (
        filtered.filter(relationship_id__in=Subquery(hidden_ids))
        .order_by()
        .values("outcome")
        .annotate(n=Count("relationship_id", distinct=True))
    )
    counts = {outcome: 0 for outcome in Outcome.values}
    for row in rows:
        counts[row["outcome"]] = row["n"]
    return counts


def _partner_rows(partners, *, releases, filters, layers, taxon, exclude, limit):
    """The edges among the partners with covered evidence, listed as a seed's
    candidates are: both endpoints partners, so no new node; `direction` does
    not apply, neither endpoint being a seed; `taxon` applies to both; a
    partner's self-loop qualifies; the kept edges are left out."""
    covered = covered_assertions(releases=releases, filters=filters)
    partners = sorted(partners)
    taxon_q = (
        Q() if taxon is None else Q(subject__taxon_id=taxon, object__taxon_id=taxon)
    )
    return list(
        Relationship.objects.filter(subject_id__in=partners, object_id__in=partners)
        .filter(_type_q(layers), taxon_q)
        .exclude(pk__in=sorted(exclude))
        .filter(Exists(covered.filter(relationship_id=OuterRef("pk"))))
        .annotate(n=_count(covered))
        .order_by("-n", "pk")
        .values_list("pk", "n", "subject_id", "object_id")[: limit + 1]
    )


# ── Predicates ──────────────────────────────────────────────────────────────


def _count(covered):
    """The outer relationship's covered assertions, the ranking count."""
    return Coalesce(
        Subquery(
            covered.filter(relationship_id=OuterRef("pk"))
            .order_by()
            .values("relationship_id")
            .annotate(n=Count("pk"))
            .values("n"),
            output_field=IntegerField(),
        ),
        0,
    )


def _type_q(layers) -> Q:
    if layers is None:
        return Q(relationship_type__is_reference=False)
    return Q(relationship_type_id__in=sorted(layers))


def _direction_q(seed, direction, taxon) -> Q:
    """The relationships of one seed in `direction`, read from the row's own
    `ordered` copy of its type's orientation: a directed or role-ordered
    relationship matches out from its subject and in from its object, a
    symmetric one from either endpoint whatever the direction, a self-loop
    every direction once. With a taxon, only those whose partner, the other
    endpoint (the seed itself for a self-loop), is of it; the seed is never
    dropped for its own taxon."""
    return _endpoint_q(Q(subject_id=seed), Q(object_id=seed), direction, taxon)


def _direction_q_all(seeds, direction, taxon) -> Q:
    """_direction_q over every seed at once, for the hidden aggregate."""
    return _endpoint_q(
        Q(subject_id__in=seeds), Q(object_id__in=seeds), direction, taxon
    )


def _endpoint_q(as_subject, as_object, direction, taxon) -> Q:
    object_of_taxon = Q() if taxon is None else Q(object__taxon_id=taxon)
    subject_of_taxon = Q() if taxon is None else Q(subject__taxon_id=taxon)
    symmetric = Q(ordered=False)
    if direction == "out":
        return (as_subject & object_of_taxon) | (
            as_object & symmetric & subject_of_taxon
        )
    if direction == "in":
        return (as_object & subject_of_taxon) | (
            as_subject & symmetric & object_of_taxon
        )
    return (as_subject & object_of_taxon) | (as_object & subject_of_taxon)

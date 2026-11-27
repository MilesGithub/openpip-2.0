"""The reference closure of a network's seeds (architecture §4.6).

From each named seed the closure follows visible reference links along
REFERENCE_PATHS only: from a gene to the protein entries it encodes, then to
their isoforms; from a protein entry to its isoforms and to the genes that
encode it; from an isoform to its entry, then to the genes that encode that
entry. Nothing else: never from a gene through an entry to another gene, from
an isoform to its siblings, past a path's end, or along a partner's links.
Each entity reached that is not a named seed is a derived seed, marked with
every path that reached it, and graph.neighbourhood looks it up as it looks
up a named seed.

A link counts when graph.entity_visibility.visible_reference_q holds for it:
a reference type, and an assertion of the caller's set covers it under the
default filter, whatever the request's evidence filters, outcome, direction,
taxon or layers. Reference evidence states no basis, method, score or
context, so such a filter would drop every link, and every drawn edge must
be shown. Release kind plays no part. Entity types are never read: the
registry fixes each reference type's ends and the relationship guard
enforces them, so a row's type, and the end the entity in hand stands on,
say which step it is.

Statements: one for the first step from every named seed, then one for the
second step from every entity the first reached from a gene or an isoform,
sent only when there is one. So one or two, whatever the number of seeds,
and a link the caller may not see reads like no link. Each statement reads
its rows in a fixed order and keeps at most CLOSURE_ROW_LIMIT of them; a
cut is counted, so one request always derives the same seeds.

This module imports nothing from graph.api, graph.resolution or
graph.neighbourhood: the caller passes its deadline check in. While the
switch is off reference_closure returns the empty closure with no query.
"""

from dataclasses import dataclass
from functools import reduce
from operator import or_

from django.db.models import Case, F, IntegerField, Q, When

from .entity_visibility import visible_reference_q
from .models import Relationship
from .preconditions import typed_graph_enabled
from .visibility import issued_release_set

# The typed paths a closure follows (architecture §4.6), each a tuple of
# (relationship type slug, direction) steps from the named seed: "out" when
# the entity in hand is the relationship's subject, "in" when it is the
# object. The registry fixes each reference type's ends, so a path's first
# step says what its seed is. Every path has one or two steps, and a
# two-step path's first step is a path of its own. N2 adds such rows, never
# code; a path of three steps needs a third statement, which is code.
REFERENCE_PATHS = (
    # From a gene: the entries it encodes, then their isoforms.
    (("encodes", "out"),),
    (("encodes", "out"), ("isoform_of", "in")),
    # From a protein entry: its isoforms, and the genes that encode it.
    (("isoform_of", "in"),),
    (("encodes", "in"),),
    # From an isoform: its entry, then the genes that encode that entry.
    (("isoform_of", "out"),),
    (("isoform_of", "out"), ("encodes", "in")),
)
# The relationship types the paths walk.
PATH_TYPES = frozenset(slug for path in REFERENCE_PATHS for slug, _ in path)
# The rows one closure statement keeps; a statement that finds more is cut
# there, and the cut is counted. Read when called, so a test may lower it.
CLOSURE_ROW_LIMIT = 1000
# Each path's first step, once, in the order of REFERENCE_PATHS.
_FIRST_STEPS = tuple(dict.fromkeys(path[0] for path in REFERENCE_PATHS))


@dataclass(frozen=True, order=True)
class Derivation:
    """One path of REFERENCE_PATHS that a closure followed: from the named
    seed `seed_id`, through the relationships `steps` (their ids, in path
    order from the seed), to `entity_id`. Ordered by entity, seed, steps."""

    entity_id: int
    seed_id: int
    steps: tuple[int, ...]


@dataclass(frozen=True)
class ReferenceClosure:
    """What a closure found. `derivations`: the paths to each derived seed
    kept, sorted; `dropped`: the derived seeds found and not kept, plus one
    for each statement its row bound cut; `links`: the paths from a named
    seed to another named seed, sorted the same way."""

    derivations: tuple[Derivation, ...]
    dropped: int
    links: tuple[Derivation, ...] = ()


EMPTY_CLOSURE = ReferenceClosure((), 0)


def reference_closure(seed_ids, *, releases, check_deadline, limit) -> ReferenceClosure:
    """The closure of these named seeds, entity ids the caller may see (none
    is read), under `releases`, a ReleaseSet visible_release_ids issued. It
    keeps at most `limit` derived seeds: those one step reached before those
    only two steps reached, then by the lowest seed that reached them in
    their fewest steps, then by id. `check_deadline` is called before each
    statement, and raises to stop the request. A limit that is not a whole
    number of at least 0 is a caller bug (ValueError)."""
    releases = issued_release_set(releases)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError(f"limit is a whole number of at least 0, not {limit!r}.")
    seeds = tuple(sorted(set(seed_ids)))
    if not typed_graph_enabled() or not seeds:
        return EMPTY_CLOSURE
    named = frozenset(seeds)
    # (entity reached, named seed, relationship ids, entities passed through)
    found = set()
    # {second step: {entity the first step reached: {(named seed, ids)}}}
    onward = {}
    check_deadline()
    rows, cut = _read(tuple((slug, way, seeds) for slug, way in _FIRST_STEPS), releases)
    for pk, slug, subject, object_ in rows:
        for way, hand, reached in (("out", subject, object_), ("in", object_, subject)):
            if hand not in named:
                continue
            for path in REFERENCE_PATHS:
                if path[0] != (slug, way):
                    continue
                if len(path) == 1:
                    found.add((reached, hand, (pk,), ()))
                else:
                    starts = onward.setdefault(path[1], {})
                    starts.setdefault(reached, set()).add((hand, (pk,)))
    if onward:
        check_deadline()
        arms = tuple(
            (slug, way, tuple(sorted(onward[slug, way])))
            for slug, way in sorted(onward)
        )
        rows, more = _read(arms, releases)
        cut += more
        for pk, slug, subject, object_ in rows:
            for way, hand, reached in (
                ("out", subject, object_),
                ("in", object_, subject),
            ):
                for seed, steps in onward.get((slug, way), {}).get(hand, ()):
                    found.add((reached, seed, (*steps, pk), (hand,)))
    derived = {}
    for reached, seed, steps, _ in found:
        if reached not in named:
            derived.setdefault(reached, []).append((seed, steps))

    def rank(entity):
        fewest = min(len(steps) for _, steps in derived[entity])
        first = min(seed for seed, steps in derived[entity] if len(steps) == fewest)
        return (fewest, first, entity)

    order = sorted(derived, key=rank)
    kept = frozenset(order[:limit])
    # A path is kept only through entities that are named or kept.
    through = named | kept
    derivations = sorted(
        {
            Derivation(reached, seed, steps)
            for reached, seed, steps, between in found
            if reached in kept and through.issuperset(between)
        }
    )
    links = sorted(
        {
            Derivation(reached, seed, steps)
            for reached, seed, steps, between in found
            if reached in named and through.issuperset(between)
        }
    )
    return ReferenceClosure(
        tuple(derivations), len(order) - len(kept) + cut, tuple(links)
    )


# ── The statements ──────────────────────────────────────────────────────────


def _read(arms, releases):
    """One statement's rows within the bound, and 1 when the bound cut it."""
    rows = _closure_rows(arms, releases=releases, limit=CLOSURE_ROW_LIMIT)
    if len(rows) > CLOSURE_ROW_LIMIT:
        return rows[:CLOSURE_ROW_LIMIT], 1
    return rows, 0


def _arm_q(slug, way, ids) -> Q:
    """The rows of one arm: of the type, the entity in hand one of the ids."""
    end = "subject_id" if way == "out" else "object_id"
    return Q(relationship_type_id=slug, **{f"{end}__in": sorted(ids)})


def _closure_rows(arms, *, releases, limit):
    """One closure statement, sent: the seam a test patches. Each arm is a
    (relationship type slug, direction, entity ids) triple, and a row matches
    an arm when its type is the arm's and its subject ("out") or object
    ("in") is one of the ids; visible_reference_q filters every row. Rows of
    (relationship id, type slug, subject id, object id), ordered by the
    entity in hand, the entity reached and the relationship's id, at most
    `limit` + 1: the extra row says the bound cut the statement."""
    outward = [_arm_q(*arm) for arm in arms if arm[1] == "out"]
    if outward:
        out = reduce(or_, outward)
        hand = Case(
            When(out, then=F("subject_id")),
            default=F("object_id"),
            output_field=IntegerField(),
        )
        reached = Case(
            When(out, then=F("object_id")),
            default=F("subject_id"),
            output_field=IntegerField(),
        )
    else:
        hand, reached = F("object_id"), F("subject_id")
    return list(
        Relationship.objects.filter(visible_reference_q(releases))
        .filter(reduce(or_, (_arm_q(*arm) for arm in arms)))
        .annotate(hand=hand, reached=reached)
        .order_by("hand", "reached", "pk")
        .values_list("pk", "relationship_type_id", "subject_id", "object_id")[
            : limit + 1
        ]
    )

"""Edge summaries over the evidence a caller may see (architecture §4.5).

Nothing here is stored. Every figure is computed when it is asked for, from
the assertions of the releases in the caller's ReleaseSet, which only
graph.visibility.visible_release_ids issues: an unapproved, embargoed,
withdrawn or discarded release never influences a public result, and a set
built by hand is refused.

Three sets of assertions per relationship:

- visible (V): those whose release is in the set and whose release's status,
  read in the same query, is published, superseded or staged, so a set kept
  too long never shows a draft or a withdrawn release;
- filtered (F): V passing every filter except the outcome filter. Each
  type-specific filter applies only to the types it concerns (effects to
  types with effect polarity, genetic signs to types with genetic_sign
  polarity, levels to the physical family), so a view that combines
  regulation and physical layers keeps both. The effect and sign filters
  also apply only to supported assertions, the only ones that carry an
  effect or a sign: a tested-absent, inconclusive or not-significant
  assertion passes them, and is covered when its outcome is asked for;
- covered (C): F with an outcome the filter asks for (supported by default).

An edge is shown when C is not empty. Its outcome counts are over F, so a
shown edge always shows the evidence that contradicts it, whatever effect or
sign the filter asks for. Effect, sign and level summaries read only covered
supporting assertions: a tested-absent direct-interaction test never makes
an edge read "direct interaction", and a negative genetic interaction is
never confused with an experiment that found none.

Independent evidence (independence_links) counts observations, not
records. Two assertions of one outcome describe one observation when they
share an origin (the paper that reports them, or without one the source
dataset of the release) and their detection methods are equal, intersect,
or either names none; or when they share an evidence group, which is scoped
to its release. The count is the number of connected components. Its stated
limits: experiments in one paper whose records share a method, or of which
one names none, count once; so do records of one origin whatever their
evidence basis (a prediction and an experiment of one dataset with no paper
and no method count once, since a basis two sources state differently must
not split one experiment); methods named at different PSI-MI depths count
twice until V1; a keyed and an unkeyed record of one experiment count twice
(M1).

A term is shown by its CURIE and term_label. A crosswalk term shows its
pinned label. Any other term shows the label a commit supplied only while
the release that supplied it is visible to the caller and not synthetic.
Term.label, which evidence() loads with each term, is never shown as it is.

While the switch is off, every function returns nothing.
"""

import math
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from django.db.models import (
    BooleanField,
    Case,
    Exists,
    OuterRef,
    Prefetch,
    Q,
    Subquery,
    Value,
    When,
)

from .models import (
    LEVEL_STRENGTH,
    SCORE_TYPE_SLUG,
    AssertionParticipant,
    ContextFacet,
    Effect,
    EvidenceAssertion,
    EvidenceBasis,
    EvidenceContext,
    EvidenceMethod,
    EvidencePublication,
    EvidencePublicationRole,
    EvidenceScore,
    GeneticSign,
    InteractionLevel,
    MethodRole,
    Outcome,
    Polarity,
    Relationship,
    RelationshipFamily,
    ReleaseStatus,
    Term,
)
from .preconditions import storable, typed_graph_enabled
from .terms import PHYSICAL_INTERACTION_LEVELS, TermError, normalize_curie, ontology_of
from .visibility import issued_release_set

ALL_OUTCOMES = frozenset(Outcome.values)
# Whatever set a caller passes, only these releases' evidence is read.
VISIBLE_STATUSES = (
    ReleaseStatus.PUBLISHED,
    ReleaseStatus.SUPERSEDED,
    ReleaseStatus.STAGED,
)
NOT_STATED = "not_stated"
NOT_APPLICABLE = Effect.NOT_APPLICABLE.value
MIXED = "mixed"
# The values the effect and sign filters take, and the keys of their counts.
EFFECT_KEYS = (
    Effect.ACTIVATING.value,
    Effect.INHIBITING.value,
    Effect.UNKNOWN.value,
    NOT_STATED,
)
SIGN_KEYS = (GeneticSign.NEGATIVE.value, GeneticSign.POSITIVE.value, NOT_STATED)
# Strongest first.
LEVEL_KEYS = tuple(
    sorted(InteractionLevel.values, key=lambda level: -LEVEL_STRENGTH[level])
)


def _closed_set(value, allowed, what, *, required=False):
    if value is None and not required:
        return None
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise TypeError(f"{what} is a set of values, not {value!r}.")
    values = frozenset(value)
    unknown = sorted(map(repr, values - set(allowed)))
    if unknown:
        raise ValueError(
            f"{what} are among {', '.join(allowed)}; not {', '.join(unknown)}."
        )
    return values


def _finite_bound(value, what):
    if value is None:
        return None
    try:
        finite = (
            not isinstance(value, bool)
            and isinstance(value, (int, float))
            and math.isfinite(value)
        )
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(f"{what} is a finite number or None, not {value!r}.")
    return value


@dataclass(frozen=True)
class ScoreRange:
    """One score type's values, from min to max inclusive. A range applies
    to its own type only, never to a pool of types, and an assertion without
    a value of that type (or with a NULL one) never matches."""

    score_type: str
    min: float | None = None
    max: float | None = None

    def __post_init__(self):
        if not isinstance(self.score_type, str) or not SCORE_TYPE_SLUG.fullmatch(
            self.score_type
        ):
            raise ValueError(
                f"A score range names a score type's slug, not {self.score_type!r}."
            )
        low = _finite_bound(self.min, "A score range's min")
        high = _finite_bound(self.max, "A score range's max")
        if low is not None and high is not None and low > high:
            raise ValueError(f"A score range's min {low!r} is above its max {high!r}.")


@dataclass(frozen=True)
class ContextMatch:
    """A context an assertion states: its facet, and the term (a CURIE),
    host taxid or raw value given here. An assertion without the facet never
    matches: an absent facet means not stated, never all."""

    facet: str
    term: str | None = None
    taxid: int | None = None
    raw: str | None = None

    def __post_init__(self):
        if not isinstance(self.facet, str) or self.facet not in ContextFacet.values:
            raise ValueError(
                f"A context's facet is one of {', '.join(ContextFacet.values)}; not "
                f"{self.facet!r}."
            )
        if self.term is not None:
            object.__setattr__(self, "term", _curie(self.term, "A context's term"))
        if self.taxid is not None and (
            isinstance(self.taxid, bool) or not isinstance(self.taxid, int)
        ):
            raise ValueError(f"A context's taxid is an integer, not {self.taxid!r}.")
        if self.raw is not None and not isinstance(self.raw, str):
            raise ValueError(f"A context's raw value is text, not {self.raw!r}.")


def _curie(value, what, ontology=None) -> str:
    try:
        curie = normalize_curie(value)
    except TermError as error:
        raise ValueError(f"{what}: {error}") from None
    if ontology is not None and ontology_of(curie) != ontology:
        raise ValueError(f"{what} is a {ontology} term; {curie} is not.")
    return curie


@dataclass(frozen=True)
class EvidenceFilter:
    """Which assertions a view covers. outcomes selects the covered ones;
    every other filter decides which visible assertions are counted at all.
    effects take activating, inhibiting, unknown and not_stated (no effect
    stated); genetic_signs negative, positive and not_stated. Each applies to
    the supported assertions of the types with its polarity, the only ones
    that carry an effect or a sign; levels to the physical family.
    detection_methods PSI-MI CURIEs, of which an assertion matches by naming
    any one; every score range and context match must hold. None leaves a
    filter out."""

    outcomes: frozenset = frozenset({Outcome.SUPPORTED.value})
    evidence_bases: frozenset | None = None
    effects: frozenset | None = None
    genetic_signs: frozenset | None = None
    detection_methods: frozenset | None = None
    levels: frozenset | None = None
    scores: tuple[ScoreRange, ...] = ()
    contexts: tuple[ContextMatch, ...] = ()

    def __post_init__(self):
        for name, allowed, required in (
            ("outcomes", Outcome.values, True),
            ("evidence_bases", EvidenceBasis.values, False),
            ("effects", EFFECT_KEYS, False),
            ("genetic_signs", SIGN_KEYS, False),
            ("levels", InteractionLevel.values, False),
        ):
            values = _closed_set(
                getattr(self, name), allowed, f"The {name}", required=required
            )
            object.__setattr__(self, name, values)
        methods = self.detection_methods
        if methods is not None:
            if isinstance(methods, (str, bytes)) or not isinstance(methods, Iterable):
                raise TypeError(
                    f"detection_methods is a set of CURIEs, not {methods!r}."
                )
            methods = frozenset(
                _curie(curie, "A detection method", "psi-mi") for curie in methods
            )
            object.__setattr__(self, "detection_methods", methods)
        for name, kind in (("scores", ScoreRange), ("contexts", ContextMatch)):
            items = getattr(self, name)
            if isinstance(items, (str, bytes)) or not isinstance(items, Iterable):
                raise TypeError(
                    f"{name} is a sequence of {kind.__name__}, not {items!r}."
                )
            items = tuple(items)
            for item in items:
                if not isinstance(item, kind):
                    raise TypeError(f"{name} holds {kind.__name__}s, not {item!r}.")
            object.__setattr__(self, name, items)


DEFAULT_FILTER = EvidenceFilter()


@dataclass(frozen=True)
class Observation:
    """What independence reads of one assertion."""

    assertion_id: int
    outcome: str
    # ("publication", id) of the paper that reports it, or ("dataset", id) of
    # its release's source dataset.
    origin: tuple
    detection_methods: frozenset
    # (release id, label) when the source groups it; None otherwise.
    evidence_group: tuple | None


@dataclass(frozen=True)
class EdgeSummary:
    """One relationship's evidence, as a caller may see it (module
    docstring). Counts over covered supporting assertions unless said
    otherwise."""

    relationship_id: int
    relationship_type: str
    family: str
    orientation: str
    subject_id: int
    object_id: int
    # Whether the edge appears: some assertion is covered.
    shown: bool
    # Over F, all four outcomes.
    outcome_counts: dict
    independent_outcome_counts: dict
    assertion_count: int
    evidence_count: int
    # not_applicable for types without effect polarity; None without
    # covered support.
    effect: str | None
    effect_counts: dict
    genetic_sign: str | None
    sign_counts: dict
    # Physical family only (None or empty otherwise): the strongest stored
    # level, and whether it rests only on openPIP's assignment (B14): true
    # only at association, when no covered support at it states a type.
    level: str | None
    level_counts: dict
    level_assigned: bool | None
    # {outcome: levels, strongest first} of F's assertions that are not
    # supported: "tested for: ...".
    tested_levels: dict
    by_basis: dict
    distinct_publications: int
    distinct_releases: int
    distinct_datasets: int
    # Of C, sorted.
    release_ids: tuple
    superseded_release_ids: tuple
    # V minus F.
    outside_filters: int
    outside_outcome_counts: dict


# ── Public functions ────────────────────────────────────────────────────────


def covered_assertions(*, releases, filters=DEFAULT_FILTER):
    """C over every relationship, as a queryset: what a view counts, for
    ranking edges and for the functions below."""
    releases = issued_release_set(releases)
    filters = _checked(filters)
    if not typed_graph_enabled():
        return EvidenceAssertion.objects.none()
    return _visible(releases).filter(
        _filter_q(filters), outcome__in=sorted(filters.outcomes)
    )


def summarize(relationship_ids, *, releases, filters=DEFAULT_FILTER) -> dict:
    """{relationship id: EdgeSummary} for each of these relationships with a
    visible assertion; one without is absent, so existence never shows.
    Three queries, whatever the number of relationships or assertions."""
    releases = issued_release_set(releases)
    filters = _checked(filters)
    ids = _relationship_ids(relationship_ids)
    if not typed_graph_enabled() or not ids:
        return {}
    relationships = {
        row["pk"]: row
        for row in Relationship.objects.filter(pk__in=ids).values(
            "pk",
            "relationship_type_id",
            "relationship_type__family",
            "relationship_type__orientation",
            "relationship_type__polarity",
            "subject_id",
            "object_id",
        )
    }
    if not relationships:
        return {}
    visible = _visible(releases).filter(relationship_id__in=ids)
    condition = _filter_q(filters)
    passes = (
        Case(
            When(condition, then=Value(True)),
            default=Value(False),
            output_field=BooleanField(),
        )
        if condition
        else Value(True, output_field=BooleanField())
    )
    reported_in = EvidencePublication.objects.filter(
        assertion=OuterRef("pk"), role=EvidencePublicationRole.REPORTED_IN
    ).values("publication_id")[:1]
    rows = list(
        visible.annotate(passes=passes, reported_in=Subquery(reported_in))
        .values(
            "pk",
            "relationship_id",
            "release_id",
            "release__status",
            "release__dataset_id",
            "outcome",
            "evidence_basis",
            "effect",
            "genetic_sign",
            "interaction_level",
            "interaction_type_id",
            "evidence_group",
            "passes",
            "reported_in",
        )
        .order_by("pk")
    )
    if not rows:
        return {}
    methods = defaultdict(set)
    detected = EvidenceMethod.objects.filter(
        role=MethodRole.DETECTION, assertion__in=visible.values("pk")
    )
    for assertion_id, term in detected.values_list("assertion_id", "term_id"):
        methods[assertion_id].add(term)
    by_relationship = defaultdict(list)
    for row in rows:
        by_relationship[row["relationship_id"]].append(row)
    return {
        pk: _summary(relationships[pk], by_relationship[pk], methods, filters)
        for pk in sorted(by_relationship)
    }


def evidence(relationship_id, *, releases, filters=None):
    """V of one relationship (C when filters are given), contradictory
    assertions included, ordered by id for cursor pagination, with what an
    evidence row shows loaded in a constant number of queries per page.

    A term loaded here (eco_term, interaction_type, a method's or a
    context's term) is shown by its CURIE and term_label(term,
    releases=releases), never by Term.label. That label is the one its first
    commit stated, and the release that stated it may be one the caller
    cannot see."""
    releases = issued_release_set(releases)
    if isinstance(relationship_id, bool) or not isinstance(relationship_id, int):
        raise TypeError(f"A relationship id is an integer, not {relationship_id!r}.")
    if filters is not None:
        filters = _checked(filters)
    if not typed_graph_enabled() or not storable([relationship_id]):
        return EvidenceAssertion.objects.none()
    rows = _visible(releases)
    if filters is not None:
        rows = rows.filter(_filter_q(filters), outcome__in=sorted(filters.outcomes))
    return (
        rows.filter(relationship_id=relationship_id)
        .select_related(
            "source_record", "release__dataset", "eco_term", "interaction_type"
        )
        .prefetch_related(
            Prefetch(
                "participants",
                queryset=AssertionParticipant.objects.select_related(
                    "entity", "stated_entity"
                ).order_by("pk"),
            ),
            Prefetch(
                "methods",
                queryset=EvidenceMethod.objects.select_related("term").order_by("pk"),
            ),
            Prefetch(
                "publications",
                queryset=EvidencePublication.objects.select_related(
                    "publication"
                ).order_by("pk"),
            ),
            Prefetch(
                "scores",
                queryset=EvidenceScore.objects.select_related("score_type").order_by(
                    "pk"
                ),
            ),
            Prefetch(
                "contexts",
                queryset=EvidenceContext.objects.select_related(
                    "term", "taxon"
                ).order_by("pk"),
            ),
        )
        .order_by("pk")
    )


def term_label(term, *, releases) -> str | None:
    """The label to show beside a term's CURIE: the crosswalk's pinned label
    for a PSI-MI crosswalk term; otherwise the label a commit supplied, when
    the release that supplied it is visible to the caller and not synthetic
    (D12: a synthetic release never labels a term that real evidence may
    name); otherwise None, and the CURIE is shown alone."""
    releases = issued_release_set(releases)
    curie = term.pk if isinstance(term, Term) else term
    if not isinstance(curie, str):
        raise TypeError(f"A term or its CURIE, not {term!r}.")
    if not typed_graph_enabled():
        return None
    try:
        curie = normalize_curie(curie)
    except TermError:
        return None
    pinned = PHYSICAL_INTERACTION_LEVELS.get(curie)
    if pinned is not None:
        return pinned[1]
    return (
        Term.objects.filter(
            pk=curie,
            label_release_id__in=storable(releases.ids),
            label_release__status__in=VISIBLE_STATUSES,
            label_release__is_synthetic=False,
        )
        .values_list("label", flat=True)
        .first()
    )


def independence_links(observations) -> dict:
    """{assertion id: the observation it belongs to}, each observation named
    by the smallest assertion id among the records that describe it.

    Two assertions of one outcome are one observation when they share an
    origin and their detection-method sets are equal, intersect, or either
    is empty; or when they share an evidence group (release, label). Records
    linked through others are one observation too: the observations are the
    connected components, found by union-find. Assertions of different
    outcomes are never one observation. M1 adds a shared observation key
    here, ahead of these rules.
    """
    observations = list(observations)
    parent = {item.assertion_id: item.assertion_id for item in observations}

    def root(pk):
        while parent[pk] != pk:
            parent[pk] = parent[parent[pk]]
            pk = parent[pk]
        return pk

    def link(first, second):
        first, second = root(first), root(second)
        if first != second:
            # The smaller id stays the root: it names the observation.
            parent[max(first, second)] = min(first, second)

    by_origin = defaultdict(list)
    for item in observations:
        by_origin[(item.outcome, item.origin)].append(item)
    for items in by_origin.values():
        unnamed = [item for item in items if not item.detection_methods]
        if unnamed:
            # A record that names no method links every other of its origin.
            for item in items:
                link(unnamed[0].assertion_id, item.assertion_id)
            continue
        first_naming = {}
        for item in items:
            for term in item.detection_methods:
                if term in first_naming:
                    link(first_naming[term], item.assertion_id)
                else:
                    first_naming[term] = item.assertion_id
    first_in_group = {}
    for item in observations:
        if item.evidence_group is None:
            continue
        key = (item.outcome, item.evidence_group)
        if key in first_in_group:
            link(first_in_group[key], item.assertion_id)
        else:
            first_in_group[key] = item.assertion_id
    return {pk: root(pk) for pk in parent}


def independent_count(observations) -> int:
    """The number of independent observations among these assertions."""
    return len(set(independence_links(observations).values()))


# ── Helpers ─────────────────────────────────────────────────────────────────


def _checked(filters):
    if not isinstance(filters, EvidenceFilter):
        raise TypeError(f"filters is an EvidenceFilter, not {filters!r}.")
    return filters


def _relationship_ids(values) -> list:
    if (
        values is None
        or isinstance(values, (str, bytes))
        or not isinstance(values, Iterable)
    ):
        raise TypeError(
            f"Relationship ids are an iterable of integers, not {values!r}."
        )
    ids = set()
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"A relationship id is an integer, not {value!r}.")
        ids.add(value)
    return storable(ids)


def _visible(releases):
    """V over every relationship: the release in the set, and its status as
    stored one that counts."""
    return EvidenceAssertion.objects.filter(
        release_id__in=storable(releases.ids),
        release__status__in=VISIBLE_STATUSES,
    )


def _stated_or_null(name, values) -> Q:
    """Stated values, with not_stated meaning NULL."""
    condition = Q(**{f"{name}__in": sorted(values - {NOT_STATED})})
    if NOT_STATED in values:
        condition |= Q(**{f"{name}__isnull": True})
    return condition


def _filter_q(filters) -> Q:
    """Every filter but the outcome filter. A type-specific filter leaves the
    assertions of other types alone, and the effect and sign filters leave
    alone every assertion that is not supported: none carries an effect or a
    sign (rule 8), so the evidence against an edge stays in F."""
    condition = Q()
    if filters.evidence_bases is not None:
        condition &= Q(evidence_basis__in=sorted(filters.evidence_bases))
    polarity = "relationship__relationship_type__polarity"
    # Rule 8: only a supported assertion carries an effect or a sign, so these
    # filters leave every other outcome alone, as they leave other types alone.
    not_supported = ~Q(outcome=Outcome.SUPPORTED)
    if filters.effects is not None:
        condition &= (
            ~Q(**{polarity: Polarity.EFFECT})
            | not_supported
            | _stated_or_null("effect", filters.effects)
        )
    if filters.genetic_signs is not None:
        condition &= (
            ~Q(**{polarity: Polarity.GENETIC_SIGN})
            | not_supported
            | _stated_or_null("genetic_sign", filters.genetic_signs)
        )
    if filters.levels is not None:
        condition &= ~Q(
            relationship__relationship_type__family=RelationshipFamily.PHYSICAL
        ) | Q(interaction_level__in=sorted(filters.levels))
    if filters.detection_methods is not None:
        condition &= Q(
            Exists(
                EvidenceMethod.objects.filter(
                    assertion=OuterRef("pk"),
                    role=MethodRole.DETECTION,
                    term_id__in=sorted(filters.detection_methods),
                )
            )
        )
    for score in filters.scores:
        bounds = {}
        if score.min is not None:
            bounds["value__gte"] = score.min
        if score.max is not None:
            bounds["value__lte"] = score.max
        condition &= Q(
            Exists(
                EvidenceScore.objects.filter(
                    assertion=OuterRef("pk"),
                    score_type_id=score.score_type,
                    value__isnull=False,
                    **bounds,
                )
            )
        )
    for context in filters.contexts:
        match = {"facet": context.facet}
        for name, value in (
            ("term_id", context.term),
            ("taxon_id", context.taxid),
            ("raw_value", context.raw),
        ):
            if value is not None:
                match[name] = value
        condition &= Q(
            Exists(EvidenceContext.objects.filter(assertion=OuterRef("pk"), **match))
        )
    return condition


def _observation(row, methods) -> Observation:
    reported_in = row["reported_in"]
    origin = (
        ("publication", reported_in)
        if reported_in is not None
        else ("dataset", row["release__dataset_id"])
    )
    group = (
        (row["release_id"], row["evidence_group"]) if row["evidence_group"] else None
    )
    return Observation(
        row["pk"],
        row["outcome"],
        origin,
        frozenset(methods.get(row["pk"], ())),
        group,
    )


def _counts(rows, name, keys) -> dict:
    """{key: rows whose `name` is the key}, every key present; a NULL counts
    as not_stated."""
    counted = Counter(NOT_STATED if row[name] is None else row[name] for row in rows)
    return {key: counted[key] for key in keys}


def _summary(relationship, rows, methods, filters) -> EdgeSummary:
    family = relationship["relationship_type__family"]
    polarity = relationship["relationship_type__polarity"]
    filtered = [row for row in rows if row["passes"]]
    outside = [row for row in rows if not row["passes"]]
    covered = [row for row in filtered if row["outcome"] in filters.outcomes]
    support = [row for row in covered if row["outcome"] == Outcome.SUPPORTED]
    observations = {row["pk"]: _observation(row, methods) for row in rows}

    def independent(selected):
        return independent_count(observations[row["pk"]] for row in selected)

    effect_counts = _counts(support, "effect", EFFECT_KEYS)
    sign_counts = _counts(support, "genetic_sign", SIGN_KEYS)
    level = level_counts = level_assigned = None
    tested_levels = {}
    if family == RelationshipFamily.PHYSICAL:
        level_counts = _counts(support, "interaction_level", LEVEL_KEYS)
        stated = [
            row["interaction_level"] for row in support if row["interaction_level"]
        ]
        if stated:
            level = max(stated, key=LEVEL_STRENGTH.__getitem__)
            # openPIP assigns only association, from a stated assay (B14);
            # not_stated is no assignment.
            level_assigned = level == InteractionLevel.ASSOCIATION and not any(
                row["interaction_type_id"] is not None
                for row in support
                if row["interaction_level"] == level
            )
        tested = defaultdict(set)
        for row in filtered:
            if row["outcome"] != Outcome.SUPPORTED and row["interaction_level"]:
                tested[row["outcome"]].add(row["interaction_level"])
        tested_levels = {
            outcome: tuple(key for key in LEVEL_KEYS if key in tested[outcome])
            for outcome in Outcome.values
            if outcome in tested
        }
    bases = Counter(row["evidence_basis"] for row in support)
    return EdgeSummary(
        relationship_id=relationship["pk"],
        relationship_type=relationship["relationship_type_id"],
        family=family,
        orientation=relationship["relationship_type__orientation"],
        subject_id=relationship["subject_id"],
        object_id=relationship["object_id"],
        shown=bool(covered),
        outcome_counts=_counts(filtered, "outcome", Outcome.values),
        independent_outcome_counts={
            outcome: independent(row for row in filtered if row["outcome"] == outcome)
            for outcome in Outcome.values
        },
        assertion_count=len(support),
        evidence_count=independent(support),
        effect=_effect(polarity, support, effect_counts),
        effect_counts=effect_counts,
        genetic_sign=_sign(polarity, support, sign_counts),
        sign_counts=sign_counts,
        level=level,
        level_counts=level_counts or {},
        level_assigned=level_assigned,
        tested_levels=tested_levels,
        by_basis={
            basis: bases[basis] for basis in EvidenceBasis.values if basis in bases
        },
        distinct_publications=len({row["reported_in"] for row in support} - {None}),
        distinct_releases=len({row["release_id"] for row in support}),
        distinct_datasets=len({row["release__dataset_id"] for row in support}),
        release_ids=tuple(sorted({row["release_id"] for row in covered})),
        superseded_release_ids=tuple(
            sorted(
                {
                    row["release_id"]
                    for row in covered
                    if row["release__status"] == ReleaseStatus.SUPERSEDED
                }
            )
        ),
        outside_filters=len(outside),
        outside_outcome_counts=_counts(outside, "outcome", Outcome.values),
    )


def _effect(polarity, support, counts):
    """mixed when activating and inhibiting are both stated; else the one
    stated; unknown when every effect is unknown or none is stated."""
    if polarity != Polarity.EFFECT:
        return NOT_APPLICABLE
    if not support:
        return None
    activating, inhibiting = counts[Effect.ACTIVATING], counts[Effect.INHIBITING]
    if activating and inhibiting:
        return MIXED
    if activating:
        return Effect.ACTIVATING.value
    if inhibiting:
        return Effect.INHIBITING.value
    return Effect.UNKNOWN.value


def _sign(polarity, support, counts):
    if polarity != Polarity.GENETIC_SIGN:
        return NOT_APPLICABLE
    if not support:
        return None
    negative, positive = counts[GeneticSign.NEGATIVE], counts[GeneticSign.POSITIVE]
    if negative and positive:
        return MIXED
    if negative:
        return GeneticSign.NEGATIVE.value
    if positive:
        return GeneticSign.POSITIVE.value
    return NOT_STATED

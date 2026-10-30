"""Claims: what one source record states about relationships, checked against
the rules of their types and written as evidence (architecture §3.6,
§4.2-§4.4).

An adapter maps a record to Claims. This module is the one place their rules
are applied, numbered as in the G3 specification and named in every refusal
(RULES): check_claims applies them read-only, for a dry run, and
record_claims applies the same checks before it writes anything, so a dry
run and a commit refuse a record in the same words. No rule writes anything.

Identity. canonical_relationship gives a claim's relationship: its type and
its two entities, a symmetric pair lower entity id first. A direction that
contradicts a directed or role-ordered type is refused, never swapped.
create_relationships writes a batch's missing relationships in canonical
order, so two commits that share pairs take their locks in one order and
never deadlock.

Idempotency is per record. Each assertion is keyed by its claim's key part
and, when the record reports several papers, by the identifiers the record
states for each paper, never by a stored row that a co-referent merge can
change. Recording a record again is a no-op only when it would write
exactly the assertions already stored, read back and compared; any
difference is refused (ClaimConflict), because a stored assertion never
changes: a correction is a new release. Qualifiers are compared as
canonical JSON text, so true is not 1 and 1.0 is not 1, and are taken in
the form PostgreSQL keeps their numbers (1e+16 is the integer
10000000000000000), so that what is read back is what a claim gives.

Locks. record_claims and create_relationships lock the release, then the
import run, then hold in shared mode the relationship and score types they
validate against (graph.models.share_lock), so graph_sync_registry, which
locks a row before it changes it, either waits for the commit or is waited
for, and whichever comes second sees the other's result.
"""

import json
import operator
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from django.db import transaction

from .approvals import INTERNAL, effective_scopes_by_release, is_effective
from .models import (
    SYNTHETIC_TAXON,
    UNSPECIFIED_PHYSICAL_TYPE,
    STATED_TEXT_MAX,
    AssertionMethod,
    AssertionParticipant,
    ContextFacet,
    DatasetRelease,
    Effect,
    Entity,
    EntityStatus,
    EntityType,
    EvidenceAssertion,
    EvidenceBasis,
    EvidenceContext,
    EvidenceMethod,
    EvidencePublication,
    EvidencePublicationRole,
    EvidenceScore,
    GeneticSign,
    ImportMode,
    ImportRun,
    ImportState,
    InteractionLevel,
    MethodRole,
    Orientation,
    Outcome,
    Polarity,
    Publication,
    RecordStatus,
    Relationship,
    RelationshipFamily,
    RelationshipType,
    ReleaseStatus,
    ResolutionBasis,
    ScoreDirection,
    ScoreType,
    SourceRecord,
    Taxon,
    Term,
    _as_stored,
    qualifiers_problem,
    share_lock,
)
from .preconditions import locked_release, require_switch, storable, typed_graph_enabled
from .publications import (
    PublicationError,
    doi_lookup,
    normalize_doi,
    normalize_imex,
    normalize_pmcid,
    normalize_pmid,
)
from .scores import (
    censored_bound,
    parse_score,
    raw_value_problem,
    score_value_problem,
)
from .terms import PHYSICAL_INTERACTION_LEVELS, TermError, normalize_curie, ontology_of

# What each refusal names.
RULES = {
    1: "relationship type and endpoints",
    2: "endpoint types",
    3: "stated direction",
    4: "self-pairs",
    5: "canonical endpoints",
    6: "closed values",
    7: "polarity",
    8: "outcome",
    9: "interaction level",
    10: "terms",
    11: "publications and assertion keys",
    12: "participants",
    13: "synthetic separation",
    14: "scores",
    15: "contexts",
    16: "text and JSON",
}

# The key part a claim's assertions get when it names none.
MAIN = "main"
KEY_PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}")
# The relationship type a protein isoform's entry is named by (B12).
ISOFORM_OF = "isoform_of"
# The identifiers a record states for a paper, in the order a paper key is
# taken from them, each normalized as graph.publications stores it, with the
# Publication field the stated value must equal.
STATED_IDENTIFIERS = {
    "pmid": (normalize_pmid, "pmid"),
    "doi": (lambda value: doi_lookup(normalize_doi(value)), "doi_lookup"),
    "pmcid": (normalize_pmcid, "pmcid"),
    "imex": (normalize_imex, "imex_id"),
    "local": (None, "local_key"),
}
# A not-significant result's criteria: the score holds against the
# threshold (abs: its absolute value does).
THRESHOLD_OPERATORS = {
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
    "abs<": lambda value, threshold: abs(value) < threshold,
    "abs<=": lambda value, threshold: abs(value) <= threshold,
    "abs>": lambda value, threshold: abs(value) > threshold,
    "abs>=": lambda value, threshold: abs(value) >= threshold,
}
THRESHOLD_KEYS = frozenset({"score_type", "operator", "value"})
# The assertion fields compared when a record is recorded again, besides
# its relationship, qualifiers and parts.
COMPARED_FIELDS = (
    "outcome",
    "evidence_basis",
    "assertion_method",
    "effect",
    "genetic_sign",
    "eco_term_id",
    "interaction_type_id",
    "interaction_level",
    "relationship_as_stated",
    "effect_as_stated",
    "evidence_group",
)
PART_KINDS = ("participants", "methods", "publications", "scores", "contexts")


class ClaimError(ValueError):
    """Nothing was written: a claim breaks a rule of evidence, or the commit
    may not record it. The message names the rule."""


class ClaimConflict(ClaimError):
    """Nothing was written: the source record already holds assertions that
    these claims do not reproduce exactly."""


# ── What an adapter states ──────────────────────────────────────────────────
# Frozen, with mappings and sequences copied on entry, so a claim is what it
# was when it was made. They hold mappings, so none is hashable.


def _copied(value):
    return dict(value) if isinstance(value, Mapping) else value


def _items(value):
    """A claim's sequence of parts, as a tuple."""
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Iterable):
        return value
    return tuple(value)


def _string_keyed(value) -> bool:
    """Every object in a JSON value has string keys: json.dumps would turn
    any other key into a string silently. (A value that contains itself is
    left to json.dumps, which refuses it.)"""
    stack, seen = [value], set()
    while stack:
        item = stack.pop()
        if not isinstance(item, (Mapping, list, tuple)) or id(item) in seen:
            continue
        seen.add(id(item))
        if isinstance(item, Mapping):
            if not all(isinstance(key, str) for key in item):
                return False
            stack.extend(item.values())
        else:
            stack.extend(item)
    return True


def _stored_json(value):
    """The value a JSON round trip gives back (a tuple becomes a list), in
    the form the database keeps (_as_stored). TypeError, ValueError or
    RecursionError when it has no JSON form."""
    return _as_stored(json.loads(json.dumps(value, allow_nan=False)))


def _round_trip(value):
    """The qualifiers as they are stored and read back (_stored_json), when
    they are an object with string keys that survives a JSON round trip;
    otherwise a copy, for rule 16 to refuse."""
    if not isinstance(value, Mapping):
        return value
    value = dict(value)
    if not _string_keyed(value):
        return value
    try:
        return _stored_json(value)
    except (TypeError, ValueError, RecursionError):
        return value


@dataclass(frozen=True)
class PlannedEntity:
    """An entity a commit would create: check_claims only."""

    entity_type: EntityType | str
    taxon: Taxon | int | None

    __hash__ = None


@dataclass(frozen=True)
class Endpoint:
    """One side of a claim, as the source stated it."""

    entity: Entity | int | PlannedEntity
    # The identifier exactly as written.
    as_stated: str
    # A ResolutionBasis value.
    basis: str
    # The isoform the source named, for basis isoform_entry (B12).
    stated_entity: Entity | int | None = None

    __hash__ = None


@dataclass(frozen=True)
class PublicationRef:
    """A paper the record cites, with its role, and the identifiers the
    record states for it (keys among pmid, doi, pmcid, imex, local), from
    which the assertion keys of a record reporting several papers come. The
    publication is None only in check_claims, for a paper not stored yet."""

    publication: Publication | int | None
    # An EvidencePublicationRole value.
    role: str
    stated: Mapping[str, str] = field(default_factory=dict)

    __hash__ = None

    def __post_init__(self):
        object.__setattr__(self, "stated", _copied(self.stated))


@dataclass(frozen=True)
class MethodRef:
    # A Term, or its CURIE.
    term: Term | str
    # A MethodRole value.
    role: str

    __hash__ = None


@dataclass(frozen=True)
class ScoreValue:
    # A ScoreType, or its slug.
    score_type: ScoreType | str
    # The value as written, kept verbatim.
    raw: str

    __hash__ = None


@dataclass(frozen=True)
class ContextValue:
    # A ContextFacet value.
    facet: str
    # The value as written, kept verbatim.
    raw: str
    label: str = ""
    # A Term or its CURIE, for facets other than host_taxon.
    term: Term | str | None = None
    # A Taxon or its taxid, for host_taxon only.
    taxon: Taxon | int | None = None

    __hash__ = None


@dataclass(frozen=True)
class Claim:
    """One statement of a record about one relationship."""

    relationship_type: RelationshipType | str
    subject: Endpoint
    object: Endpoint
    outcome: str
    evidence_basis: str
    assertion_method: str
    effect: str | None = None
    genetic_sign: str | None = None
    interaction_type: Term | str | None = None
    interaction_level: str | None = None
    eco_term: Term | str | None = None
    relationship_as_stated: str = ""
    effect_as_stated: str = ""
    evidence_group: str = ""
    # As the source stated it: checked against the type, never stored.
    stated_directed: bool | None = None
    # Tells several claims of one record apart.
    key_part: str = ""
    qualifiers: Mapping = field(default_factory=dict)
    methods: tuple[MethodRef, ...] = ()
    publications: tuple[PublicationRef, ...] = ()
    scores: tuple[ScoreValue, ...] = ()
    contexts: tuple[ContextValue, ...] = ()

    __hash__ = None

    def __post_init__(self):
        object.__setattr__(self, "qualifiers", _round_trip(self.qualifiers))
        for name in ("methods", "publications", "scores", "contexts"):
            object.__setattr__(self, name, _items(getattr(self, name)))


# ── What the services return ────────────────────────────────────────────────


@dataclass(frozen=True)
class CanonicalRelationship:
    """A relationship's identity: its type and endpoints, a symmetric pair
    lower entity id first, and its qualifier key ('' in G3)."""

    type_slug: str
    subject_id: int
    object_id: int
    ordered: bool
    qualifier_key: str = ""

    def __str__(self):
        arrow = "->" if self.ordered else "--"
        return f"{self.type_slug} {self.subject_id}{arrow}{self.object_id}"


@dataclass(frozen=True)
class RecordedAssertion:
    assertion: EvidenceAssertion
    # Written by this call; False when the record already held it.
    created: bool
    relationship: Relationship
    relationship_created: bool


@dataclass(frozen=True)
class RecordResult:
    assertions: tuple[RecordedAssertion, ...]
    # What reading the record's values lost or assigned (a score kept as its
    # raw value alone, an unregistered host taxon, an assigned level).
    notes: tuple[str, ...]


@dataclass(frozen=True)
class RelationshipResult:
    relationship: Relationship
    created: bool


@dataclass(frozen=True)
class PlannedClaim:
    # None when an endpoint is a planned entity.
    canonical: CanonicalRelationship | None
    # Whether the relationship is stored; None for planned endpoints.
    relationship_exists: bool | None
    assertion_keys: tuple[str, ...]


@dataclass(frozen=True)
class ClaimPlan:
    """What record_claims would write for a record's claims."""

    claims: tuple[PlannedClaim, ...]
    # None without a source record; True when it already holds exactly these
    # assertions (a commit is a no-op), False when it holds none.
    assertions_exist: bool | None
    notes: tuple[str, ...]

    @property
    def assertion_keys(self) -> tuple[str, ...]:
        return tuple(key for claim in self.claims for key in claim.assertion_keys)


# ── Public functions ────────────────────────────────────────────────────────


def canonical_relationship(
    relationship_type, subject, object, *, stated_directed=None
) -> CanonicalRelationship:
    """The identity of the relationship of this type from subject to object
    (rules 1-5; rule 3 when stated_directed is given). Read-only. The type
    is a row or its slug, the entities rows or their ids."""
    require_switch(ClaimError, "no relationship identity is computed")
    slug = _key(relationship_type, RelationshipType)
    ids = {_entity_key(subject), _entity_key(object)}
    types = _relationship_types({slug})
    entities = _entities(ids)
    check = _Check(None, None, _Rows(types=types, entities=entities), None, True)
    rel_type = check.relationship_type(relationship_type)
    ends = (
        check.endpoint(Endpoint(subject, "", ""), "subject"),
        check.endpoint(Endpoint(object, "", ""), "object"),
    )
    return check.identity(rel_type, *ends, stated_directed)


def find_relationship(canonical) -> Relationship | None:
    """The stored relationship with this identity, or None. Read-only; None
    while the switch is off."""
    if not isinstance(canonical, CanonicalRelationship):
        raise TypeError(f"A CanonicalRelationship, not {canonical!r}.")
    if not typed_graph_enabled():
        return None
    return _stored_relationships([canonical]).get(canonical)


def check_claims(claims, *, release, source_record=None) -> ClaimPlan:
    """Apply rules 1-16 to the claims of one record, without an import run and
    without writing: the dry run's check. Endpoints may be PlannedEntity,
    publications PublicationRef(None, role, stated), and terms CURIEs that
    are not stored yet (their form and the crosswalk are still checked).

    release: the record's release, a row or its id. source_record (a row or
    its id, of that release), when given, is compared as record_claims would
    compare it: ClaimConflict when it holds other assertions. Every refusal
    is the ClaimError record_claims would raise.
    """
    require_switch(ClaimError, "no claim is checked")
    claims = _claims(claims)
    release = _release(release)
    record_pk = None if source_record is None else _record_of(source_record, release)
    rows = _Rows.load(claims, lock=False)
    planned, notes = _plan(claims, rows, release, for_record=False)
    canonicals = [spec.canonical for spec in planned if spec.canonical is not None]
    stored = _stored_relationships(canonicals)
    by_claim = defaultdict(list)
    for spec in planned:
        by_claim[spec.claim].append(spec)
    summaries = []
    for n in range(1, len(claims) + 1):
        specs = by_claim[n]
        canonical = specs[0].canonical
        summaries.append(
            PlannedClaim(
                canonical,
                None if canonical is None else canonical in stored,
                tuple(spec.key for spec in specs),
            )
        )
    exist = None
    if record_pk is not None:
        held = _stored_assertions(record_pk)
        if held:
            _compare(record_pk, planned, held)
        exist = bool(held)
    return ClaimPlan(tuple(summaries), exist, tuple(notes))


def record_claims(source_record, claims, *, import_run) -> RecordResult:
    """Write the evidence of every claim of one source record, in one
    transaction, or nothing.

    source_record and import_run are rows or ids. The record's release is
    locked and must be a draft, the run (locked next) a running commit run of
    it, and the record mapped; a release that is not synthetic needs an
    effective internal approval. The relationship and score types the claims
    name are then held in shared mode, and every claim is checked (rules
    1-16). A record that already holds assertions is a no-op when these
    claims give exactly those assertions, compared by reading them back, and
    a ClaimConflict naming the added, missing and differing keys otherwise.
    New relationships are created race-safely, in canonical order; each
    assertion is inserted with save(), and each kind of part with one
    bulk_create(), so the number of queries does not grow with the parts.
    """
    require_switch(ClaimError, "no claim is recorded")
    claims = _claims(claims)
    with transaction.atomic():
        release, run, record_pk = _locked_record(source_record, import_run)
        rows = _Rows.load(claims, lock=True)
        planned, notes = _plan(claims, rows, release, for_record=True)
        held = _stored_assertions(record_pk)
        if held:
            _compare(record_pk, planned, held)
            recorded = tuple(
                RecordedAssertion(
                    held[spec.key].assertion,
                    False,
                    held[spec.key].assertion.relationship,
                    False,
                )
                for spec in planned
            )
        else:
            recorded = _write(planned, release, run, record_pk)
    return RecordResult(recorded, tuple(notes))


def record_claim(source_record, claim, *, import_run) -> RecordResult:
    """record_claims with one claim."""
    return record_claims(source_record, [claim], import_run=import_run)


def create_relationships(canonicals, *, import_run) -> dict:
    """{canonical: RelationshipResult} for a batch's relationships, the
    missing ones created in canonical order (type slug, subject id, object
    id), race-safely, so two commits that share pairs never deadlock.

    Under record_claims' preconditions: import_run (a row or its id) is a
    running commit run of a draft release that is synthetic or has an
    effective internal approval. Each identity is checked again from the
    database (rules 1-5, and rule 13 against the run's release), and must
    already be canonical.
    """
    require_switch(ClaimError, "no relationship is created")
    if isinstance(canonicals, (str, bytes, Mapping)) or not isinstance(
        canonicals, Iterable
    ):
        raise TypeError(f"An iterable of CanonicalRelationship, not {canonicals!r}.")
    canonicals = list(canonicals)
    for canonical in canonicals:
        if not isinstance(canonical, CanonicalRelationship):
            raise TypeError(f"A CanonicalRelationship, not {canonical!r}.")
    with transaction.atomic():
        release, _ = _locked_run_release(import_run)
        slugs = {
            _key(canonical.type_slug, RelationshipType) for canonical in canonicals
        }
        share_lock(RelationshipType, None, slugs)
        rows = _Rows(
            types=_relationship_types(slugs),
            entities=_entities(
                {_entity_key(c.subject_id) for c in canonicals}
                | {_entity_key(c.object_id) for c in canonicals}
            ),
        )
        for n, canonical in enumerate(canonicals, 1):
            _check_canonical(n, canonical, rows, release)
        return {
            canonical: RelationshipResult(*found)
            for canonical, found in _relationships(canonicals).items()
        }


# ── Preconditions ───────────────────────────────────────────────────────────


def _pk(value, model):
    """The id a caller gave, a row or the id itself; None for anything that
    is not an id a row could have."""
    pk = value.pk if isinstance(value, model) else value
    if isinstance(pk, bool) or not isinstance(pk, int) or not storable([pk]):
        return None
    return pk


def _key(value, model):
    """The slug or CURIE a caller gave, a row or the key itself; None for
    anything else."""
    pk = value.pk if isinstance(value, model) else value
    return pk if isinstance(pk, str) else None


def _entity_key(value):
    return _pk(value, Entity)


def _taxon_key(value):
    return _pk(value, Taxon)


def _shown(value) -> str:
    return repr(value.pk if hasattr(value, "_meta") else value)


def _claims(claims) -> tuple:
    if isinstance(claims, (str, bytes, Mapping, Claim)) or not isinstance(
        claims, Iterable
    ):
        raise TypeError(f"claims is an iterable of Claim, not {claims!r}.")
    claims = tuple(claims)
    for claim in claims:
        if not isinstance(claim, Claim):
            raise TypeError(f"A claim is a graph.claims.Claim, not {claim!r}.")
        for side in ("subject", "object"):
            if not isinstance(getattr(claim, side), Endpoint):
                raise TypeError(f"A claim's {side} is an Endpoint, not {claim!r}.")
        for name, kind in (
            ("methods", MethodRef),
            ("publications", PublicationRef),
            ("scores", ScoreValue),
            ("contexts", ContextValue),
        ):
            items = getattr(claim, name)
            if not isinstance(items, tuple) or not all(
                isinstance(item, kind) for item in items
            ):
                raise TypeError(
                    f"A claim's {name} are {kind.__name__}s, not {items!r}."
                )
    return claims


def _release(release) -> dict:
    pk = _pk(release, DatasetRelease)
    row = None
    if pk is not None:
        row = (
            DatasetRelease.objects.filter(pk=pk)
            .values("pk", "is_synthetic", "dataset_id")
            .first()
        )
    if row is None:
        raise ClaimError(f"Unknown release {release!r}. Nothing was written.")
    return row


def _record_of(source_record, release) -> int:
    pk = _pk(source_record, SourceRecord)
    owner = None
    if pk is not None:
        owner = (
            SourceRecord.objects.filter(pk=pk)
            .values_list("release_id", flat=True)
            .first()
        )
    if owner is None:
        raise ClaimError(
            f"Unknown source record {source_record!r}. Nothing was written."
        )
    if owner != release["pk"]:
        raise ClaimError(
            f"Source record {pk} belongs to release {owner}, not to release "
            f"{release['pk']}. Nothing was written."
        )
    return pk


def _locked_record(source_record, import_run):
    """The record's release and the run, locked in that order, once they may
    record the record's evidence (record_claims, steps 2 and 3)."""
    record_pk = _pk(source_record, SourceRecord)
    release_id = None
    if record_pk is not None:
        release_id = (
            SourceRecord.objects.filter(pk=record_pk)
            .values_list("release_id", flat=True)
            .first()
        )
    if release_id is None:
        raise ClaimError(
            f"Unknown source record {source_record!r}. Nothing was written."
        )
    release = locked_release(release_id, ClaimError)
    _require_draft(release)
    run = _locked_run(import_run, release)
    # Read again under the release's lock, which its deletion takes too.
    status = (
        SourceRecord.objects.filter(pk=record_pk)
        .values_list("status", flat=True)
        .first()
    )
    if status is None:
        raise ClaimError(
            f"Unknown source record {source_record!r}. Nothing was written."
        )
    if status != RecordStatus.MAPPED:
        raise ClaimError(
            f"Source record {record_pk} is {status}: evidence is recorded only from "
            "a mapped record. Nothing was written."
        )
    _require_internal(release)
    return _release_values(release), run, record_pk


def _locked_run_release(import_run):
    """The run's release and the run, locked in that order, once they may
    record evidence (create_relationships)."""
    pk = _pk(import_run, ImportRun)
    release_id = None
    if pk is not None:
        release_id = (
            ImportRun.objects.filter(pk=pk).values_list("release_id", flat=True).first()
        )
    if release_id is None:
        raise ClaimError(f"Unknown import run {import_run!r}. Nothing was written.")
    release = locked_release(release_id, ClaimError)
    _require_draft(release)
    run = _locked_run(import_run, release)
    _require_internal(release)
    return _release_values(release), run


def _locked_run(import_run, release):
    pk = _pk(import_run, ImportRun)
    run = None
    if pk is not None:
        run = ImportRun.objects.select_for_update().filter(pk=pk).first()
    if run is None:
        raise ClaimError(f"Unknown import run {import_run!r}. Nothing was written.")
    if run.release_id != release.pk:
        raise ClaimError(
            f"Import run {run.pk} belongs to release {run.release_id}, not to "
            f"release {release.pk}: a run records only its own release's evidence. "
            "Nothing was written."
        )
    if run.mode != ImportMode.COMMIT or run.state != ImportState.RUNNING:
        raise ClaimError(
            f"Import run {run.pk} is a {run.mode} run, {run.state}: only a running "
            "commit run records evidence. Nothing was written."
        )
    return run


def _require_draft(release):
    if release.status != ReleaseStatus.DRAFT:
        raise ClaimError(
            f"Release {release.pk} is {release.status}: evidence is recorded only "
            "while its release is a draft. Nothing was written."
        )


def _require_internal(release):
    # An embargo leaves internal use effective.
    if not release.is_synthetic and not is_effective(release, INTERNAL):
        raise ClaimError(
            f"Release {release.pk} has no effective internal approval, so it records "
            "no evidence. Nothing was written."
        )


def _release_values(release) -> dict:
    return {
        "pk": release.pk,
        "is_synthetic": release.is_synthetic,
        "dataset_id": release.dataset_id,
    }


# ── The rows the rules read ─────────────────────────────────────────────────


def _relationship_types(slugs) -> dict:
    """{slug: the type's rules}, with its allowed entity types, in three
    queries."""
    slugs = sorted(slug for slug in slugs if isinstance(slug, str))
    if not slugs:
        return {}
    types = {
        row["slug"]: {**row, "subject_types": set(), "object_types": set()}
        for row in RelationshipType.objects.filter(pk__in=slugs).values(
            "slug", "family", "orientation", "polarity", "self_pairs_allowed"
        )
    }
    for name, side in (
        ("allowed_subject_types", "subject_types"),
        ("allowed_object_types", "object_types"),
    ):
        through = getattr(RelationshipType, name).through
        pairs = through.objects.filter(relationshiptype_id__in=list(types))
        for type_slug, entity_type in pairs.values_list(
            "relationshiptype_id", "entitytype_id"
        ):
            types[type_slug][side].add(entity_type)
    return types


def _entities(ids) -> dict:
    ids = sorted(pk for pk in ids if pk is not None)
    if not ids:
        return {}
    return {
        row["pk"]: row
        for row in Entity.objects.filter(pk__in=ids).values(
            "pk", "entity_type_id", "taxon_id", "status"
        )
    }


def _isoform_links(isoforms) -> dict:
    """{isoform id: entry ids} that verified isoform_of links name: stated by
    a supported assertion of a published or superseded release with an
    effective internal approval (B12)."""
    isoforms = sorted(pk for pk in isoforms if pk is not None)
    if not isoforms:
        return {}
    stated = list(
        EvidenceAssertion.objects.filter(
            relationship__relationship_type_id=ISOFORM_OF,
            relationship__subject_id__in=isoforms,
            outcome=Outcome.SUPPORTED,
            release__status__in=[ReleaseStatus.PUBLISHED, ReleaseStatus.SUPERSEDED],
        )
        .values_list(
            "relationship__subject_id", "relationship__object_id", "release_id"
        )
        .distinct()
    )
    scopes = effective_scopes_by_release({release_id for _, _, release_id in stated})
    links = defaultdict(set)
    for isoform, entry, release_id in stated:
        if INTERNAL in scopes.get(release_id, ()):
            links[isoform].add(entry)
    return links


class _Rows:
    """What the rules read for one call: each kind of row in one query, so
    the number of queries does not grow with the claims or their parts."""

    def __init__(self, **rows):
        self.types = rows.get("types", {})
        self.entities = rows.get("entities", {})
        self.terms = rows.get("terms", {})
        self.publications = rows.get("publications", {})
        self.score_types = rows.get("score_types", {})
        self.taxa = rows.get("taxa", set())
        self.isoform_links = rows.get("isoform_links", {})

    @classmethod
    def load(cls, claims, *, lock):
        """With `lock`, the relationship and score types are held in shared
        mode before they are read (graph.models.share_lock)."""
        type_slugs = {_key(c.relationship_type, RelationshipType) for c in claims}
        score_slugs = {_key(s.score_type, ScoreType) for c in claims for s in c.scores}
        if lock:
            share_lock(RelationshipType, None, type_slugs)
            share_lock(ScoreType, None, score_slugs)
        entity_ids, isoforms, curies, papers, taxa = set(), set(), set(), set(), set()
        for claim in claims:
            for endpoint in (claim.subject, claim.object):
                entity_ids.add(_entity_key(endpoint.entity))
                stated = _entity_key(endpoint.stated_entity)
                entity_ids.add(stated)
                if endpoint.basis == ResolutionBasis.ISOFORM_ENTRY:
                    isoforms.add(stated)
            for term in (
                claim.interaction_type,
                claim.eco_term,
                *(method.term for method in claim.methods),
                *(context.term for context in claim.contexts),
            ):
                curies.add(_curie(term))
            papers |= {_pk(ref.publication, Publication) for ref in claim.publications}
            taxa |= {_taxon_key(context.taxon) for context in claim.contexts}
        return cls(
            types=_relationship_types(type_slugs),
            entities=_entities(entity_ids),
            terms=_terms(curies),
            publications=_publications(papers),
            score_types=_score_types(score_slugs),
            taxa=_taxa(taxa),
            isoform_links=_isoform_links(isoforms),
        )


def _curie(term):
    """A term's CURIE in canonical form, or None."""
    key = _key(term, Term)
    try:
        return None if key is None else normalize_curie(key)
    except TermError:
        return None


def _terms(curies) -> dict:
    curies = sorted(curie for curie in curies if curie is not None)
    if not curies:
        return {}
    return dict(Term.objects.filter(pk__in=curies).values_list("pk", "ontology"))


def _publications(ids) -> dict:
    ids = sorted(pk for pk in ids if pk is not None)
    if not ids:
        return {}
    return {
        row["pk"]: row
        for row in Publication.objects.filter(pk__in=ids).values(
            "pk",
            "is_synthetic",
            "local_dataset_id",
            "pmid",
            "doi_lookup",
            "pmcid",
            "imex_id",
            "local_key",
        )
    }


def _score_types(slugs) -> dict:
    slugs = sorted(slug for slug in slugs if slug is not None)
    if not slugs:
        return {}
    return {
        row["slug"]: row
        for row in ScoreType.objects.filter(pk__in=slugs).values(
            "slug", "value_kind", "direction", "range_min", "range_max", "null_model"
        )
    }


def _taxa(taxids) -> set:
    taxids = sorted(pk for pk in taxids if pk is not None)
    if not taxids:
        return set()
    return set(Taxon.objects.filter(pk__in=taxids).values_list("pk", flat=True))


# ── The rules ───────────────────────────────────────────────────────────────


def _refusal(rule, where, text, error=ClaimError):
    place = f", {where}" if where else ""
    return error(f"Rule {rule} ({RULES[rule]}){place}: {text} Nothing was written.")


@dataclass(frozen=True)
class _End:
    """One endpoint, as the rules read it."""

    side: str
    entity_id: int | None
    planned: PlannedEntity | None
    entity_type: str | None
    taxon_id: int | None
    endpoint: Endpoint

    def __str__(self):
        if self.planned is not None:
            return f"a planned {self.entity_type}"
        return f"entity {self.entity_id}"


@dataclass(frozen=True)
class _Planned:
    """One assertion a claim gives."""

    claim: int
    key: str
    canonical: CanonicalRelationship | None
    fields: dict
    qualifiers: dict
    participants: tuple
    methods: tuple
    publications: tuple
    scores: tuple
    contexts: tuple

    def content(self) -> dict:
        canonical = self.canonical
        return {
            "relationship": (
                None
                if canonical is None
                else (
                    canonical.type_slug,
                    canonical.subject_id,
                    canonical.object_id,
                    canonical.qualifier_key,
                )
            ),
            **{name: self.fields[name] for name in COMPARED_FIELDS},
            "qualifiers": _canonical_json(self.qualifiers),
            **{name: frozenset(getattr(self, name)) for name in PART_KINDS},
        }


def _canonical_json(value) -> str:
    # As canonical JSON text: Python finds True == 1 and 1.0 == 1.
    return json.dumps(value, sort_keys=True)


def _plan(claims, rows, release, *, for_record):
    """The assertions the claims give, every rule checked, and the notes."""
    _check_key_parts(claims)
    planned, notes = [], []
    for n, claim in enumerate(claims, 1):
        check = _Check(n, claim, rows, release, for_record)
        planned.extend(check.assertions())
        notes.extend(check.notes)
    return planned, notes


def _check_key_parts(claims):
    """Rule 11, across a record's claims: each key part is well formed and
    not 'main'; with two claims or more, each claim has one, distinct."""
    several = len(claims) >= 2
    seen = {}
    for n, claim in enumerate(claims, 1):
        part, where = claim.key_part, f"claim {n}"
        if not isinstance(part, str):
            raise _refusal(11, where, f"key_part is text, not {part!r}.")
        if not part:
            if several:
                raise _refusal(
                    11,
                    where,
                    "a record with several claims names each with a key_part, "
                    "distinct within the record; this claim has none.",
                )
            continue
        if part == MAIN:
            raise _refusal(
                11,
                where,
                f"key_part {MAIN!r} is what a blank key part means: leave it blank "
                "or choose another.",
            )
        if not KEY_PART.fullmatch(part):
            raise _refusal(
                11,
                where,
                "key_part is a letter or digit, then up to 63 letters, digits or "
                f". _ : -; not {part!r}.",
            )
        if part in seen:
            raise _refusal(
                11,
                where,
                f"key_part {part!r} is claim {seen[part]}'s too: key parts are "
                "distinct within a record.",
            )
        seen[part] = n


class _Check:
    """One claim's rules. Every refusal names the rule and the claim."""

    def __init__(self, n, claim, rows, release, for_record):
        self.n = n
        self.claim = claim
        self.rows = rows
        self.release = release
        # record_claims writes, so what it names must be stored.
        self.for_record = for_record
        self.where = f"claim {n}" if n is not None else ""
        self.notes = []

    def refuse(self, rule, text):
        raise _refusal(rule, self.where, text)

    def note(self, text):
        self.notes.append(f"Claim {self.n}: {text}")

    def assertions(self) -> list:
        claim = self.claim
        rel_type = self.relationship_type(claim.relationship_type)
        subject = self.endpoint(claim.subject, "subject")
        obj = self.endpoint(claim.object, "object")
        canonical = self.identity(rel_type, subject, obj, claim.stated_directed)
        self.synthetic_endpoints(subject, obj)
        self.closed_values()
        texts = self.texts()
        qualifiers = self.qualifiers()
        effect, sign = self.polarity(rel_type)
        self.bare_unless_supported(effect, sign, qualifiers)
        interaction_type, eco_term, methods = self.terms()
        level = self.level(rel_type, interaction_type)
        participants = self.participants(subject, obj)
        scores = self.scores()
        contexts = self.contexts()
        self.signed_scores(sign, scores)
        self.significance(qualifiers, scores)
        keyed = self.publications()
        fields = {
            "outcome": claim.outcome,
            "evidence_basis": claim.evidence_basis,
            "assertion_method": claim.assertion_method,
            "effect": effect,
            "genetic_sign": sign,
            "eco_term_id": eco_term,
            "interaction_type_id": interaction_type,
            "interaction_level": level,
            **texts,
        }
        stored_scores = tuple((slug, value, raw) for slug, value, raw, _ in scores)
        return [
            _Planned(
                claim=self.n,
                key=key,
                canonical=canonical,
                fields=fields,
                qualifiers=qualifiers,
                participants=participants,
                methods=methods,
                publications=publications,
                scores=stored_scores,
                contexts=contexts,
            )
            for key, publications in keyed
        ]

    # Rules 1-5: the relationship.

    def relationship_type(self, given):
        rel_type = self.rows.types.get(_key(given, RelationshipType))
        if rel_type is None:
            self.refuse(1, f"names no relationship type: {_shown(given)}.")
        return rel_type

    def endpoint(self, endpoint, side) -> _End:
        given = endpoint.entity
        if isinstance(given, PlannedEntity):
            if self.for_record:
                self.refuse(
                    1,
                    f"the {side} is a planned entity, which only check_claims "
                    "accepts: create it first (graph.entities.create_entity).",
                )
            return _End(
                side,
                None,
                given,
                _key(given.entity_type, EntityType),
                _taxon_key(given.taxon),
                endpoint,
            )
        pk = _entity_key(given)
        row = self.rows.entities.get(pk)
        if row is None:
            self.refuse(1, f"the {side} names no entity: {_shown(given)}.")
        if row["status"] in (EntityStatus.MERGED, EntityStatus.RETIRED):
            self.refuse(
                1,
                f"the {side}, entity {pk}, is {row['status']}: an endpoint is active "
                "or provisional, and the evidence of a merged or retired entity "
                "belongs on its successor.",
            )
        return _End(side, pk, None, row["entity_type_id"], row["taxon_id"], endpoint)

    def identity(self, rel_type, subject, obj, stated_directed):
        """Rules 2-5, and the canonical identity; None for a planned
        endpoint."""
        slug, orientation = rel_type["slug"], rel_type["orientation"]
        symmetric = orientation == Orientation.SYMMETRIC
        if symmetric:
            # A symmetric type's two sets are equal (graph.registry); both
            # endpoints are in each, whichever order the source wrote.
            allowed = rel_type["subject_types"] & rel_type["object_types"]
            for end in (subject, obj):
                if end.entity_type not in allowed:
                    self.refuse(
                        2,
                        f"both entities of a {slug} pair are {_either(allowed)}; "
                        f"the {end.side}, {end}, is a {end.entity_type}.",
                    )
        else:
            for end, allowed in (
                (subject, rel_type["subject_types"]),
                (obj, rel_type["object_types"]),
            ):
                if end.entity_type not in allowed:
                    self.refuse(
                        2,
                        f"a {slug} {end.side} is a {_either(allowed)}; {end} is a "
                        f"{end.entity_type}. The order is the claim: a direction "
                        "the type contradicts is refused, never swapped.",
                    )
        if stated_directed is not None:
            if not isinstance(stated_directed, bool):
                self.refuse(
                    3,
                    f"stated_directed is True, False or None; not {stated_directed!r}.",
                )
            if orientation == Orientation.SYMMETRIC and stated_directed:
                self.refuse(
                    3,
                    f"{slug} is symmetric, and the record states a direction: A-B "
                    "is B-A.",
                )
            if orientation == Orientation.DIRECTED and not stated_directed:
                self.refuse(
                    3,
                    f"{slug} is directed, and the record states the pair "
                    "undirected: A->B and B->A are different claims.",
                )
        same = (
            subject.entity_id is not None and subject.entity_id == obj.entity_id
        ) or (subject.planned is not None and subject.planned is obj.planned)
        if same and not rel_type["self_pairs_allowed"]:
            self.refuse(4, f"{slug} relates two different entities; {subject} is both.")
        if subject.entity_id is None or obj.entity_id is None:
            return None
        ends = (subject.entity_id, obj.entity_id)
        if symmetric:
            ends = tuple(sorted(ends))
        return CanonicalRelationship(slug, ends[0], ends[1], not symmetric, "")

    def synthetic_endpoints(self, subject, obj):
        """Rule 13."""
        synthetic = self.release["is_synthetic"]
        for end in (subject, obj):
            construct = end.taxon_id == SYNTHETIC_TAXON
            if synthetic and not construct:
                self.refuse(
                    13,
                    "a synthetic release's evidence joins synthetic constructs only "
                    f"(taxon {SYNTHETIC_TAXON}); the {end.side}, {end}, is of taxon "
                    f"{end.taxon_id}.",
                )
            if construct and not synthetic:
                self.refuse(
                    13,
                    f"synthetic constructs (taxon {SYNTHETIC_TAXON}) appear only in "
                    f"synthetic releases' evidence; the {end.side}, {end}, is one, and "
                    f"release {self.release['pk']} is not synthetic.",
                )

    # Rules 6-10: what the assertion states.

    def closed_values(self):
        """Rule 6: each is independent of the others."""
        for name, choices in (
            ("outcome", Outcome),
            ("evidence_basis", EvidenceBasis),
            ("assertion_method", AssertionMethod),
        ):
            value = getattr(self.claim, name)
            if not isinstance(value, str) or value not in choices.values:
                self.refuse(
                    6, f"{name} is one of {', '.join(choices.values)}; not {value!r}."
                )

    def text(self, rule, what, value, limit, *, blank=False) -> str:
        if not isinstance(value, str):
            self.refuse(rule, f"{what} is text, not {value!r}.")
        if "\x00" in value:
            self.refuse(16, f"{what} contains a NUL character (U+0000).")
        if len(value) > limit:
            self.refuse(
                rule, f"{what} is at most {limit} characters, not {len(value)}."
            )
        if not blank and not value.strip():
            self.refuse(rule, f"{what} is never blank.")
        return value

    def texts(self) -> dict:
        """Rule 16: the source's own words, within their limits."""
        return {
            name: self.text(16, name, getattr(self.claim, name), limit, blank=True)
            for name, limit in (
                ("relationship_as_stated", STATED_TEXT_MAX),
                ("effect_as_stated", STATED_TEXT_MAX),
                ("evidence_group", 255),
            )
        }

    def qualifiers(self) -> dict:
        """Rule 16: a JSON object as a JSON round trip gives it back, in the
        form the database keeps its numbers (_as_stored), which is what is
        validated, stored and compared."""
        value = self.claim.qualifiers
        if not isinstance(value, dict) or not _string_keyed(value):
            self.refuse(
                16,
                "qualifiers are a JSON object whose keys are strings, at every level.",
            )
        try:
            value = _stored_json(value)
        except (TypeError, ValueError, RecursionError) as error:
            self.refuse(
                16,
                f"qualifiers survive a round trip through JSON; these do not: {error}.",
            )
        problem = qualifiers_problem(value)
        if problem:
            self.refuse(16, f"qualifiers {problem}")
        return value

    def polarity(self, rel_type):
        """Rule 7, the values: one encoding per polarity."""
        effect, sign = self.claim.effect, self.claim.genetic_sign
        slug, polarity = rel_type["slug"], rel_type["polarity"]
        if effect is not None:
            if effect == Effect.NOT_APPLICABLE:
                self.refuse(
                    7,
                    "effect not_applicable is the summary for types without effect "
                    "polarity, and is never stated.",
                )
            if (
                not isinstance(effect, str)
                or effect not in EvidenceAssertion.STORED_EFFECTS
            ):
                if isinstance(effect, str) and effect.strip().lower() == "negative":
                    self.refuse(
                        7,
                        "'negative' is ambiguous as an effect (inhibiting? a negative "
                        "genetic interaction? tested and not found?), so it is "
                        "refused, never coerced.",
                    )
                self.refuse(
                    7,
                    "effect is activating, inhibiting, unknown, or None when the "
                    f"source states none; not {effect!r}.",
                )
            if polarity != Polarity.EFFECT:
                self.refuse(
                    7,
                    "only a type with effect polarity carries an effect; "
                    f"{slug} has polarity {polarity}.",
                )
        if sign is not None:
            if not isinstance(sign, str) or sign not in GeneticSign.values:
                self.refuse(
                    7, f"genetic_sign is negative, positive or None; not {sign!r}."
                )
            if polarity != Polarity.GENETIC_SIGN:
                self.refuse(
                    7,
                    "only a type with genetic_sign polarity carries a sign; "
                    f"{slug} has polarity {polarity}.",
                )
        return effect, sign

    def bare_unless_supported(self, effect, sign, qualifiers):
        """Rule 8: only support carries an effect or a sign, and only a
        not-significant result states the criterion it failed."""
        outcome = self.claim.outcome
        if outcome != Outcome.SUPPORTED:
            for name, value in (("an effect", effect), ("a genetic sign", sign)):
                if value is not None:
                    self.refuse(
                        8,
                        f"an assertion that is not supported carries no effect and no "
                        f"sign; this one is {outcome}, and states {name}.",
                    )
        if outcome == Outcome.NOT_SIGNIFICANT:
            if "threshold" not in qualifiers:
                self.refuse(
                    8,
                    "a not_significant assertion records the significance criterion "
                    'it failed in qualifiers["threshold"].',
                )
        elif "threshold" in qualifiers:
            self.refuse(
                8,
                'qualifiers["threshold"] is the criterion a not_significant result '
                f"failed, and is refused on a {outcome} assertion.",
            )

    def term(self, given, what, ontology):
        """Rule 10: a term's CURIE in canonical form, of its ontology. In
        record_claims it is a stored term: no term is created here."""
        key = _key(given, Term)
        if key is None:
            self.refuse(10, f"{what} is a term or its CURIE, not {given!r}.")
        try:
            curie = normalize_curie(key)
        except TermError as error:
            self.refuse(10, f"{what}: {error}")
        if ontology is not None and ontology_of(curie) != ontology:
            self.refuse(
                10, f"{what} is a {ontology} term; {curie} is {ontology_of(curie)}."
            )
        if self.for_record and curie not in self.rows.terms:
            self.refuse(
                10,
                f"{what} names no stored term: {curie}. Claims never create one; "
                "graph.terms.get_or_create_term records a term the source states.",
            )
        return curie

    def terms(self):
        claim = self.claim
        interaction_type = eco_term = None
        if claim.interaction_type is not None:
            interaction_type = self.term(
                claim.interaction_type, "interaction_type", "psi-mi"
            )
        if claim.eco_term is not None:
            eco_term = self.term(claim.eco_term, "eco_term", "eco")
        methods = {}
        for method in claim.methods:
            role = method.role
            if not isinstance(role, str) or role not in MethodRole.values:
                self.refuse(
                    10,
                    f"a method's role is one of {', '.join(MethodRole.values)}; not "
                    f"{role!r}.",
                )
            curie = self.term(
                method.term, f"a {role} method", EvidenceMethod.ROLE_ONTOLOGIES[role]
            )
            # Identical (term, role) pairs collapse.
            methods[(curie, role)] = None
        return interaction_type, eco_term, tuple(methods)

    def level(self, rel_type, interaction_type):
        """Rule 9 (architecture D20, open question B14)."""
        level = self.claim.interaction_level
        slug, family = rel_type["slug"], rel_type["family"]
        if family != RelationshipFamily.PHYSICAL:
            for name, value in (
                ("interaction_level", level),
                ("interaction_type", interaction_type),
            ):
                if value is not None:
                    self.refuse(
                        9,
                        f"{name} belongs to physical-family evidence only; {slug} is "
                        f"{family}.",
                    )
            return None
        if level is None:
            self.refuse(
                9,
                f"{slug} evidence states an interaction_level: not_stated when the "
                "source states no interaction type.",
            )
        if not isinstance(level, str) or level not in InteractionLevel.values:
            self.refuse(
                9,
                f"interaction_level is one of {', '.join(InteractionLevel.values)}; "
                f"not {level!r}.",
            )
        if slug == UNSPECIFIED_PHYSICAL_TYPE and (
            interaction_type is not None or level != InteractionLevel.NOT_STATED
        ):
            self.refuse(
                9, f"{slug} evidence states no interaction type, at level not_stated."
            )
        if interaction_type is None:
            if level not in (InteractionLevel.NOT_STATED, InteractionLevel.ASSOCIATION):
                self.refuse(
                    9,
                    f"{level} rests on the interaction type the source states; "
                    "without one the level is not_stated, or association assigned by "
                    "openPIP (open question B14).",
                )
            if level == InteractionLevel.ASSOCIATION:
                self.note(
                    "level association is assigned by openPIP from the assay the "
                    "source states, not from a stated interaction type (open "
                    "question B14)."
                )
            return level
        crosswalk = PHYSICAL_INTERACTION_LEVELS.get(interaction_type)
        if crosswalk is None:
            self.refuse(
                9,
                f"{interaction_type} is not in the PSI-MI physical crosswalk "
                "(graph.terms.PHYSICAL_INTERACTION_LEVELS), so it is never "
                "physical-interaction evidence.",
            )
        if level != crosswalk[0]:
            self.refuse(
                9,
                f"interaction_level is the crosswalk's level for {interaction_type}, "
                f"{crosswalk[0]}; not {level}.",
            )
        return level

    # Rules 12-15: the parts.

    def participants(self, subject, obj) -> tuple:
        """Rule 12: the two sides as the source stated them; the relationship
        holds the canonical order."""
        rows = []
        for end in (subject, obj):
            endpoint = end.endpoint
            self.text(
                12, f"the {end.side}'s identifier as stated", endpoint.as_stated, 255
            )
            basis = endpoint.basis
            if not isinstance(basis, str) or basis not in ResolutionBasis.values:
                self.refuse(
                    12,
                    f"the {end.side}'s resolution basis is one of "
                    f"{', '.join(ResolutionBasis.values)}; not {basis!r}.",
                )
            stated = None
            if basis == ResolutionBasis.ISOFORM_ENTRY:
                stated = self.isoform(end)
            elif endpoint.stated_entity is not None:
                self.refuse(
                    12,
                    f"the {end.side} names a stated entity: the isoform a source "
                    "named, given exactly when the basis is isoform_entry, and this "
                    f"basis is {basis}.",
                )
            rows.append((end.side, end.entity_id, stated, endpoint.as_stated, basis))
        return tuple(rows)

    def isoform(self, end):
        """B12: a named isoform maps to its entry only through a verified
        isoform_of link, and never by comparing accessions."""
        given = end.endpoint.stated_entity
        pk = _entity_key(given)
        row = self.rows.entities.get(pk)
        if row is None:
            self.refuse(
                12,
                f"an isoform_entry {end.side} names the isoform the source stated "
                f"(stated_entity), and {_shown(given)} names no entity.",
            )
        if row["entity_type_id"] != "protein_isoform":
            self.refuse(
                12,
                f"the isoform an isoform_entry {end.side} names is a "
                f"protein_isoform; entity {pk} is a {row['entity_type_id']}.",
            )
        if end.entity_type != "protein":
            self.refuse(
                12,
                f"an isoform_entry {end.side} is the isoform's protein entry; "
                f"{end} is a {end.entity_type}.",
            )
        entries = self.rows.isoform_links.get(pk, set())
        if end.entity_id not in entries:
            self.refuse(
                12,
                f"isoform {pk} maps to its entry only through an {ISOFORM_OF} link "
                "that a supported assertion of a published or superseded release "
                f"with an effective internal approval states; none links it to "
                f"{end}. Accession prefixes are never compared.",
            )
        others = sorted(entries - {end.entity_id})
        if others:
            self.refuse(
                12,
                f"isoform {pk} is linked to entities {', '.join(map(str, others))} "
                f"as well as to {end}, so mapping it to one entry is ambiguous.",
            )
        return pk

    def scores(self) -> list:
        """Rule 14: one score per type, the raw value kept verbatim, its value
        read by graph.scores' strict grammar."""
        scores, seen = [], set()
        synthetic = self.release["is_synthetic"]
        for score in self.claim.scores:
            slug = _key(score.score_type, ScoreType)
            score_type = self.rows.score_types.get(slug)
            if score_type is None:
                self.refuse(14, f"names no score type: {_shown(score.score_type)}.")
            if slug in seen:
                self.refuse(14, f"one score per score type, and {slug} is given twice.")
            seen.add(slug)
            problem = raw_value_problem(score.raw)
            if problem:
                self.refuse(14, f"{slug}: {problem}")
            source = slug.split(":", 1)[0]
            if synthetic and source not in ("synthetic", "stats"):
                self.refuse(
                    14,
                    "a synthetic release's scores are of synthetic: or stats: types; "
                    f"{slug} is neither.",
                )
            if source == "synthetic" and not synthetic:
                self.refuse(
                    14,
                    f"synthetic: score types belong to synthetic releases; {slug} is "
                    f"one, and release {self.release['pk']} is not synthetic.",
                )
            parsed = parse_score(score.raw)
            if parsed.note:
                self.note(f"score {slug}: {parsed.note}")
            if parsed.value is not None:
                problem = score_value_problem(
                    score_type["value_kind"],
                    score_type["range_min"],
                    score_type["range_max"],
                    parsed.value,
                )
                if problem:
                    self.refuse(14, f"{slug}: {problem}.")
            scores.append((slug, parsed.value, score.raw, score_type))
        return scores

    def contexts(self) -> tuple:
        """Rule 15. An absent facet means not stated."""
        found = {}
        synthetic = self.release["is_synthetic"]
        for context in self.claim.contexts:
            facet = context.facet
            if not isinstance(facet, str) or facet not in ContextFacet.values:
                self.refuse(
                    15,
                    f"a context's facet is one of {', '.join(ContextFacet.values)}; "
                    f"not {facet!r}.",
                )
            raw = self.text(15, f"the {facet} context's raw value", context.raw, 255)
            label = self.text(
                15, f"the {facet} context's label", context.label, 255, blank=True
            )
            term = taxon = None
            if facet == ContextFacet.HOST_TAXON:
                taxon = self.host_taxon(context, raw, synthetic)
            else:
                if context.taxon is not None:
                    self.refuse(
                        15, f"a taxon is stated for host_taxon only; this is {facet}."
                    )
                if context.term is not None:
                    term = self.term(context.term, f"the {facet} context's term", None)
            row = (facet, raw, label, term, taxon)
            held = found.setdefault((facet, raw), row)
            # Identical duplicates collapse; one facet and raw value name one
            # context.
            if held != row:
                self.refuse(
                    15,
                    f"the {facet} context {raw!r} is given twice, with a different "
                    "term, taxon or label.",
                )
        return tuple(found.values())

    def host_taxon(self, context, raw, synthetic):
        if context.term is not None:
            self.refuse(
                15,
                "a host taxon is a registered taxon or the raw value alone, never a "
                "term.",
            )
        taxid = _taxon_key(context.taxon)
        if context.taxon is not None and taxid is None:
            self.refuse(15, f"a host taxon is a taxid, not {context.taxon!r}.")
        if taxid is not None and taxid <= 0:
            self.refuse(
                15,
                f"taxid {taxid} is a PSI-MI pseudo-taxon, which describes the assay "
                "(an assay_system context), never a host taxon.",
            )
        taxon = taxid if taxid in self.rows.taxa else None
        if taxon is None:
            self.note(
                f"host taxon {raw!r} names no registered taxon, so only its raw "
                "value is kept."
            )
        if synthetic and taxon not in (None, SYNTHETIC_TAXON):
            self.refuse(
                15,
                "a synthetic release's host taxon is the synthetic construct "
                f"({SYNTHETIC_TAXON}) or unregistered; not {taxon}.",
            )
        if taxon == SYNTHETIC_TAXON and not synthetic:
            self.refuse(
                15,
                f"the synthetic construct ({SYNTHETIC_TAXON}) is the host taxon of "
                f"synthetic releases only; release {self.release['pk']} is not "
                "synthetic.",
            )
        return taxon

    def signed_scores(self, sign, scores):
        """Rule 7, with the scores: a genetic sign is relative to a null
        model, and agrees with the signed score it rests on."""
        if sign is None:
            return
        signed = [
            (slug, value)
            for slug, value, _, score_type in scores
            if score_type["direction"] == ScoreDirection.SIGNED
            and score_type["null_model"].strip()
        ]
        if not signed:
            self.refuse(
                7,
                "a genetic sign is relative to a null model: it needs a score on the "
                "same assertion whose type is signed and states its null_model.",
            )
        for slug, value in signed:
            if (
                value is not None
                and value != 0
                and (value < 0) != (sign == GeneticSign.NEGATIVE)
            ):
                self.refuse(
                    7,
                    f"the genetic sign {sign} disagrees with the {slug} score "
                    f"{value!r}: negative goes with a value below 0, positive with "
                    "one above.",
                )

    def significance(self, qualifiers, scores):
        """Rule 8, with the scores: a not-significant result fails at least
        one of the criteria it states, where the values involved decide; a
        censored value's bound decides a criterion that every value within it
        meets (R's "< 2.2e-16" meets "p < 0.05")."""
        if self.claim.outcome != Outcome.NOT_SIGNIFICANT:
            return
        criteria = qualifiers["threshold"]
        shape = (
            'qualifiers["threshold"] is a non-empty list of criteria, each '
            '{"score_type": <a score on the assertion>, "operator": one of '
            f"{', '.join(THRESHOLD_OPERATORS)}, "
            '"value": <the threshold as stated, a string>}'
        )
        if not isinstance(criteria, list) or not criteria:
            self.refuse(8, f"{shape}; not {criteria!r}.")
        values = {slug: value for slug, value, _, _ in scores}
        raws = {slug: raw for slug, _, raw, _ in scores}
        held = []
        for criterion in criteria:
            if not isinstance(criterion, dict) or set(criterion) != THRESHOLD_KEYS:
                self.refuse(8, f"{shape}; not {criterion!r}.")
            slug, name = criterion["score_type"], criterion["operator"]
            stated = criterion["value"]
            if not isinstance(slug, str) or slug not in values:
                self.refuse(
                    8,
                    f"a criterion names a score on the assertion; {slug!r} is not one.",
                )
            if not isinstance(name, str) or name not in THRESHOLD_OPERATORS:
                self.refuse(
                    8,
                    f"a criterion's operator is one of {', '.join(THRESHOLD_OPERATORS)}; "
                    f"not {name!r}.",
                )
            if not isinstance(stated, str) or not stated.strip():
                self.refuse(
                    8,
                    "a criterion's value is the threshold as stated, a non-blank "
                    f"string; not {stated!r}.",
                )
            parsed = parse_score(stated)
            value, threshold = values[slug], parsed.value
            if threshold is None:
                held.append(None)
            elif value is not None:
                held.append(THRESHOLD_OPERATORS[name](value, threshold))
            else:
                # A finite threshold carries a note only when it underflows;
                # read as a double it would be tighter than stated, so a bound
                # never settles it.
                bound = censored_bound(raws[slug])
                held.append(
                    True
                    if bound
                    and not parsed.note
                    and _bound_meets(bound, name, threshold)
                    else None
                )
        if all(result is True for result in held):
            self.refuse(
                8,
                "a not_significant result fails at least one criterion it states; "
                "its scores meet every one, so the result is significant.",
            )

    def publications(self) -> list:
        """Rule 11: [(assertion key, publication links)], one assertion per
        paper that reports the claim."""
        links = {}
        for ref in self.claim.publications:
            role = ref.role
            if not isinstance(role, str) or role not in EvidencePublicationRole.values:
                self.refuse(
                    11,
                    "a publication's role is one of "
                    f"{', '.join(EvidencePublicationRole.values)}; not {role!r}.",
                )
            stated = self.stated(ref)
            if ref.publication is None:
                if self.for_record:
                    self.refuse(
                        11,
                        "record_claims cites stored publications "
                        "(graph.publications.get_or_create_publication); a "
                        "publication given by its stated identifiers alone is for "
                        "check_claims.",
                    )
                if not stated:
                    self.refuse(
                        11,
                        "a publication not stored yet is given by the identifiers "
                        "the record states, and these state none.",
                    )
                identity = ("stated", _paper_key(stated))
            else:
                identity = self.paper(ref.publication)
            # Identical (publication, role) pairs collapse; what the record
            # states for one paper is taken together.
            held = links.setdefault((identity, role), {})
            for kind, value in stated.items():
                if held.setdefault(kind, value) != value:
                    self.refuse(
                        11,
                        f"the record states two {kind} values for one paper: "
                        f"{held[kind]!r} and {value!r}.",
                    )
        reported = [
            (identity, stated)
            for (identity, role), stated in links.items()
            if role == EvidencePublicationRole.REPORTED_IN
        ]
        others = [
            (identity, role)
            for identity, role in links
            if role != EvidencePublicationRole.REPORTED_IN
        ]
        part = self.claim.key_part or MAIN
        if len(reported) < 2:
            return [(part, tuple(_link(identity, role) for identity, role in links))]
        keyed = {}
        for identity, stated in reported:
            if not stated:
                self.refuse(
                    11,
                    "a record that reports several papers states the identifiers of "
                    "each reported_in paper (PublicationRef.stated): the assertion "
                    f"keys come from them, and {_paper_name(identity)} has none.",
                )
            paper_key = _paper_key(stated)
            if identity[0] == "row":
                self.own_value(identity[1], stated)
            if paper_key in keyed:
                self.refuse(
                    11, f"two reported_in papers give one paper key, {paper_key}."
                )
            keyed[paper_key] = identity
        return [
            (
                f"{part}|pub:{paper_key}",
                (_link(identity, EvidencePublicationRole.REPORTED_IN),)
                + tuple(_link(other, role) for other, role in others),
            )
            for paper_key, identity in sorted(keyed.items())
        ]

    def stated(self, ref) -> dict:
        """What the record states for a paper, normalized as
        graph.publications stores each identifier."""
        stated = ref.stated
        if not isinstance(stated, dict):
            self.refuse(
                11,
                "what a record states for a paper is a mapping of identifiers; not "
                f"{stated!r}.",
            )
        unknown = sorted(map(repr, set(stated) - set(STATED_IDENTIFIERS)))
        if unknown:
            self.refuse(
                11,
                "what a record states for a paper is keyed by "
                f"{', '.join(STATED_IDENTIFIERS)}; not {', '.join(unknown)}.",
            )
        normalized = {}
        for kind, (normalize, _) in STATED_IDENTIFIERS.items():
            if kind not in stated:
                continue
            value = self.text(
                11, f"the {kind} the record states", stated[kind], 255
            ).strip()
            if normalize is None:
                value = self.text(11, f"the {kind} the record states", value, 100)
            else:
                try:
                    value = normalize(value)
                except PublicationError as error:
                    self.refuse(11, f"the {kind} the record states: {error}")
            normalized[kind] = value
        return normalized

    def paper(self, given):
        pk = _pk(given, Publication)
        row = self.rows.publications.get(pk)
        if row is None:
            self.refuse(11, f"names no publication: {_shown(given)}.")
        release = self.release
        if row["is_synthetic"] != release["is_synthetic"]:
            self.refuse(
                11,
                "a synthetic release cites only synthetic papers, and no other "
                f"release cites one; publication {pk} is "
                f"{'' if row['is_synthetic'] else 'not '}synthetic, release "
                f"{release['pk']} is {'' if release['is_synthetic'] else 'not '}"
                "synthetic.",
            )
        dataset = row["local_dataset_id"]
        if dataset is not None and dataset != release["dataset_id"]:
            self.refuse(
                11,
                "a local publication is cited by its own dataset's releases; "
                f"publication {pk} is local to dataset {dataset}, release "
                f"{release['pk']} is in dataset {release['dataset_id']}.",
            )
        return ("row", pk)

    def own_value(self, pk, stated):
        """The identifier a paper key is built from is the publication's own
        value for that field."""
        kind = next(kind for kind in STATED_IDENTIFIERS if kind in stated)
        name = STATED_IDENTIFIERS[kind][1]
        held = self.rows.publications[pk][name]
        if held != stated[kind]:
            self.refuse(
                11,
                f"the record states {kind} {stated[kind]!r} for publication {pk}, "
                f"whose {name} is {held!r}: a paper key is built from the "
                "publication's own identifier.",
            )


def _bound_meets(bound, name, threshold) -> bool:
    """Whether every value within a censored bound meets a criterion: "<x"
    meets "< t" when x <= t, "<=x" when x < t (x <= t for "<="), and ">"
    the same way up. "abs> t" holds for every value when 0 meets it, and
    otherwise follows from "> t" on a bound that faces up and from "< -t" on
    one that faces down ("abs>=" likewise). A bound never settles "abs<",
    "abs<=" or a criterion that faces the other way."""
    side, limit = bound
    if name in ("abs>", "abs>="):
        if THRESHOLD_OPERATORS[name](0.0, threshold):
            return True
        if side[0] == ">":
            name = name[3:]
        else:
            name, threshold = "<" + name[4:], -threshold
    if name.startswith("abs") or side[0] != name[0]:
        return False
    if side[0] == "<":
        return limit < threshold or (
            limit == threshold and (side == "<" or name == "<=")
        )
    return limit > threshold or (limit == threshold and (side == ">" or name == ">="))


def _paper_key(stated) -> str:
    """The first identifier the record states for a paper: pmid, doi (its
    lookup form), pmcid, imex, then local."""
    kind = next(kind for kind in STATED_IDENTIFIERS if kind in stated)
    return f"{kind}:{stated[kind]}"


def _paper_name(identity) -> str:
    kind, value = identity
    return f"publication {value}" if kind == "row" else f"paper {value}"


def _link(identity, role):
    """A publication link as stored: the publication's id, or for a paper not
    stored yet (check_claims) its paper key."""
    kind, value = identity
    return (value if kind == "row" else f"stated {value}", role)


def _either(slugs) -> str:
    return " or ".join(sorted(slugs)) or "no entity type"


def _check_canonical(n, canonical, rows, release):
    """create_relationships: the identity, checked again from the database,
    is canonical, and on the release's side of the synthetic line."""
    check = _Check(n, None, rows, release, True)
    check.where = f"relationship {n}"
    rel_type = check.relationship_type(canonical.type_slug)
    ends = (
        check.endpoint(Endpoint(canonical.subject_id, "", ""), "subject"),
        check.endpoint(Endpoint(canonical.object_id, "", ""), "object"),
    )
    expected = check.identity(rel_type, *ends, None)
    if expected != canonical:
        check.refuse(
            5,
            f"{canonical.type_slug} {canonical.subject_id}, {canonical.object_id} "
            f"(ordered={canonical.ordered!r}, qualifier_key="
            f"{canonical.qualifier_key!r}) is not canonical; its identity is "
            f"{expected} (ordered={expected.ordered}, qualifier_key='').",
        )
    check.synthetic_endpoints(*ends)


# ── Reading back and writing ────────────────────────────────────────────────


@dataclass(frozen=True)
class _Held:
    assertion: EvidenceAssertion
    content: dict


def _stored_assertions(record_pk) -> dict:
    """{key: assertion and content} of every assertion the record holds,
    with its parts, in six queries whatever their number."""
    assertions = list(
        EvidenceAssertion.objects.filter(source_record_id=record_pk)
        .select_related("relationship")
        .order_by("pk")
    )
    if not assertions:
        return {}
    parts = defaultdict(lambda: {kind: set() for kind in PART_KINDS})
    for kind, model, names in (
        (
            "participants",
            AssertionParticipant,
            (
                "side",
                "entity_id",
                "stated_entity_id",
                "identifier_as_stated",
                "resolution_basis",
            ),
        ),
        ("methods", EvidenceMethod, ("term_id", "role")),
        ("publications", EvidencePublication, ("publication_id", "role")),
        ("scores", EvidenceScore, ("score_type_id", "value", "raw_value")),
        (
            "contexts",
            EvidenceContext,
            ("facet", "raw_value", "label", "term_id", "taxon_id"),
        ),
    ):
        rows = model.objects.filter(assertion__source_record_id=record_pk)
        for assertion_id, *values in rows.values_list("assertion_id", *names):
            parts[assertion_id][kind].add(tuple(values))
    held = {}
    for assertion in assertions:
        relationship = assertion.relationship
        held[assertion.assertion_key] = _Held(
            assertion,
            {
                "relationship": (
                    relationship.relationship_type_id,
                    relationship.subject_id,
                    relationship.object_id,
                    relationship.qualifier_key,
                ),
                **{name: getattr(assertion, name) for name in COMPARED_FIELDS},
                "qualifiers": _canonical_json(assertion.qualifiers),
                **{
                    kind: frozenset(values)
                    for kind, values in parts[assertion.pk].items()
                },
            },
        )
    return held


def _compare(record_pk, planned, held):
    """Refuse unless the claims give exactly the assertions the record holds:
    the same keys, each with the same content."""
    wanted = {spec.key: spec.content() for spec in planned}
    added = sorted(set(wanted) - set(held))
    missing = sorted(set(held) - set(wanted))
    differing = []
    for key in sorted(set(wanted) & set(held)):
        names = [
            name
            for name, value in wanted[key].items()
            if value != held[key].content[name]
        ]
        if names:
            differing.append(f"{key} ({', '.join(names)})")
    if not (added or missing or differing):
        return
    found = []
    for what, keys in (
        ("added", added),
        ("missing", missing),
        ("differing", differing),
    ):
        if keys:
            found.append(f"{what} {', '.join(keys)}")
    raise ClaimConflict(
        f"Source record {record_pk} already holds assertions that these claims do "
        f"not reproduce: {'; '.join(found)}. A record's evidence is recorded once "
        "and never changed: recording it again is a no-op only when every "
        "assertion is unchanged, and a correction is a new release. Nothing was "
        "written."
    )


def _stored_relationships(canonicals) -> dict:
    """{canonical: relationship} of those stored, in one query."""
    canonicals = set(canonicals)
    if not canonicals:
        return {}
    rows = Relationship.objects.filter(
        relationship_type_id__in={c.type_slug for c in canonicals},
        subject_id__in={c.subject_id for c in canonicals},
        object_id__in={c.object_id for c in canonicals},
        qualifier_key__in={c.qualifier_key for c in canonicals},
    )
    by_identity = {
        (
            row.relationship_type_id,
            row.subject_id,
            row.object_id,
            row.qualifier_key,
        ): row
        for row in rows
    }
    found = {}
    for canonical in canonicals:
        row = by_identity.get(_identity_of(canonical))
        if row is not None:
            found[canonical] = row
    return found


def _identity_of(canonical) -> tuple:
    return (
        canonical.type_slug,
        canonical.subject_id,
        canonical.object_id,
        canonical.qualifier_key,
    )


def _relationships(canonicals) -> dict:
    """{canonical: (relationship, created)}: the stored ones, and the missing
    ones inserted in canonical order (ON CONFLICT DO NOTHING, then a
    re-select), so concurrent writers wait on a shared pair in one order."""
    canonicals = sorted(set(canonicals), key=_identity_of)
    found = _stored_relationships(canonicals)
    missing = [canonical for canonical in canonicals if canonical not in found]
    created = set()
    if missing:
        candidates = {
            canonical: Relationship(
                relationship_type_id=canonical.type_slug,
                subject_id=canonical.subject_id,
                object_id=canonical.object_id,
                ordered=canonical.ordered,
                qualifier_key=canonical.qualifier_key,
            )
            for canonical in missing
        }
        Relationship.objects.bulk_create(candidates.values(), ignore_conflicts=True)
        again = _stored_relationships(missing)
        for canonical in missing:
            row = again.get(canonical)
            if row is None:
                raise ClaimError(
                    f"Relationship {canonical} was not written: a database "
                    "constraint refused it. Nothing was written."
                )
            found[canonical] = row
            # bulk_create() stamped the candidate's created_at: the row found
            # is this call's own exactly when it carries the same stamp.
            if row.created_at == candidates[canonical].created_at:
                created.add(canonical)
    return {
        canonical: (found[canonical], canonical in created) for canonical in canonicals
    }


def _write(planned, release, run, record_pk) -> tuple:
    """Step 6: the relationships, each assertion with save(), and each kind
    of part with one bulk_create() (no conflict is ignored)."""
    relationships = _relationships([spec.canonical for spec in planned])
    recorded, parts = [], defaultdict(list)
    for spec in planned:
        relationship, relationship_created = relationships[spec.canonical]
        assertion = EvidenceAssertion(
            relationship=relationship,
            release_id=release["pk"],
            source_record_id=record_pk,
            import_run_id=run.pk,
            assertion_key=spec.key,
            qualifiers=spec.qualifiers,
            **spec.fields,
        )
        assertion.save()
        recorded.append(
            RecordedAssertion(assertion, True, relationship, relationship_created)
        )
        common = {"assertion": assertion, "release_id": release["pk"]}
        for side, entity, stated, as_stated, basis in spec.participants:
            parts[AssertionParticipant].append(
                AssertionParticipant(
                    side=side,
                    entity_id=entity,
                    stated_entity_id=stated,
                    identifier_as_stated=as_stated,
                    resolution_basis=basis,
                    **common,
                )
            )
        for term, role in spec.methods:
            parts[EvidenceMethod].append(
                EvidenceMethod(term_id=term, role=role, **common)
            )
        for publication, role in spec.publications:
            parts[EvidencePublication].append(
                EvidencePublication(publication_id=publication, role=role, **common)
            )
        for score_type, value, raw in spec.scores:
            parts[EvidenceScore].append(
                EvidenceScore(
                    score_type_id=score_type, value=value, raw_value=raw, **common
                )
            )
        for facet, raw, label, term, taxon in spec.contexts:
            parts[EvidenceContext].append(
                EvidenceContext(
                    facet=facet,
                    raw_value=raw,
                    label=label,
                    term_id=term,
                    taxon_id=taxon,
                    **common,
                )
            )
    for model in (
        AssertionParticipant,
        EvidenceMethod,
        EvidencePublication,
        EvidenceScore,
        EvidenceContext,
    ):
        if parts[model]:
            model.objects.bulk_create(parts[model])
    return tuple(recorded)

"""The shared mapping: what each record of a release maps to, for the dry run
(G4) and the commit (G5) (architecture §4.1, §8).

plan_batch maps a batch of the records an adapter read, with the adapter, and
plans each one, read-only:

- each endpoint gets a target. Existing is a stored entity, found by its
  primary identifier (basis primary) or through the reviewed resolution file
  (basis curated); Create is an entity a commit with --create-entities would
  create; Unmapped is unresolved or ambiguous, with the reason and the
  candidates. The rules apply in this order, and each refusal rejects the
  record with its code, never leaving it silently unresolved:
  1. the namespace, the entity type and the taxon are in the registry, and
     the value is an identifier of that type and taxon in the namespace
     (graph.entities.identifier_problem);
  2. in a global namespace that defines identity, the value's primary
     holder, found without a taxon filter, has the stated type and taxon
     (taxon_contradiction otherwise);
  3. the resolver's status (graph.resolution, filtered by the namespace, the
     taxon and the type): resolved maps (a resolution entry naming another
     entity contradicts it, and one naming the same entity is redundant); a
     candidate or an ambiguous identifier maps only through an entry naming
     one of its candidates, and is otherwise listed with them; an unmatched
     one maps through an entry, or is a planned creation when
     graph.entities.creation_problem passes, and is otherwise unresolved,
     with the reason. Cross-references and aliases map with RF1a.
- each cited paper is a stored publication (graph.publications) or one a
  commit would create, and each term the claims name a stored term or one
  the release manifest declares;
- the claims are checked by G3's check_claims: with the stored record when
  every endpoint is a stored entity and the record holds evidence, and by
  the rules alone otherwise;
- the status follows: rejected (any error: the adapter's, a rule's above,
  G3's, a synthetic violation) before ambiguous, unresolved and mapped. A
  planned creation maps only with --create-entities.

A run (MappingRun) resolves each identifier once, in groups of one
namespace, taxon and type (graph.resolution.resolve_many), and plans one
PlannedEntity per entity a commit would create, shared by every endpoint
that names it on either side of any record, so G3's self-pair rule sees one
entity; an unmapped identifier likewise gets one placeholder. A value the
file states at two types or taxa that would create one entity creates
none: every row that states it is rejected, as a conflicting duplicate
rejects both versions (D12), and file order decides nothing. A planned
entity's label is the first non-blank label a mapped row gives it, or else
its identifier as written; a later, different label is reported and never
used. An existing entity's label never changes: a different stated label is
a note.

A later batch can change how an earlier batch's rows map: a later line of a
record id rejects its first occurrence, and a value stated at another taxon
refuses what earlier rows planned. The run then sets read_again, and the
dry run reads the file a second time (MappingRun.restart), so its report
and records follow from the whole file; what was resolved is kept, so the
second reading finds nothing new.

A synthetic release (§11c) is checked on every readable row, rejected and
unstored ones included: the adapter checks namespaces, taxa, SYN- labels,
papers (only the manifest's, so no PMID or DOI) and host taxa
(Adapter.synthetic_issues for a row that is not stored but whose cells
split into the header's columns), and the mapping the taxon of each
resolution entry a row's identifiers meet. Any violation rejects the row
and refuses the run.

A run keeps every identifier the file states, as written, from batch to
batch: once the file has stated more than limits.MAX_IDENTIFIERS distinct
ones (refused ones included), plan_batch refuses the file with a FileError
(too_many_identifiers) naming the first line of the batch that passed it.

The commit (graph.ingest.commit) plans each batch as the dry run did, and
reads each plan's stored record (stored_id, stored_status). Its writing
reading plans with check=False: G3's check is not run and no label is
chosen, so a planned entity keeps the label and written value of the last
planning reading, which the dry run's report showed. The entities and
publications it writes are kept in MappingRun.entity_ids and paper_ids,
which entity_of and checked_claims then give G3 instead of the planned
entity or None; a dry run never fills them.

write_records is the dry run's one write besides its import run: one short
transaction per batch that writes the batch's source records, after the
release's state is checked again.
"""

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field, replace

from django.db import transaction
from django.utils import timezone

from graph.approvals import INTERNAL, is_effective
from graph.claims import (
    ClaimConflict,
    ClaimError,
    Endpoint,
    PlannedEntity,
    PublicationRef,
    check_claims,
)
from graph.entities import creation_problems, identifier_problems
from graph.identity import VALUE_LENGTH, identity_key, normalize, scope_key
from graph.models import (
    SYNTHETIC_TAXON,
    Entity,
    EntityIdentifier,
    EvidenceAssertion,
    IdentifierRole,
    ImportMode,
    ImportRun,
    ImportState,
    RecordStatus,
    ReleaseStatus,
    ResolutionBasis,
    Scope,
    SourceRecord,
    Term,
)
from graph.preconditions import locked_release, typed_graph_enabled
from graph.publications import doi_lookup, find_publications
from graph.registry import REGISTRY_VERSION, registry_state
from graph.resolution import AMBIGUOUS, CANDIDATE, RESOLVED, UNMATCHED, resolve_many

from . import limits
from .protocol import (
    FileError,
    Issue,
    duplicate_issues,
    issues_json,
    note,
    quoted,
    rejects,
)
from .resolutions import NO_RESOLUTIONS

SIDES = ("subject", "object")
# The candidates the resolver returns for one identifier. A resolution entry
# maps a candidate or an ambiguous identifier only to one of them, so an
# identifier with more matches than this maps through none past them.
CANDIDATE_LIMIT = 100
# The candidates a record's note describes in full; it names the others by
# their entity ids.
NOTED_CANDIDATES = 20
# The column of the endpoint each identifier refusal concerns.
_COLUMN = {
    "unknown_namespace": "namespace",
    "namespace_not_for_type": "type",
    "unknown_entity_type": "type",
    "unknown_taxon": "taxon",
    "bad_taxon": "taxon",
    "taxon_contradiction": "taxon",
}
# What the adapter already refused of an endpoint: its cells say why.
_REFUSED_CELLS = {"bad_taxon": "taxon", "hgnc_without_prefix": "id"}
# How G3 names a rule in a refusal, the claim it refuses, and a claim in a
# note (graph.claims._refusal and _Check.note).
_RULE = re.compile(r"Rule (\d+) \(")
_REFUSED_CLAIM = re.compile(r"Rule \d+ \([^)]*\), claim (\d+): ")
_CLAIM_NOTE = re.compile(r"Claim \d+: ")


class RunStopped(Exception):
    """The run stops: the release changed under it (it is no longer a draft,
    a commit run opened, its internal approval lapsed, the registry changed,
    or it was registered again under another adapter version), or its stored
    records do not come from this file. What earlier batches wrote stays,
    unless a registration deleted it."""


# ── Targets ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Existing:
    """An endpoint mapped to a stored entity."""

    entity_id: int
    # A ResolutionBasis value: primary, or curated (the resolution file).
    basis: str
    # The entity's label, which never changes.
    label: str = ""


@dataclass(frozen=True)
class Create:
    """An entity a commit with --create-entities would create."""

    namespace: str
    # As written, by the first row that states it.
    value: str
    lookup: str
    entity_type: str
    taxon: int | None
    # The label the planned entity has so far (MappingRun.planned).
    label: str
    identity_key: str


@dataclass(frozen=True)
class Unmapped:
    """An endpoint that maps to no entity."""

    # RecordStatus.UNRESOLVED or RecordStatus.AMBIGUOUS.
    status: str
    # candidate, or the code of what refuses a creation (graph.entities).
    code: str
    reason: str
    # Of the resolver (candidate_json), when it found any.
    candidates: tuple = ()


@dataclass(frozen=True)
class ExistingPaper:
    """A cited paper that a stored publication names."""

    pk: int


@dataclass(frozen=True)
class NewPaper:
    """A cited paper a commit would create: by its PMID or DOI, or a manifest
    paper by its local key, with the manifest's title and year."""

    kind: str
    value: str
    title: str = ""
    year: int | None = None
    is_synthetic: bool = False


# ── Plans ───────────────────────────────────────────────────────────────────


@dataclass
class EndpointPlan:
    """One endpoint of one claim of a record."""

    side: str
    # As the record states it (graph.ingest.protocol.EntityRef).
    stated: object
    # (namespace, lookup, version, entity type, taxon), once rule 1 passes;
    # the version is "" except in a namespace that versions its values.
    key: tuple | None
    # None when a rule refused the endpoint: the record is rejected.
    target: Existing | Create | Unmapped | None


@dataclass
class PaperPlan:
    """One paper a claim cites."""

    # The identifier the record states: {"pmid"|"doi"|"local": value}.
    stated: dict
    target: ExistingPaper | NewPaper


@dataclass
class RecordPlan:
    """What one line of the file maps to."""

    record: object
    # Unstored: the line has no trustworthy record id or values (a reading
    # issue, or a later line of a record id already stored).
    unstored: bool = False
    # The record stored for this key before the batch, if any, and its
    # status (a RecordStatus value) as stored.
    stored_id: int | None = None
    stored_status: str | None = None
    claims: tuple = ()
    endpoints: list = field(default_factory=list)
    papers: list = field(default_factory=list)
    # The terms the claims name that are not stored: (CURIE, the manifest's
    # label), which a commit records.
    terms: tuple = ()
    issues: list = field(default_factory=list)
    status: str | None = None
    # Per claim, from check_claims: (its canonical relationship, or None for
    # a planned endpoint; whether that relationship is stored).
    relationships: tuple = ()
    # check_claims': True when the record holds exactly these assertions.
    assertions_exist: bool | None = None
    # G3's notes on the claims.
    notes: tuple = ()
    # A later line that states a stored record's id with other content, in
    # a conflict this run has not found yet: the issue the record of its
    # first occurrence gets (in write_records). One an earlier reading of
    # the run found, the first occurrence's own plan carried.
    first_key: str | None = None
    first_issue: Issue | None = None
    # A stored first occurrence's conflicting_duplicate issues, which a
    # later line found in an earlier run or batch: plan_batch adds them
    # right after the adapter's issues, where _read adds one found in the
    # same batch, so the record is rejected before G3's check.
    carried: tuple = ()

    @property
    def would_map(self) -> bool:
        """Whether a commit with --create-entities would import the record:
        no error, and every endpoint a stored or planned entity."""
        return (
            not self.unstored
            and not rejects(self.issues)
            and all(
                isinstance(endpoint.target, (Existing, Create))
                for endpoint in self.endpoints
            )
        )


@dataclass
class IdentifierInfo:
    """One identifier the run's rows state, as the report lists it."""

    namespace: str
    lookup: str
    version: str
    entity_type: str
    taxon: int | None
    # The resolver's status; None when rule 2 refused it first.
    status: str | None = None
    target: Existing | Create | Unmapped | None = None
    # (code, reason) of a refusal that rejects every record stating it.
    refusal: tuple | None = None
    # (code, reason) of the note every record stating it gets.
    note: tuple | None = None
    candidates: tuple = ()
    # The resolution entry that applies to it, if any.
    entry: object = None
    written: list = field(default_factory=list)
    lines: list = field(default_factory=list)
    records: int = 0

    def seen(self, written, line):
        if (
            written not in self.written
            and len(self.written) < limits.REPORT_ENTRY_LINES
        ):
            self.written.append(written)
        if len(self.lines) < limits.REPORT_ENTRY_LINES:
            self.lines.append(line)
        self.records += 1


@dataclass
class PlannedInfo:
    """One entity a commit with --create-entities would create."""

    entity: PlannedEntity
    create: Create
    # The first non-blank label a mapped row gives it, and its line.
    label: str | None = None
    label_line: int | None = None
    # The identifier as the first mapped row wrote it.
    written: str | None = None
    # Other labels mapped rows give it: {label: first line}, at most
    # NOTED_CANDIDATES of them, with their count.
    other_labels: dict = field(default_factory=dict)
    other_label_count: int = 0
    lines: list = field(default_factory=list)
    records: int = 0
    # The identifiers that plan it, by their keys, and each (type, taxon)
    # the file states it at, with the first line that does: two of them
    # contradict, and the entity is not created.
    infos: list = field(default_factory=list)
    stated: dict = field(default_factory=dict)
    contradicted: bool = False

    @property
    def chosen(self) -> str:
        return self.label or self.written or self.create.value.strip()


@dataclass
class _Refused:
    """An identifier rule 1 refused, by what the row states. A cell longer
    than limits.QUOTED_VALUE_CHARS is kept as quoted() quotes it, cut short
    (the record's raw row keeps it whole), so the run holds no long value
    for its whole length."""

    code: str
    reason: str
    namespace: str = ""
    value: str = ""
    entity_type: str = ""
    taxon: int | None = None
    written: list = field(default_factory=list)
    lines: list = field(default_factory=list)
    records: int = 0


@dataclass
class _First:
    line: int
    sha256: str
    # The plan of an earlier line of the same batch, or None for a stored
    # record.
    plan: RecordPlan | None


# ── A run's mapping ─────────────────────────────────────────────────────────


class MappingRun:
    """What one run's mapping keeps from batch to batch: the adapter and its
    context (graph.ingest.context.RunContext), the release, the resolution
    file and --create-entities, and each identifier, paper and term,
    resolved once."""

    def __init__(
        self,
        *,
        adapter,
        ctx,
        release,
        resolutions=NO_RESOLUTIONS,
        create_entities=False,
    ):
        self.adapter = adapter
        self.ctx = ctx
        # The release as read when the run started: pk, dataset_id,
        # is_synthetic.
        self.release = release
        self.resolutions = resolutions
        self.create_entities = bool(create_entities)
        # Rule 1, by _written_key(namespace, value as written, type, taxon):
        # the key (namespace, lookup, version, type, taxon), or a _Refused.
        self._stated = {}
        # By (namespace, lookup, version, type, taxon). A versioned
        # namespace's identifier is resolved with the version stated, so a
        # version mismatch stays a candidate (TEST_PLAN §5.1).
        self.identifiers = {}
        # Identifiers rule 1 refused, by _written_key(...).
        self.refused = {}
        # {record key: [issue]}: the conflicting_duplicate issues later lines
        # gave stored first occurrences, which a second reading carries.
        self.contradicted = {}
        # A later batch changed how an earlier batch's rows map: the file is
        # read again (restart).
        self.read_again = False
        # {identity key: PlannedInfo}, and one placeholder per unmapped
        # (namespace, lookup, type, taxon).
        self.planned = {}
        self._placeholders = {}
        # How the run used each resolution entry, by its index: a set of
        # "mapped", "redundant", "contradicted", "not_a_candidate".
        self.entry_uses = defaultdict(set)
        # {(kind, value): ExistingPaper or NewPaper}; value as stored (a
        # DOI's lookup form). Stored local papers whose title or year the
        # manifest states otherwise: {key: (stored title, stored year)}.
        self.papers = {}
        self.paper_differences = {}
        # {CURIE: the stored term's label, or None when not stored}.
        self.terms = {}
        # What a commit wrote: {identity key: the entity's pk} for planned
        # entities, and {paper identity: the publication's pk}. Empty unless
        # a commit fills them; entity_of and checked_claims read them.
        self.entity_ids = {}
        self.paper_ids = {}

    def restart(self):
        """Forget what the rows gave, and keep what was decided: the file is
        read again from its first line, and each identifier, planned entity
        and refusal counts its rows anew."""
        for info in self.identifiers.values():
            info.written, info.lines, info.records = [], [], 0
        for refused in self.refused.values():
            refused.written, refused.lines, refused.records = [], [], 0
        for planned in self.planned.values():
            planned.label = planned.label_line = planned.written = None
            planned.other_labels, planned.other_label_count = {}, 0
            planned.lines, planned.records = [], 0
        self.read_again = False

    # ── Placeholders and planned entities ──

    def _placeholder(self, key) -> PlannedEntity:
        namespace, lookup, _, entity_type, taxon = key
        found = self._placeholders.get((namespace, lookup, entity_type, taxon))
        if found is None:
            found = PlannedEntity(entity_type, taxon)
            self._placeholders[namespace, lookup, entity_type, taxon] = found
        return found

    def entity_of(self, endpoint):
        """What check_claims gets for an endpoint: an entity id (a planned
        entity's once a commit created it, entity_ids), the planned entity,
        or the unmapped identifier's placeholder."""
        target = endpoint.target
        if isinstance(target, Existing):
            return target.entity_id
        if isinstance(target, Create):
            created = self.entity_ids.get(target.identity_key)
            if created is not None:
                return created
            return self.planned[target.identity_key].entity
        return self._placeholder(endpoint.key)

    # ── Rules 1 to 3, per identifier ──

    def resolve_identifiers(self, refs):
        """Apply rules 1 to 3 to every identifier these (EntityRef, line)
        pairs state that the run has not met yet: a constant number of
        queries per namespace, taxon and type."""
        registry = self.ctx.registry
        # {written: the first line stating it}, for this batch only.
        stated = {}
        for ref, line in refs:
            written = (ref.namespace, ref.value, ref.entity_type, ref.taxon)
            if _written_key(*written) not in self._stated:
                stated.setdefault(written, line)
        pending = []
        for written, line in stated.items():
            namespace, value, entity_type, taxon = written
            if namespace not in registry.namespaces:
                self._refuse(
                    written,
                    "unknown_namespace",
                    f"namespace {quoted(namespace)} is not in the registry.",
                )
            elif entity_type not in registry.entity_types:
                self._refuse(
                    written,
                    "unknown_entity_type",
                    f"entity type {quoted(entity_type)} is not in the registry.",
                )
            elif taxon is not None and taxon not in registry.taxa:
                self._refuse(
                    written, "unknown_taxon", f"taxon {taxon} is not in the registry."
                )
            else:
                pending.append((written, line))
        problems = (
            identifier_problems(
                (entity_type, taxon, namespace, value)
                for (namespace, value, entity_type, taxon), _ in pending
            )
            if pending
            else []
        )
        first_written = {}
        for (written, line), problem in zip(pending, problems):
            if problem:
                self._refuse(written, problem.code, str(problem))
                continue
            namespace, value, entity_type, taxon = written
            lookup, version = normalize(registry.namespaces[namespace], value)
            key = (namespace, lookup, version, entity_type, taxon)
            self._stated[_written_key(*written)] = key
            if key not in self.identifiers:
                first_written.setdefault(key, (value.strip(), line))
        if first_written:
            self._resolve(first_written)

    def _refuse(self, written, code, reason):
        key = _written_key(*written)
        refused = self.refused.get(key)
        if refused is None:
            namespace, value, entity_type, taxon = written
            refused = self.refused[key] = _Refused(
                code, reason, _kept(namespace), _kept(value), _kept(entity_type), taxon
            )
        self._stated[key] = refused

    def _resolve(self, first_written):
        """Rules 2 and 3 for new identifiers: {key: (the value as the first
        row wrote it, that row's line)}."""
        keys = list(first_written)
        for key in keys:
            namespace, lookup, version, entity_type, taxon = key
            info = self.identifiers[key] = IdentifierInfo(
                namespace, lookup, version, entity_type, taxon
            )
            info.entry = self.resolutions.get(*_entry_key(key))
        unchecked = self._rule_two(keys)
        found = self._rule_three(unchecked)
        creations, labels, primaries = [], set(), set()
        for key in unchecked:
            info = self.identifiers[key]
            resolution = found[key]
            info.status = resolution.status
            self._decide(info, resolution, creations, labels)
            primaries |= {candidate.entity.pk for candidate in resolution.candidates}
        known = self._entities(labels, primaries)
        for key, resolution in found.items():
            info = self.identifiers[key]
            # Every candidate the resolver gives (at most CANDIDATE_LIMIT):
            # the report file and the record's note list them all.
            info.candidates = tuple(
                candidate_json(candidate, known) for candidate in resolution.candidates
            )
            self._finish(info, resolution, known)
        self._plan_creations(
            [(info, *first_written[_key_of(info)]) for info in creations]
        )

    def _rule_two(self, keys) -> list:
        """Rule 2: in a global namespace that defines identity, the value's
        primary holder, of any taxon and type, is of the stated ones. The
        keys that pass."""
        registry = self.ctx.registry
        by_namespace = defaultdict(list)
        for key in keys:
            rules = registry.namespaces[key[0]]
            if rules.scope == Scope.GLOBAL and rules.defines_identity:
                by_namespace[key[0]].append(key)
        refused = set()
        for namespace, group in sorted(by_namespace.items()):
            terms = sorted({_term(key) for key in group})
            found = dict(
                zip(
                    terms,
                    resolve_many(terms, namespace=namespace, limit=CANDIDATE_LIMIT),
                )
            )
            for key in group:
                _, lookup, _, entity_type, taxon = key
                for candidate in found[_term(key)].candidates:
                    entity = candidate.entity
                    if not _primary_holder(candidate, namespace):
                        continue
                    if (entity.entity_type_id, entity.taxon_id) == (entity_type, taxon):
                        continue
                    info = self.identifiers[key]
                    if info.entry is not None:
                        self.entry_uses[info.entry.index].add("refused")
                    info.refusal = (
                        "taxon_contradiction",
                        f"{namespace} {lookup} is the primary identifier of entity "
                        f"{entity.pk}, a {entity.entity_type_id} of taxon "
                        f"{entity.taxon_id}; the row states a {entity_type} of taxon "
                        f"{taxon}.",
                    )
                    refused.add(key)
                    break
        return [key for key in keys if key not in refused]

    def _rule_three(self, keys) -> dict:
        """{key: Resolution}, from one resolve_many per namespace, taxon and
        type."""
        groups = defaultdict(list)
        for key in keys:
            namespace, _, _, entity_type, taxon = key
            groups[namespace, entity_type, taxon].append(key)
        found = {}
        for (namespace, entity_type, taxon), group in sorted(
            groups.items(), key=lambda item: _sortable(item[0])
        ):
            results = resolve_many(
                [_term(key) for key in group],
                namespace=namespace,
                taxon=taxon,
                entity_type=entity_type,
                limit=CANDIDATE_LIMIT,
            )
            found.update(zip(group, results))
        return found

    def _decide(self, info, resolution, creations, labels):
        """Rule 3's target, as far as the resolver and the entry decide it;
        creations and the curated entities' labels are read after."""
        entry = info.entry
        status = resolution.status
        if status == RESOLVED:
            entity = resolution.candidates[0].entity
            if entry is not None and entry.entity != entity.pk:
                self.entry_uses[entry.index].add("contradicted")
                info.refusal = (
                    "resolution_contradicts",
                    f"{info.namespace} {info.lookup} resolves to entity {entity.pk}, "
                    f"and entry {entry.index} of the resolution file maps it to "
                    f"entity {entry.entity}.",
                )
                return
            if entry is not None:
                self.entry_uses[entry.index].add("redundant")
            info.target = Existing(entity.pk, ResolutionBasis.PRIMARY, entity.label)
            return
        if status in (CANDIDATE, AMBIGUOUS):
            ids = {candidate.entity.pk for candidate in resolution.candidates}
            if entry is not None and entry.entity in ids:
                self.entry_uses[entry.index].add("mapped")
                labels.add(entry.entity)
                return
            if entry is not None:
                self.entry_uses[entry.index].add("not_a_candidate")
            return
        if status == UNMATCHED:
            if entry is not None:
                self.entry_uses[entry.index].add("mapped")
                labels.add(entry.entity)
                return
            creations.append(info)

    def _entities(self, labels, primaries) -> dict:
        """{entity id: (label, primary identifier)} for the curated entities
        and the candidates, in two queries."""
        known = {}
        ids = sorted(labels | primaries)
        if not ids:
            return known
        names = dict(Entity.objects.filter(pk__in=ids).values_list("pk", "label"))
        primary = {
            entity_id: f"{namespace}:{value}"
            for entity_id, namespace, value in EntityIdentifier.objects.filter(
                entity_id__in=ids, role=IdentifierRole.PRIMARY
            ).values_list("entity_id", "namespace_id", "value")
        }
        for pk in ids:
            known[pk] = (names.get(pk, ""), primary.get(pk))
        return known

    def _finish(self, info, resolution, known):
        """The target and note of a candidate, ambiguous or curated
        identifier, once the candidates are described."""
        entry = info.entry
        stated = _identifier(info)
        if info.status in (CANDIDATE, AMBIGUOUS):
            if entry is not None and entry.entity in {
                candidate.entity.pk for candidate in resolution.candidates
            }:
                info.target = Existing(
                    entry.entity,
                    ResolutionBasis.CURATED,
                    known.get(entry.entity, ("", None))[0],
                )
                info.note = ("curated_mapping", _curated(info, entry))
                return
            status = (
                RecordStatus.AMBIGUOUS
                if info.status == AMBIGUOUS
                else RecordStatus.UNRESOLVED
            )
            reason = (
                f"{stated} is {info.status}: "
                f"{_listed(info.candidates, resolution)}. Nothing accepted "
                + (
                    "one: a candidate maps only through a reviewed resolution "
                    "file until RF1a."
                    if entry is None
                    else f"one: entry {entry.index} of the resolution file names "
                    f"entity {entry.entity}, which is not among them."
                )
            )
            info.target = Unmapped(status, "candidate", reason, info.candidates)
            info.note = ("candidate", reason)
            return
        if info.status == UNMATCHED and entry is not None:
            info.target = Existing(
                entry.entity,
                ResolutionBasis.CURATED,
                known.get(entry.entity, ("", None))[0],
            )
            info.note = ("curated_mapping", _curated(info, entry))

    def _plan_creations(self, pending):
        """Unmatched identifiers without an entry, each with the value as
        the first row wrote it and that row's line: a planned creation when
        creation_problem passes for that value, otherwise unresolved with
        the reason. The value stands in for the label here: the label a
        commit would give is chosen from the mapped rows (record_labels),
        and the adapter checks each row's. Identifiers that would create one
        entity at two types or taxa create none (_contradict)."""
        if not pending:
            return
        registry = self.ctx.registry
        problems = creation_problems(
            [
                (info.entity_type, info.taxon, info.namespace, value, value)
                for info, value, _ in pending
            ],
            release=self.release.pk,
        )
        for (info, value, line), problem in zip(pending, problems):
            stated = _identifier(info)
            if problem:
                reason = f"{stated} matches no entity, and none is created: {problem}"
                info.target = Unmapped(RecordStatus.UNRESOLVED, problem.code, reason)
                info.note = ("unmatched", reason)
                continue
            rules = registry.namespaces[info.namespace]
            key = identity_key(rules, scope_key(rules, info.taxon), info.lookup)
            planned = self.planned.get(key)
            if planned is None:
                create = Create(
                    info.namespace,
                    value,
                    info.lookup,
                    info.entity_type,
                    info.taxon,
                    value,
                    key,
                )
                planned = self.planned[key] = PlannedInfo(
                    PlannedEntity(info.entity_type, info.taxon), create
                )
            planned.infos.append(_key_of(info))
            planned.stated.setdefault((info.entity_type, info.taxon), line)
            if planned.contradicted or len(planned.stated) > 1:
                self._contradict(planned)
                continue
            info.target = planned.create
            if not self.create_entities:
                info.note = (
                    "unmatched",
                    f"{stated} matches no entity; a commit with --create-entities "
                    f"would create it ({key}).",
                )

    def _contradict(self, planned):
        """The file states one value at two types or taxa, so neither row is
        trusted over the other: the entity is not created, and every
        identifier that would create it is refused, in every batch."""
        create = planned.create
        statements = " and as ".join(
            f"a {entity_type} of taxon {taxon} (line {line})"
            for (entity_type, taxon), line in sorted(
                planned.stated.items(), key=lambda item: item[1]
            )
        )
        refusal = (
            "taxon_contradiction",
            f"{create.namespace} {create.lookup} would create {create.identity_key}, "
            f"and the file states it as {statements}: one value names one entity, "
            "so none is created.",
        )
        if any(self.identifiers[key].records for key in planned.infos):
            # Rows of earlier batches planned it: they are planned again.
            self.read_again = True
        planned.contradicted = True
        for key in planned.infos:
            info = self.identifiers[key]
            info.target, info.note, info.refusal = None, None, refusal

    # ── Papers and terms ──

    def resolve_papers(self, stated_papers):
        """Find the stored publications the cited papers name, in one query
        per kind of identifier, for those the run has not met yet."""
        wanted = {"pmid": set(), "doi": set(), "local": set()}
        for stated in stated_papers:
            identity = paper_identity(stated)
            if identity is not None and identity not in self.papers:
                kind, value = identity
                wanted[kind].add(stated["doi"] if kind == "doi" else value)
        if not any(wanted.values()):
            return
        found = find_publications(
            pmids=sorted(wanted["pmid"]),
            dois=sorted(wanted["doi"]),
            local=(self.release.dataset_id, sorted(wanted["local"])),
        )
        manifest = self.ctx.manifest.publications
        for kind, values in wanted.items():
            for value in values:
                identity = (kind, doi_lookup(value) if kind == "doi" else value)
                row = found.get(identity)
                if row is not None:
                    self.papers[identity] = ExistingPaper(row.pk)
                    declared = manifest.get(value) if kind == "local" else None
                    if declared is not None and (
                        (row.title or "").strip() != declared.title
                        or row.year != declared.year
                    ):
                        self.paper_differences[value] = (row.title, row.year)
                elif kind == "local":
                    declared = manifest[value]
                    self.papers[identity] = NewPaper(
                        kind,
                        value,
                        declared.title,
                        declared.year,
                        declared.is_synthetic,
                    )
                else:
                    self.papers[identity] = NewPaper(kind, value)

    def resolve_terms(self, curies):
        """Which of these CURIEs name a stored term, in one query for those
        the run has not met yet."""
        new = sorted({curie for curie in curies if curie not in self.terms})
        if not new:
            return
        stored = dict(Term.objects.filter(pk__in=new).values_list("pk", "label"))
        for curie in new:
            self.terms[curie] = stored.get(curie)

    # ── Labels ──

    def record_labels(self, plan):
        """A mapped row's labels for the entities it would create."""
        line = plan.record.line_number
        for endpoint in plan.endpoints:
            target = endpoint.target
            if not isinstance(target, Create):
                continue
            planned = self.planned[target.identity_key]
            planned.records += 1
            if len(planned.lines) < limits.REPORT_ENTRY_LINES:
                planned.lines.append(line)
            if planned.written is None:
                planned.written = endpoint.stated.value.strip()
            label = endpoint.stated.label.strip()
            if not label:
                continue
            if planned.label is None:
                planned.label, planned.label_line = label, line
            elif label != planned.label:
                planned.other_label_count += 1
                if (
                    label not in planned.other_labels
                    and len(planned.other_labels) < NOTED_CANDIDATES
                ):
                    planned.other_labels[label] = line


# ── Planning a batch ────────────────────────────────────────────────────────


def plan_batch(records, *, run_ctx, check=True) -> list:
    """A RecordPlan for each record of the batch, in order. Read-only: a
    constant number of queries for the batch, besides G3's check_claims
    (_check), whose calls grow with the records it refuses and those that
    hold evidence (_check_together).

    With check=False (a commit's writing reading) G3's check is skipped, and
    so are the labels (record_labels, _labelled): statuses are computed
    without G3's refusals, which the caller does not use, and each planned
    entity keeps the label and written value of the last reading planned
    with the check, so a row G3 refuses never chooses one.

    Raises FileError (too_many_identifiers), at the batch's first line, once
    the run has met more than limits.MAX_IDENTIFIERS distinct identifiers as
    written, refused ones included: the file is refused."""
    run = run_ctx
    records = list(records)
    stored = _stored_records(run.release.pk, records)
    plans = _read(records, stored, run)
    planned = [plan for plan in plans if not plan.unstored]
    for plan in planned:
        # A conflict a later line was found to state, in an earlier run or
        # batch: added where _read adds one found in this batch, right after
        # the adapter's issues, so the record is rejected before G3's check
        # and has the same issues whatever the batches.
        plan.issues.extend(issue for issue in plan.carried if issue not in plan.issues)
    refs, papers, curies = [], [], []
    for plan in planned:
        for claim in plan.claims:
            for side in SIDES:
                endpoint = getattr(claim, side)
                if not _refused_by_adapter(plan, side):
                    refs.append((endpoint.entity, plan.record.line_number))
            papers.extend(ref.stated for ref in claim.publications)
            curies.extend(_curies(claim))
    run.resolve_identifiers(refs)
    _identifier_limit(run, records)
    run.resolve_papers(papers)
    run.resolve_terms(curies)
    for plan in planned:
        _plan_record(plan, run)
    if check:
        _check(planned, run)
    for plan in planned:
        plan.status = _status(plan, run)
        if check and plan.would_map:
            run.record_labels(plan)
            plan.endpoints = [_labelled(endpoint, run) for endpoint in plan.endpoints]
    return plans


def _identifier_limit(run, records):
    """FileError once the run keeps more than limits.MAX_IDENTIFIERS distinct
    identifiers as written (MappingRun._stated, refused ones included)."""
    if len(run._stated) <= limits.MAX_IDENTIFIERS:
        return
    raise FileError(
        [
            Issue(
                "too_many_identifiers",
                f"The file states more than {limits.MAX_IDENTIFIERS:,} distinct "
                "identifiers, which a run does not keep: split it into releases.",
                line=records[0].line_number if records else None,
            )
        ]
    )


def _stored_records(release_id, records) -> dict:
    """{record key: the stored record's pk, line, digest, status and
    issues}, in one query."""
    keys = {
        record.record_key
        for record in records
        if record.record_key is not None and not record.issues
    }
    if not keys:
        return {}
    rows = SourceRecord.objects.filter(
        release_id=release_id, record_key__in=sorted(keys)
    ).values("pk", "record_key", "line_number", "raw_sha256", "status", "issues")
    return {row["record_key"]: row for row in rows}


def _read(records, stored, run) -> list:
    """The plans of the batch's lines, unstored or mapped by the adapter.
    The stored records and the batch's earlier lines are the run's seen
    set: a later line of a record id is unstored (protocol.duplicate_issues).
    A synthetic release's unstored lines are checked too, when their cells
    could be read (Adapter.synthetic_issues)."""
    plans, firsts = [], {}
    for record in records:
        plan = RecordPlan(record)
        plans.append(plan)
        if record.fields is None or record.issues:
            plan.unstored = True
            plan.issues = [
                *record.issues,
                *run.adapter.synthetic_issues(record, run.ctx),
            ]
            continue
        key = record.record_key
        first = firsts.get(key)
        row = stored.get(key)
        if first is None and row is not None:
            if row["line_number"] < record.line_number:
                first = _First(row["line_number"], row["raw_sha256"], None)
            elif (row["line_number"], row["raw_sha256"]) != (
                record.line_number,
                record.raw_sha256,
            ):
                raise RunStopped(
                    f"Record {quoted(key)} is stored from line {row['line_number']} "
                    f"with SHA-256 {row['raw_sha256']}, and this file has it on line "
                    f"{record.line_number} with {record.raw_sha256}: the release's "
                    "records do not come from its data file."
                )
            else:
                plan.stored_id = row["pk"]
                plan.stored_status = row["status"]
                plan.carried = _carried(row, record, run)
        if first is not None:
            later, earlier = duplicate_issues(record, first.line, first.sha256)
            plan.unstored = True
            plan.issues = [later]
            if earlier is not None:
                # Another version: a synthetic release checks it too. An
                # identical line was checked as its first occurrence.
                plan.issues.extend(run.adapter.synthetic_issues(record, run.ctx))
                if first.plan is not None:
                    first.plan.issues.append(earlier)
                else:
                    # Given to the stored first occurrence once per run: in
                    # a second reading its own plan carried it (_carried),
                    # also past limits.RECORD_ISSUES conflicts, where its
                    # count of further issues (more) holds it, which
                    # write_records could not tell.
                    found = run.contradicted.setdefault(key, [])
                    if earlier not in found:
                        found.append(earlier)
                        plan.first_key, plan.first_issue = key, earlier
            continue
        firsts[key] = _First(record.line_number, record.raw_sha256, plan)
        mapped = run.adapter.map_record(record, run.ctx)
        plan.claims = tuple(mapped.claims)
        plan.issues = list(mapped.issues)
    return plans


def _carried(row, record, run) -> tuple:
    """The conflicting_duplicate issues a stored first occurrence holds,
    from a later line an earlier run or batch read, and those this run's
    earlier batches found. The stored file never changes (its digest is
    fixed, and _read stops the run if a stored line differs), so that
    later line states the conflict again: carried, the record stays
    rejected instead of being written mapped until the later line's batch.
    issues_json and _with_issue keep a record's conflicts among the issues
    it stores, so one is lost only past limits.RECORD_ISSUES conflicts on
    one record. Then a later run's first reading writes the record without
    it, the later line's batch gives it back, and the file is read a
    second time (MappingRun.read_again): that run writes the record twice
    and reads the file twice. Its records and report are still those of
    one batch."""
    line = record.line_number
    carried = [
        Issue(
            item["code"],
            item.get("message", ""),
            field=item.get("field", ""),
            line=line,
        )
        for item in row["issues"] or []
        if item.get("code") == "conflicting_duplicate" and item.get("line") == line
    ]
    for issue in run.contradicted.get(record.record_key, ()):
        if issue not in carried:
            carried.append(issue)
    return tuple(carried)


def _refused_by_adapter(plan, side) -> bool:
    """The adapter refused the endpoint's identifier cells already."""
    return any(
        issue.field == f"{side}_{_REFUSED_CELLS[issue.code]}"
        for issue in plan.issues
        if issue.code in _REFUSED_CELLS
    )


def _curies(claim):
    for term in (
        claim.interaction_type,
        claim.eco_term,
        *(method.term for method in claim.methods),
        *(context.term for context in claim.contexts),
    ):
        if isinstance(term, str):
            yield term


def _plan_record(plan, run):
    """The record's endpoints, papers and terms, with the issues and notes
    they give."""
    for claim in plan.claims:
        for side in SIDES:
            plan.endpoints.append(_endpoint(plan, run, side, getattr(claim, side)))
        # One identifier per paper (§11i): two items that name one stored
        # paper, a PMID and a DOI say, are refused, never merged into one
        # assertion. The adapter finds repeats of one identifier itself.
        named = {}
        for ref in claim.publications:
            identity = paper_identity(ref.stated)
            if identity is None:
                continue
            target = run.papers[identity]
            plan.papers.append(PaperPlan(dict(ref.stated), target))
            if not isinstance(target, ExistingPaper):
                continue
            first = named.setdefault(target.pk, identity)
            if first != identity:
                plan.issues.append(
                    Issue(
                        "duplicate_paper",
                        f"publications items {first[0]} {quoted(first[1])} and "
                        f"{identity[0]} {quoted(identity[1])} name one stored "
                        f"paper, publication {target.pk}: cite each paper once.",
                        field="publications",
                        line=plan.record.line_number,
                    )
                )
    declared = run.ctx.manifest.terms
    plan.terms = tuple(
        sorted(
            {
                (curie, declared[curie])
                for claim in plan.claims
                for curie in _curies(claim)
                if run.terms.get(curie) is None and curie in declared
            }
        )
    )


def _endpoint(plan, run, side, endpoint) -> EndpointPlan:
    ref = endpoint.entity
    line = plan.record.line_number
    if _refused_by_adapter(plan, side):
        return EndpointPlan(side, ref, None, None)
    outcome = run._stated[
        _written_key(ref.namespace, ref.value, ref.entity_type, ref.taxon)
    ]
    if isinstance(outcome, _Refused):
        outcome.records += 1
        if len(outcome.lines) < limits.REPORT_ENTRY_LINES:
            outcome.lines.append(line)
        written = _kept(ref.value)
        if written not in outcome.written and len(outcome.written) < (
            limits.REPORT_ENTRY_LINES
        ):
            outcome.written.append(written)
        plan.issues.append(
            Issue(
                outcome.code,
                f"The {side}, {_written(ref)}: {outcome.reason}",
                field=f"{side}_{_COLUMN.get(outcome.code, 'id')}",
                line=line,
            )
        )
        return EndpointPlan(side, ref, None, None)
    info = run.identifiers[outcome]
    info.seen(ref.value, line)
    key = (info.namespace, info.lookup, info.entity_type, info.taxon)
    if run.ctx.is_synthetic:
        entry = run.resolutions.get(*key)
        if entry is not None and entry.taxon != SYNTHETIC_TAXON:
            plan.issues.append(
                Issue(
                    "synthetic_violation",
                    f"The {side}, {_written(ref)}, meets entry {entry.index} of the "
                    f"resolution file, whose target is of taxon {entry.taxon}, not "
                    f"{SYNTHETIC_TAXON}: a synthetic release maps only to synthetic "
                    "constructs.",
                    field=f"{side}_id",
                    line=line,
                )
            )
    if info.refusal is not None:
        code, reason = info.refusal
        plan.issues.append(
            Issue(
                code,
                f"The {side}, {_written(ref)}: {reason}",
                field=f"{side}_{_COLUMN.get(code, 'id')}",
                line=line,
            )
        )
        return EndpointPlan(side, ref, outcome, None)
    target = info.target
    if info.note is not None:
        code, reason = info.note
        plan.issues.append(
            note(code, f"The {side}: {reason}", field=f"{side}_id", line=line)
        )
    label = ref.label.strip()
    if isinstance(target, Existing) and label and label != target.label:
        plan.issues.append(
            note(
                "label_differs",
                f"The {side}'s label {quoted(label)} differs from entity "
                f"{target.entity_id}'s, {quoted(target.label)}, which never changes.",
                field=f"{side}_label",
                line=line,
            )
        )
    return EndpointPlan(side, ref, outcome, target)


def _labelled(endpoint, run) -> EndpointPlan:
    """A planned creation's target with the label its entity has so far."""
    target = endpoint.target
    if not isinstance(target, Create):
        return endpoint
    chosen = run.planned[target.identity_key].chosen
    if chosen == target.label:
        return endpoint
    return replace(endpoint, target=replace(target, label=chosen))


def _status(plan, run) -> str:
    """rejected > ambiguous > unresolved > mapped (§11f)."""
    if rejects(plan.issues):
        return RecordStatus.REJECTED
    targets = [endpoint.target for endpoint in plan.endpoints]
    if any(
        isinstance(target, Unmapped) and target.status == RecordStatus.AMBIGUOUS
        for target in targets
    ):
        return RecordStatus.AMBIGUOUS
    if any(isinstance(target, Unmapped) for target in targets):
        return RecordStatus.UNRESOLVED
    if not run.create_entities and any(isinstance(t, Create) for t in targets):
        return RecordStatus.UNRESOLVED
    return RecordStatus.MAPPED


# ── G3's check ──────────────────────────────────────────────────────────────


def _check(plans, run):
    """check_claims over the batch's records that no rule has rejected: with
    the stored record when every endpoint is a stored entity and the record
    holds evidence (G3 compares them), and by the rules alone otherwise.

    Records of one claim without a key part are checked together
    (_check_together), each claim given a key part of its own (b1, b2, ...)
    so that G3's rule 11, the only rule that reads several claims, tells
    them apart; for a record that holds no evidence that is the check one
    call per record would make."""
    eligible = [plan for plan in plans if plan.claims and not rejects(plan.issues)]
    if not eligible:
        return
    held = _holding_evidence([plan.stored_id for plan in eligible])
    together = []
    for plan in eligible:
        existing = all(isinstance(e.target, Existing) for e in plan.endpoints)
        if existing and plan.stored_id in held:
            _check_one(plan, run, plan.stored_id)
        elif len(plan.claims) == 1 and not plan.claims[0].key_part:
            together.append(plan)
        else:
            _check_one(plan, run, plan.stored_id if existing else None)
    _check_together(together, run)


def _holding_evidence(record_ids) -> set:
    ids = sorted({pk for pk in record_ids if pk is not None})
    if not ids:
        return set()
    return set(
        EvidenceAssertion.objects.filter(source_record_id__in=ids)
        .values_list("source_record_id", flat=True)
        .distinct()
    )


def checked_claims(plan, run) -> list:
    """The record's claims as check_claims takes them: each endpoint a
    stored entity, a planned entity or a placeholder, and each paper its
    stored publication or None (what a commit wrote, once it has: entity_of
    and _paper_pk)."""
    targets = iter(plan.endpoints)
    claims = []
    for claim in plan.claims:
        ends = {}
        for side in SIDES:
            endpoint = next(targets)
            basis = (
                endpoint.target.basis
                if isinstance(endpoint.target, Existing)
                else ResolutionBasis.PRIMARY
            )
            ends[side] = Endpoint(
                run.entity_of(endpoint), getattr(claim, side).as_stated, basis
            )
        papers = tuple(
            PublicationRef(_paper_pk(run, ref.stated), ref.role, ref.stated)
            for ref in claim.publications
        )
        claims.append(replace(claim, publications=papers, **ends))
    return claims


def _paper_pk(run, stated):
    """The publication a stated paper names: the one a commit wrote for it
    (paper_ids), else the stored one, else None."""
    identity = paper_identity(stated)
    if identity is None:
        return None
    written = run.paper_ids.get(identity)
    if written is not None:
        return written
    target = run.papers.get(identity)
    return target.pk if isinstance(target, ExistingPaper) else None


def _check_one(plan, run, source_record) -> bool:
    """check_claims over one record's claims; whether G3 let them through."""
    try:
        result = check_claims(
            checked_claims(plan, run),
            release=run.release.pk,
            source_record=source_record,
        )
    except ClaimError as error:
        plan.issues.append(_claim_issue(plan, error))
        return False
    plan.relationships = tuple(
        (claim.canonical, claim.relationship_exists) for claim in result.claims
    )
    plan.assertions_exist = result.assertions_exist
    plan.notes = tuple(_CLAIM_NOTE.sub("", text, count=1) for text in result.notes)
    return True


def _check_together(plans, run):
    """check_claims over records of one claim each, in windows of the
    batch. G3 checks claims in order and stops at the first it refuses,
    naming it: the claims before it passed, and are checked again as one
    window known to pass, for what G3 says of them; the refused record is
    checked alone, so its issue is in G3's words for it alone; and the
    check goes on from the next record. A window doubles after a call that
    passes, and after a refusal is twice the claims that passed before it,
    but at least half the window: so the claims checked stay within a few
    times the batch's, never one check of the rest of the batch per
    refusal, and the calls grow with the refusals (three each at most), and
    with the records that pass only by the doublings that follow a
    refusal. A refusal that names no claim checks each half of its window
    on its own."""
    pending, window = list(plans), len(plans)
    while pending:
        part = pending[: max(window, 1)]
        if len(part) == 1:
            passed = _check_one(part[0], run, None)
            pending = pending[1:]
            window = 2 if passed else 1
            continue
        try:
            result = check_claims(_numbered(part, run), release=run.release.pk)
        except ClaimError as error:
            named = _REFUSED_CLAIM.match(str(error))
            if named is None or not 1 <= int(named[1]) <= len(part):
                middle = len(part) // 2
                _check_together(part[:middle], run)
                _check_together(part[middle:], run)
                pending = pending[len(part) :]
                continue
            index = int(named[1]) - 1
            if index:
                _check_together(part[:index], run)
            _check_one(part[index], run, None)
            pending = pending[index + 1 :]
            window = max(2 * index, len(part) // 2, 1)
            continue
        notes = defaultdict(list)
        for text in result.notes:
            number, _, rest = text.partition(": ")
            notes[int(number.split()[-1])].append(rest)
        for n, (plan, claim) in enumerate(zip(part, result.claims), 1):
            plan.relationships = ((claim.canonical, claim.relationship_exists),)
            plan.assertions_exist = None
            plan.notes = tuple(notes[n])
        pending = pending[len(part) :]
        window = 2 * len(part)


def _numbered(plans, run) -> list:
    """The records' claims, one each, with key parts b1, b2, ..."""
    return [
        replace(checked_claims(plan, run)[0], key_part=f"b{n}")
        for n, plan in enumerate(plans, 1)
    ]


def _claim_issue(plan, error) -> Issue:
    """G3's refusal, in G3's words: claim_rule_<n>, or claim_conflict for a
    record whose evidence the claims do not reproduce."""
    message = str(error)
    if isinstance(error, ClaimConflict):
        code = "claim_conflict"
    else:
        rule = _RULE.match(message)
        if rule is None:
            raise error
        code = f"claim_rule_{rule[1]}"
    return Issue(code, message, line=plan.record.line_number)


# ── Writing a batch's records ───────────────────────────────────────────────


@dataclass
class Written:
    """What write_records did for one batch."""

    created: int = 0
    updated: int = 0
    unchanged: int = 0
    # Records that hold evidence and stay mapped although the plan says
    # otherwise: (record key, line, the planned status, issues as stored).
    would_change: list = field(default_factory=list)
    # First occurrences a later line contradicts, rejected now: (record
    # key, line, their status before, issues as stored).
    rejected_firsts: list = field(default_factory=list)


def write_records(plans, *, run) -> Written:
    """Write the batch's source records, in one short transaction: the only
    write of a dry run besides its import run.

    The release is locked and checked again: a draft, with no open commit
    run, read by the adapter version the run reads with, an effective
    internal approval unless it is synthetic, and the registry version the
    code carries. A change raises RunStopped, and what earlier batches wrote
    stays. The batch's stored records are read by key in one query; new ones
    are bulk-created (the run is their first writer; raw is the verbatim
    column map when the release stores raw rows, else NULL); a stored
    record's status and issues are updated when they change, except that a
    record that evidence was recorded from stays mapped and only its issues
    follow the plan. A first occurrence that a later line contradicts is
    rejected, unless it holds that issue already: its own plan carried it."""
    plans = list(plans)
    written = Written()
    with transaction.atomic():
        release = locked_release(run.release_id, RunStopped)
        _check_still(release, run)
        storing = [plan for plan in plans if not plan.unstored]
        firsts = defaultdict(list)
        for plan in plans:
            if plan.first_issue is not None:
                firsts[plan.first_key].append(plan.first_issue)
        keys = {plan.record.record_key for plan in storing} | set(firsts)
        rows = {}
        if keys:
            # Their column maps are never needed: content never changes.
            rows = {
                row.record_key: row
                for row in SourceRecord.objects.filter(
                    release_id=release.pk, record_key__in=sorted(keys)
                ).defer("raw")
            }
        new, changes = [], []
        for plan in storing:
            record = plan.record
            issues = issues_json(plan.issues)
            row = rows.get(record.record_key)
            if row is None:
                new.append(
                    SourceRecord(
                        release_id=release.pk,
                        file_id=run.file_id,
                        import_run_id=run.pk,
                        record_key=record.record_key,
                        record_key_basis=record.record_key_basis,
                        line_number=record.line_number,
                        raw=dict(record.fields) if release.store_raw_rows else None,
                        raw_sha256=record.raw_sha256,
                        status=plan.status,
                        issues=issues,
                    )
                )
                continue
            changes.append((row, plan.status, issues, record.line_number))
        for key, added in sorted(firsts.items()):
            row = rows.get(key)
            if row is None:
                continue
            issues = row.issues
            for issue in added:
                issues = _with_issue(issues, issue)
            if issues == row.issues:
                # Its own plan carried the conflict (_carried) and wrote it:
                # the record is rejected, or stays mapped because it holds
                # evidence, and its plan listed it under would_change.
                # Nothing changes.
                written.unchanged += 1
                continue
            written.rejected_firsts.append((key, row.line_number, row.status, issues))
            changes.append((row, RecordStatus.REJECTED, issues, row.line_number))
        if new:
            SourceRecord.objects.bulk_create(new)
            written.created = len(new)
        _update(changes, written)
    return written


def _check_still(release, run):
    """The run's preconditions, read again under the release's lock."""
    if not typed_graph_enabled():
        raise RunStopped(
            "The typed graph is off on this portal now (TYPED_GRAPH_ENABLED). The "
            "records of earlier batches stay."
        )
    if release.status != ReleaseStatus.DRAFT:
        raise RunStopped(
            f"Release {release.pk} is {release.status} now: a dry run runs on a "
            "draft only. The records of earlier batches stay."
        )
    if ImportRun.objects.filter(
        release_id=release.pk,
        mode=ImportMode.COMMIT,
        state__in=[ImportState.RUNNING, ImportState.FAILED],
    ).exists():
        raise RunStopped(
            f"Release {release.pk} has an open commit run now: a dry run never runs "
            "beside one. The records of earlier batches stay."
        )
    check_adapter_unchanged(release, run)
    if not release.is_synthetic and not is_effective(release, INTERNAL):
        raise RunStopped(
            f"Release {release.pk} has no effective internal approval now. The "
            "records of earlier batches stay."
        )
    database = registry_state().database
    if database != REGISTRY_VERSION:
        raise RunStopped(
            f"The database's registry is at version {database or 'none'} now, and "
            f"this code's at {REGISTRY_VERSION}. The records of earlier batches stay."
        )


def check_adapter_unchanged(release, run):
    """RunStopped unless the release (as locked by the caller) still reads
    with the adapter, version and mapping version the run reads with: a
    registration under the code's next adapter version deletes its records
    and reads the file anew (registration._update), which a running run's
    batches and report would not follow."""
    if (release.adapter, release.adapter_version, release.mapping_version) != (
        run.adapter,
        run.adapter_version,
        run.mapping_version,
    ):
        raise RunStopped(
            f"Release {release.pk} reads with {release.adapter} "
            f"{release.adapter_version} (mapping {release.mapping_version}) now, and "
            f"this run with {run.adapter} {run.adapter_version} (mapping "
            f"{run.mapping_version}): it was registered again. The records of "
            "earlier batches stay, unless that registration deleted them."
        )


def _update(changes, written):
    """Update the stored records whose status or issues change: those that
    hold evidence keep their status (mapped)."""
    changed = [
        (row, status, issues, line)
        for row, status, issues, line in changes
        if row.status != status or row.issues != issues
    ]
    written.unchanged += len(changes) - len(changed)
    if not changed:
        return
    held = _holding_evidence([row.pk for row, _, _, _ in changed])
    now = timezone.now()
    statuses, issues_only = [], []
    for row, status, issues, line in changed:
        if row.pk in held:
            if status != row.status:
                written.would_change.append((row.record_key, line, status, issues))
            if issues == row.issues:
                written.unchanged += 1
                continue
            row.issues, row.updated_at = issues, now
            issues_only.append(row)
            continue
        row.status, row.issues, row.updated_at = status, issues, now
        statuses.append(row)
    if statuses:
        SourceRecord.objects.bulk_update(statuses, ["status", "issues", "updated_at"])
    if issues_only:
        SourceRecord.objects.bulk_update(issues_only, ["issues", "updated_at"])
    written.updated += len(statuses) + len(issues_only)


def _with_issue(stored, issue) -> list:
    """A record's stored issues with one more, kept as issues_json keeps
    them: at most limits.RECORD_ISSUES, then a count of the rest, with its
    conflicting_duplicate issues always among those kept. A conflict added
    to a full list takes the place of the last kept issue of another code,
    which joins the count, so the layout is the one a single batch stores."""
    added = issue.as_json()
    issues = [dict(item) for item in stored or []]
    if added in issues:
        return issues
    more = issues.pop() if issues and issues[-1].get("code") == "more" else None
    if len(issues) < limits.RECORD_ISSUES:
        issues.append(added)
        if more is not None:
            issues.append(more)
        return issues
    more = more or {"code": "more", "count": 0}
    others = [
        index
        for index, item in enumerate(issues)
        if item.get("code") != "conflicting_duplicate"
    ]
    if added.get("code") == "conflicting_duplicate" and others:
        del issues[others[-1]]
        issues.append(added)
    more["count"] += 1
    issues.append(more)
    return issues


# ── Helpers ─────────────────────────────────────────────────────────────────


def paper_identity(stated):
    """(kind, value) of a paper a record states, as stored rows are found:
    ("pmid", digits), ("doi", its lookup form), ("local", key); None for
    anything else."""
    if not isinstance(stated, dict) or len(stated) != 1:
        return None
    [(kind, value)] = stated.items()
    if kind == "doi":
        return kind, doi_lookup(value)
    if kind in ("pmid", "local"):
        return kind, value
    return None


def candidate_json(candidate, known=None) -> dict:
    """A resolver candidate as the report and a record's note show it: every
    match, the entity's primary identifier and its status."""
    entity = candidate.entity
    primary = (known or {}).get(entity.pk, (None, None))[1]
    return {
        "entity": entity.pk,
        "entity_type": entity.entity_type_id,
        "taxon": entity.taxon_id,
        "status": entity.status,
        "label": entity.label,
        "primary": primary,
        "exact_primary": candidate.exact_primary,
        "matches": [
            {
                "kind": match.kind,
                "namespace": match.namespace,
                "value": match.value,
                "case_exact": match.case_exact,
                "version_mismatch": match.version_mismatch,
            }
            for match in candidate.matches
        ],
    }


def _listed(candidates, resolution) -> str:
    """The candidates as a record's note lists them: the first
    NOTED_CANDIDATES described, every other by its entity id, so the record
    names each candidate the resolver gave."""
    shown = "; ".join(
        f"entity {c['entity']} ({c['primary'] or 'no primary identifier'}, "
        f"{c['status']}), matched as "
        + ", ".join(
            f"{m['kind']}"
            + (f" {m['namespace']}" if m["namespace"] else "")
            + f" {quoted(m['value'])}"
            + ("" if m["case_exact"] else " ignoring case")
            + (" with another version" if m["version_mismatch"] else "")
            for m in c["matches"]
        )
        for c in candidates[:NOTED_CANDIDATES]
    )
    rest = candidates[NOTED_CANDIDATES:]
    if rest:
        shown += f"; and {len(rest)} more: " + ", ".join(
            f"entity {c['entity']}" for c in rest
        )
    if len(resolution.candidates) >= CANDIDATE_LIMIT:
        shown += f" (the resolver lists at most {CANDIDATE_LIMIT})"
    return shown


def _curated(info, entry) -> str:
    return (
        f"{_identifier(info)} is {info.status}, and entry {entry.index} of the "
        f"resolution file maps it to entity {entry.entity} through "
        f"{entry.target_namespace} {quoted(entry.target_value)} (basis curated): "
        f"{quoted(entry.rationale)}, reviewed by {quoted(entry.reviewer)}."
    )


def _identifier(info) -> str:
    version = f".{info.version}" if info.version else ""
    return (
        f"{info.namespace} {info.lookup}{version} ({info.entity_type}, taxon "
        f"{info.taxon})"
    )


def _written(ref) -> str:
    return (
        f"{ref.namespace} {quoted(ref.value)} ({ref.entity_type}, taxon "
        f"{ref.taxon})"
    )


def _written_key(namespace, value, entity_type, taxon) -> tuple:
    """How the run keys an identifier as a row writes it: the cells
    themselves while each is at most VALUE_LENGTH characters, as every
    identifier's are, or else their SHA-256, so that a refused cell of
    thousands of characters is not kept for the whole run."""
    written = (namespace, value, entity_type, taxon)
    if all(len(part) <= VALUE_LENGTH for part in (namespace, value, entity_type)):
        return written
    digest = hashlib.sha256(repr(written).encode("utf-8", "surrogatepass"))
    return ("sha256", digest.hexdigest())


def _kept(text) -> str:
    """A cell as the run keeps it for the report: whole up to
    limits.QUOTED_VALUE_CHARS characters, else as quoted() quotes it."""
    return text if len(text) <= limits.QUOTED_VALUE_CHARS else quoted(text)


def _term(key) -> str:
    """The term resolve_many reads for a key: the lookup, with its version."""
    _, lookup, version, _, _ = key
    return f"{lookup}.{version}" if version else lookup


def _key_of(info) -> tuple:
    return (info.namespace, info.lookup, info.version, info.entity_type, info.taxon)


def _entry_key(key) -> tuple:
    namespace, lookup, _, entity_type, taxon = key
    return namespace, lookup, entity_type, taxon


def _primary_holder(candidate, namespace) -> bool:
    """The candidate holds the value as its primary identifier in this
    namespace (a version may differ: the identity key has none)."""
    return any(
        match.kind == IdentifierRole.PRIMARY
        and match.namespace == namespace
        and match.case_exact
        for match in candidate.matches
    )


def _sortable(group) -> tuple:
    namespace, entity_type, taxon = group
    return namespace, entity_type, taxon is None, taxon or 0

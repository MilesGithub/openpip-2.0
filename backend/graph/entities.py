"""The write path for entities and their identifiers.

A check constraint cannot span tables, so the rules that do are enforced
here, in one tested place:

- the entity type's taxon policy (a gene needs a taxon);
- which entity types a namespace may identify, and, where one namespace
  identifies both protein entries and isoforms, which of the two a value is;
- the identity rule: an entity is created only from the one namespace that
  defines identity for its type and taxon, and never from a value that an
  existing entity of that type and taxon already holds, in any role;
- provenance: an entity created for a release records it as the release that
  introduced it, the first writer, and only a draft or staged release with an
  effective `internal` approval introduces any (a synthetic one needs none,
  and introduces synthetic constructs only).

The entity type and namespace rows these rules read are read again inside
the write's transaction, under a shared lock (graph.models.share_lock), so a
registry sync cannot change their rules before the entity is written; a
release is locked before them.

The same rules are offered read-only, for an import's dry run (G4) and for
its commit: identifier_problem says why a value is not an identifier of an
entity of a type and taxon in a namespace, and creation_problem why
create_entity would not create an entity from it. Each returns None or a
Problem, the sentence create_entity raises, with the issue code an import
reports it under and create_entity's exception type, and each has a batch
form with one query per kind of row. create_entity applies them through the
same functions.
"""

from collections import defaultdict
from dataclasses import dataclass

from django.db import router, transaction

from .approvals import INTERNAL, is_effective
from .identity import (
    InvalidIdentifier,
    identity_key,
    normalize,
    scope_key,
    uniprot_entity_type,
)
from .models import (
    SYNTHETIC_TAXON,
    DatasetRelease,
    Entity,
    EntityIdentifier,
    EntityStatus,
    EntityType,
    IdentifierNamespace,
    IdentifierRole,
    ReleaseKind,
    ReleaseStatus,
    Scope,
    Taxon,
    TaxonPolicy,
    share_lock,
)
from .preconditions import locked_release, require_switch, storable, typed_graph_enabled

# Namespaces that identify both tell them apart by the value's form.
ISOFORM_PAIR = {"protein", "protein_isoform"}
NON_PRIMARY_ROLES = (
    IdentifierRole.XREF,
    IdentifierRole.SECONDARY,
    IdentifierRole.ALIAS,
    IdentifierRole.SYMBOL,
)
# A release introduces entities while it is imported, and until it is
# published. A synthetic one introduces synthetic constructs only
# (SYNTHETIC_TAXON, NCBI Taxonomy's "synthetic construct").
INTRODUCING = (ReleaseStatus.DRAFT, ReleaseStatus.STAGED)


class EntityError(Exception):
    """Nothing was written: the entity or identifier breaks an identity rule."""


class TaxonPolicyError(EntityError):
    """The taxon is missing where the entity type needs one, present where it
    forbids one, unknown, or not the taxon the namespace belongs to."""


class NamespaceTypeError(EntityError):
    """The namespace does not identify entities of this type, or the value's
    form names the other type (an isoform accession for a protein entry)."""


class IdentityNamespaceError(EntityError):
    """Entities of this type and taxon are not created from this namespace:
    another one defines their identity, or none does."""


class ReleaseProvenanceError(EntityError):
    """The release cannot introduce entities: the typed graph is off, the
    release is past staged, it has no effective internal approval, or it is
    synthetic and the entity is not a synthetic construct."""


class IdentityConflict(EntityError):
    """The value already names a different entity, or is held by one."""

    def __init__(self, message: str, entity_ids):
        super().__init__(message)
        self.entity_ids = tuple(sorted(entity_ids))

    @property
    def entity_id(self) -> int | None:
        """The existing entity, when exactly one holds the value."""
        return self.entity_ids[0] if len(self.entity_ids) == 1 else None


class Problem(str):
    """Why a value is refused as an identifier, or an entity is not created:
    the sentence create_entity raises, with the issue code an import reports
    it under (graph.ingest.protocol.ISSUE_CODES) and the exception type
    create_entity raises for it."""

    def __new__(cls, message, *, code, error, entity_ids=()):
        problem = super().__new__(cls, message)
        problem.code = code
        problem.error = error
        problem.entity_ids = tuple(sorted(entity_ids))
        return problem

    def __getnewargs_ex__(self):
        # So copy and pickle rebuild it with its code and type.
        return (str(self),), {
            "code": self.code,
            "error": self.error,
            "entity_ids": self.entity_ids,
        }

    def exception(self) -> Exception:
        """The exception create_entity raises for this problem."""
        if issubclass(self.error, IdentityConflict):
            return self.error(str(self), self.entity_ids)
        return self.error(str(self))


def identity_namespace(entity_type, taxon) -> IdentifierNamespace | None:
    """The one namespace that defines identity for this type and taxon.

    A namespace fixed to the taxon comes first, otherwise the single global
    one. Cross-reference namespaces never stand in, so a type and taxon that
    neither covers has no entities (no fly genes until a fly namespace is
    chosen). Two candidates at one level mean a broken registry, reported
    rather than settled by picking one.
    """
    defining = IdentifierNamespace.objects.filter(
        defines_identity=True, allowed_entity_types=entity_type
    ).order_by("slug")
    taxid = taxon.taxid if isinstance(taxon, Taxon) else taxon
    return _identity_among(list(defining), entity_type, taxid)


def _identity_among(defining, entity_type, taxid) -> IdentifierNamespace | None:
    """identity_namespace among these namespaces, which define identity for
    the type, in slug order."""
    levels = [
        [
            ns
            for ns in defining
            if ns.scope == Scope.GLOBAL and ns.fixed_taxon_id is None
        ]
    ]
    if taxid is not None:
        levels.insert(
            0,
            [
                ns
                for ns in defining
                if ns.scope == Scope.TAXON and ns.fixed_taxon_id == taxid
            ],
        )
    for found in levels:
        if len(found) > 1:
            slugs = ", ".join(namespace.slug for namespace in found)
            raise IdentityNamespaceError(
                f"Several namespaces define identity for {_describe(entity_type, taxid)}"
                f" ({slugs}); the registry allows one."
            )
        if found:
            return found[0]
    return None


# ── Read-only: the rules, before anything is written ────────────────────────


def identifier_problem(entity_type, taxon, namespace, value) -> str | None:
    """Why `value` is not an identifier of an entity of this type and taxon
    in this namespace, or None. Read-only.

    In order: the type, namespace and taxon are in the registry; the type's
    taxon policy; the namespace identifies the type; the namespace's fixed
    taxon (scope_key: hgnc identifiers belong to 9606); the value's
    normalization and pattern; and, where one namespace identifies protein
    entries and isoforms, the value's form (an isoform accession is never
    read as its entry). entity_type and namespace are rows or slugs, read as
    stored; taxon is a row, a taxid or None.
    """
    return identifier_problems([(entity_type, taxon, namespace, value)])[0]


def identifier_problems(identifiers) -> list:
    """identifier_problem for each (entity_type, taxon, namespace, value), in
    order, from four queries whatever their number."""
    identifiers = list(identifiers)
    registry = _Registry.read(
        types={_key(entity_type, EntityType) for entity_type, *_ in identifiers},
        namespaces={_key(item[2], IdentifierNamespace) for item in identifiers},
        taxa={_taxid(item[1]) for item in identifiers},
    )
    return [registry.identifier_problem(*identifier) for identifier in identifiers]


def creation_problem(entity_type, taxon, namespace, value, *, label, release):
    """Why create_entity(entity_type, label, taxon, namespace, value,
    release=release) would not create an entity, or None. Read-only.

    The problem is the one create_entity raises, in its order: while the
    switch is off a release introduces nothing, and an unknown release
    nothing; identifier_problem's rules, except that a value whose namespace
    is fixed to another taxon is refused as create_entity refuses it, because
    that namespace does not define identity for the stated taxon; the
    namespace defines identity for the type and taxon; the label is
    non-blank and fits Entity.label; the release may introduce the entity
    (draft or staged; a synthetic one only taxon 32630, any other with an
    effective internal approval); and no entity has the identity key, of any
    type or taxon, and no entity of the type and taxon holds the value in any
    role. Where an entity of the same type and taxon has the key,
    create_entity returns it instead of creating one, and the problem says
    so (code entity_exists).
    """
    return creation_problems(
        [(entity_type, taxon, namespace, value, label)], release=release
    )[0]


def creation_problems(identifiers, *, release) -> list:
    """creation_problem for each (entity_type, taxon, namespace, value,
    label), in order, with one query per kind of row: the release, entity
    types, namespaces and the ones that define identity, their entity types,
    taxa, entities by identity key and the identifiers' holders."""
    identifiers = list(identifiers)
    if release is not None:
        if not typed_graph_enabled():
            problem = Problem(
                "The typed graph is off on this portal (TYPED_GRAPH_ENABLED), so no "
                "release introduces entities. Nothing was written.",
                code="release_cannot_introduce",
                error=ReleaseProvenanceError,
            )
            return [problem] * len(identifiers)
        release_row = _release_row(release)
        if release_row is None:
            problem = Problem(
                f"Unknown release {release!r}. Nothing was written.",
                code="release_cannot_introduce",
                error=ReleaseProvenanceError,
            )
            return [problem] * len(identifiers)
        release = release_row
    registry = _Registry.read(
        types={_key(item[0], EntityType) for item in identifiers},
        namespaces={_key(item[2], IdentifierNamespace) for item in identifiers},
        taxa={_taxid(item[1]) for item in identifiers},
        defining=True,
    )
    # Read once for the whole batch.
    internal = None
    if release is not None and not release.is_synthetic and identifiers:
        internal = is_effective(release, INTERNAL)
    results, planned = [], {}
    for n, (entity_type, taxon, namespace, value, label) in enumerate(identifiers):
        known = registry.known(entity_type, taxon, namespace)
        if isinstance(known, Problem):
            results.append(known)
            continue
        type_row, taxid, namespace_row = known
        plan = _creation(
            type_row,
            taxid,
            namespace_row,
            registry.allowed[namespace_row.slug],
            value,
            label,
            lambda: registry.identity_namespace(type_row.slug, taxid),
        )
        if isinstance(plan, Problem):
            results.append(plan)
            continue
        if release is not None:
            problem = _introducing_problem(release, taxid, internal)
            if problem:
                results.append(problem)
                continue
        results.append(None)
        planned[n] = (type_row, taxid, namespace_row, plan)
    for n, problem in _existing_problems(planned).items():
        results[n] = problem
    return results


class _Registry:
    """The registry rows the rules read, held in memory for one call."""

    def __init__(self, types, namespaces, allowed, taxa):
        self.types = types
        self.namespaces = namespaces
        self.allowed = allowed
        self.taxa = taxa

    @classmethod
    def read(cls, *, types, namespaces, taxa, defining=False):
        """The rows these slugs and taxids name, and with `defining` every
        namespace that defines identity."""
        type_rows = {
            row.slug: row for row in EntityType.objects.filter(slug__in=_strings(types))
        }
        wanted = IdentifierNamespace.objects.filter(slug__in=_strings(namespaces))
        if defining:
            wanted = wanted | IdentifierNamespace.objects.filter(defines_identity=True)
        namespace_rows = {row.slug: row for row in wanted.order_by("slug")}
        allowed = defaultdict(set)
        through = IdentifierNamespace.allowed_entity_types.through
        for slug, type_slug in through.objects.filter(
            identifiernamespace_id__in=list(namespace_rows)
        ).values_list("identifiernamespace_id", "entitytype_id"):
            allowed[slug].add(type_slug)
        taxids = storable(
            t for t in taxa if isinstance(t, int) and not isinstance(t, bool)
        )
        registered = set(
            Taxon.objects.filter(taxid__in=taxids).values_list("taxid", flat=True)
        )
        return cls(type_rows, namespace_rows, allowed, registered)

    def known(self, entity_type, taxon, namespace):
        """(type row, taxid, namespace row), or the Problem create_entity
        raises for an unknown type, namespace or taxon."""
        type_row = self.types.get(_key(entity_type, EntityType))
        if type_row is None:
            return Problem(
                f"Unknown entity type {entity_type!r}.",
                code="unknown_entity_type",
                error=ValueError,
            )
        namespace_row = self.namespaces.get(_key(namespace, IdentifierNamespace))
        if namespace_row is None:
            return Problem(
                f"Unknown namespace {namespace!r}.",
                code="unknown_namespace",
                error=ValueError,
            )
        taxid = _taxid(taxon)
        # A row given is used as it is, as create_entity uses it. A taxid is
        # an int, never a bool, a string or a float equal to one (9606.0 is
        # in {9606}), as create_entity reads it (_taxon).
        if (
            taxon is not None
            and not isinstance(taxon, Taxon)
            and (
                not isinstance(taxid, int)
                or isinstance(taxid, bool)
                or taxid not in self.taxa
            )
        ):
            return Problem(
                f"Taxon {taxon!r} is not in the registry.",
                code="unknown_taxon",
                error=TaxonPolicyError,
            )
        return type_row, taxid, namespace_row

    def identifier_problem(self, entity_type, taxon, namespace, value):
        known = self.known(entity_type, taxon, namespace)
        if isinstance(known, Problem):
            return known
        type_row, taxid, namespace_row = known
        allowed = self.allowed[namespace_row.slug]
        problem = (
            _taxon_policy_problem(type_row, taxid)
            or _type_problem(type_row, namespace_row, allowed)
            or _fixed_taxon_problem(namespace_row, taxid)
        )
        if problem:
            return problem
        normalized = _normalized(namespace_row, value)
        if isinstance(normalized, Problem):
            return normalized
        return _form_problem(namespace_row, allowed, normalized[0], type_row.slug)

    def identity_namespace(self, type_slug, taxid):
        defining = [
            row
            for row in self.namespaces.values()
            if row.defines_identity and type_slug in self.allowed[row.slug]
        ]
        return _identity_among(defining, type_slug, taxid)


@dataclass(frozen=True)
class _Planned:
    """What create_entity writes for a value that passes the rules."""

    lookup: str
    version: str
    scope: str
    key: str
    label: str


def _creation(entity_type, taxid, namespace, allowed, value, label, defining):
    """The rules create_entity applies before it writes, in its order: a
    _Planned, or the first Problem. `defining` returns the namespace that
    defines identity for the type and taxon; it is asked only once the value
    is an identifier, as create_entity asks."""
    problem = _taxon_policy_problem(entity_type, taxid) or _type_problem(
        entity_type, namespace, allowed
    )
    if problem:
        return problem
    normalized = _normalized(namespace, value)
    if isinstance(normalized, Problem):
        return normalized
    lookup, version = normalized
    problem = _form_problem(namespace, allowed, lookup, entity_type.slug)
    if problem:
        return problem
    try:
        identity = defining()
    except IdentityNamespaceError as exc:
        return Problem(
            str(exc), code="not_identity_namespace", error=IdentityNamespaceError
        )
    if identity is None:
        return Problem(
            f"No namespace defines identity for {_describe(entity_type, taxid)},"
            " so none can be created.",
            code="not_identity_namespace",
            error=IdentityNamespaceError,
        )
    if identity.slug != namespace.slug:
        return Problem(
            f"{_describe(entity_type, taxid)} are created only from "
            f"{identity.slug} identifiers, not from {namespace.slug}.",
            code="not_identity_namespace",
            error=IdentityNamespaceError,
        )
    label = _label(label)
    if isinstance(label, Problem):
        return label
    # The namespace that defines identity for the taxon belongs to it, so
    # this holds by now; it is checked rather than assumed.
    problem = _fixed_taxon_problem(namespace, taxid)
    if problem:
        return problem
    scope = scope_key(namespace, taxid)
    return _Planned(
        lookup, version, scope, identity_key(namespace, scope, lookup), label
    )


def _taxon_policy_problem(entity_type, taxid):
    if entity_type.taxon_policy == TaxonPolicy.REQUIRED and taxid is None:
        return Problem(
            f"A {entity_type.slug} needs a taxon.",
            code="bad_taxon",
            error=TaxonPolicyError,
        )
    if entity_type.taxon_policy == TaxonPolicy.FORBIDDEN and taxid is not None:
        return Problem(
            f"A {entity_type.slug} has no taxon.",
            code="bad_taxon",
            error=TaxonPolicyError,
        )
    return None


def _type_problem(entity_type, namespace, allowed):
    if entity_type.slug not in allowed:
        return Problem(
            f"{namespace.slug} does not identify {entity_type.slug} entities.",
            code="namespace_not_for_type",
            error=NamespaceTypeError,
        )
    return None


def _fixed_taxon_problem(namespace, taxid):
    """The namespace's scope against the taxon (scope_key): a taxon-scoped
    namespace needs one, and one fixed to a taxon belongs to it alone."""
    try:
        scope_key(namespace, taxid)
    except ValueError as exc:
        return Problem(
            str(exc),
            code="bad_taxon" if taxid is None else "taxon_contradiction",
            error=TaxonPolicyError,
        )
    return None


def _normalized(namespace, value):
    """(lookup, version), or the Problem normalize raises."""
    if not isinstance(value, str):
        return Problem(
            f"{value!r} is not a {namespace.slug} identifier.",
            code="bad_identifier",
            error=InvalidIdentifier,
        )
    try:
        return normalize(namespace, value)
    except InvalidIdentifier as exc:
        return Problem(str(exc), code="bad_identifier", error=InvalidIdentifier)


def _form_problem(namespace, allowed, lookup, type_slug):
    if ISOFORM_PAIR <= allowed:
        form = uniprot_entity_type(lookup)
        if form != type_slug:
            return Problem(
                f"{namespace.slug} {lookup} is a {form} identifier, not a {type_slug}.",
                code="isoform_form",
                error=NamespaceTypeError,
            )
    return None


def _label(label):
    """The label stripped, or the Problem create_entity raises."""
    if not isinstance(label, str):
        return Problem(
            f"An entity's label is text, not {label!r}.",
            code="label_blank",
            error=ValueError,
        )
    label = label.strip()
    if not label:
        return Problem("An entity needs a label.", code="label_blank", error=ValueError)
    # SQLite does not enforce the column size, so it is checked here.
    label_length = Entity._meta.get_field("label").max_length
    if len(label) > label_length:
        return Problem(
            f"An entity label is at most {label_length} characters.",
            code="label_too_long",
            error=ValueError,
        )
    return label


def _introducing_problem(release, taxid, internal=None):
    """Why the release, as read (and locked by create_entity), may not
    introduce an entity of this taxon, or None. internal: whether the
    release has an effective internal approval, when the caller has read it
    already for a batch."""
    if release.status not in INTRODUCING:
        return Problem(
            f"Release {release.pk} is {release.status}: only a draft or staged "
            "release introduces entities. Nothing was written.",
            code="release_cannot_introduce",
            error=ReleaseProvenanceError,
        )
    if release.is_synthetic:
        if taxid != SYNTHETIC_TAXON:
            return Problem(
                f"Release {release.pk} is synthetic, so it introduces only "
                f"synthetic constructs (taxon {SYNTHETIC_TAXON}), not entities of "
                f"taxon {taxid}. Nothing was written.",
                code="release_cannot_introduce",
                error=ReleaseProvenanceError,
            )
    elif not (is_effective(release, INTERNAL) if internal is None else internal):
        return Problem(
            f"Release {release.pk} has no effective internal approval, so it "
            "introduces no entities. Nothing was written.",
            code="release_cannot_introduce",
            error=ReleaseProvenanceError,
        )
    return None


def _existing_problems(planned) -> dict:
    """{n: Problem} for the planned creations an existing entity blocks: one
    with the identity key, of any type or taxon, or a holder of the value of
    the type and taxon, in any role. Two queries."""
    if not planned:
        return {}
    keys = {plan.key for _, _, _, plan in planned.values()}
    by_key = {
        row["identity_key"]: row
        for row in Entity.objects.filter(identity_key__in=keys).values(
            "pk", "identity_key", "entity_type_id", "taxon_id"
        )
    }
    holders = defaultdict(set)
    rows = EntityIdentifier.objects.filter(
        namespace_id__in={namespace.slug for _, _, namespace, _ in planned.values()},
        lookup__in={plan.lookup for _, _, _, plan in planned.values()},
    ).values_list(
        "namespace_id",
        "lookup",
        "scope_key",
        "entity__entity_type_id",
        "entity__taxon_id",
        "entity_id",
    )
    for namespace, lookup, scope, type_slug, taxid, entity_id in rows:
        holders[namespace, lookup, scope, type_slug, taxid].add(entity_id)
    problems = {}
    for n, (entity_type, taxid, namespace, plan) in planned.items():
        existing = by_key.get(plan.key)
        if existing is not None:
            same = (existing["entity_type_id"], existing["taxon_id"]) == (
                entity_type.slug,
                taxid,
            )
            if same:
                problems[n] = Problem(
                    f"{plan.key} already identifies entity {existing['pk']}, which "
                    "is found, not created.",
                    code="entity_exists",
                    error=IdentityConflict,
                    entity_ids=[existing["pk"]],
                )
            else:
                problems[n] = Problem(
                    _identifies_another(plan.key, existing),
                    code="identity_conflict",
                    error=IdentityConflict,
                    entity_ids=[existing["pk"]],
                )
            continue
        held = holders.get(
            (namespace.slug, plan.lookup, plan.scope, entity_type.slug, taxid)
        )
        if held:
            problems[n] = Problem(
                _held_by(namespace, plan.lookup, held),
                code="identity_conflict",
                error=IdentityConflict,
                entity_ids=held,
            )
    return problems


def _release_row(release):
    pk = release.pk if isinstance(release, DatasetRelease) else release
    if isinstance(pk, bool) or not isinstance(pk, int) or not storable([pk]):
        return None
    return DatasetRelease.objects.filter(pk=pk).first()


def _taxid(taxon):
    """The taxid a caller gave, as a row or the id itself."""
    return taxon.taxid if isinstance(taxon, Taxon) else taxon


def _strings(keys) -> list:
    return sorted(key for key in keys if isinstance(key, str))


# ── The write path ──────────────────────────────────────────────────────────


def create_entity(
    entity_type, label, taxon, namespace, value, *, release=None
) -> Entity:
    """The entity this value defines, created with its primary identifier if
    it does not exist yet.

    entity_type and namespace are rows or slugs, taxon a row, a taxid or None.
    Creating the same entity again returns the existing row, also when two
    transactions race: the insert is ON CONFLICT DO NOTHING on identity_key,
    followed by a re-select.

    release: the release that introduces the entity, a row or its id. It is
    locked, so its status and kind are read as they are when the entity is
    written. It must be a draft or staged, and needs an effective internal
    approval unless it is synthetic; a synthetic release introduces only
    entities of taxon 32630. A new entity names the release in
    created_by_release, and is active if the release is a reference release,
    provisional otherwise. An existing entity is returned unchanged: the
    first writer keeps its provenance. Without a release, new entities are
    provisional and name none.

    The rules are creation_problem's, applied by the same functions to the
    rows read under the locks.
    """
    if release is not None:
        require_switch(ReleaseProvenanceError, "no release introduces entities")
    with transaction.atomic():
        if release is not None:
            release = locked_release(release, ReleaseProvenanceError)
        using = router.db_for_write(Entity)
        share_lock(EntityType, using, [_key(entity_type, EntityType)])
        share_lock(IdentifierNamespace, using, [_key(namespace, IdentifierNamespace)])
        return _create(entity_type, label, taxon, namespace, value, release)


def _create(entity_type, label, taxon, namespace, value, release) -> Entity:
    entity_type = _row(EntityType, entity_type, "entity type")
    namespace = _row(IdentifierNamespace, namespace, "namespace")
    taxon = _taxon(taxon)
    taxid = taxon.taxid if taxon else None
    plan = _creation(
        entity_type,
        taxid,
        namespace,
        _allowed_types(namespace),
        value,
        label,
        lambda: identity_namespace(entity_type, taxon),
    )
    if isinstance(plan, Problem):
        raise plan.exception()
    written = (plan.lookup, plan.version, plan.scope, plan.key)
    if release is None:
        return _get_or_insert(
            entity_type, plan.label, taxon, namespace, value, *written
        )
    problem = _introducing_problem(release, taxid)
    if problem:
        raise problem.exception()
    status = (
        EntityStatus.ACTIVE
        if release.kind == ReleaseKind.REFERENCE
        else EntityStatus.PROVISIONAL
    )
    return _get_or_insert(
        entity_type,
        plan.label,
        taxon,
        namespace,
        value,
        *written,
        created_by_release=release,
        status=status,
    )


def _get_or_insert(
    entity_type, label, taxon, namespace, value, lookup, version, scope, key, **new
) -> Entity:
    """The entity with this identity key, inserted with its primary
    identifier if there is none. `new` holds what only a new entity gets."""
    existing = Entity.objects.filter(identity_key=key).first()
    if existing is not None:
        return _same_entity(existing, entity_type, taxon, key)
    holders = (
        EntityIdentifier.objects.filter(
            namespace=namespace,
            lookup=lookup,
            scope_key=scope,
            entity__entity_type=entity_type,
            entity__taxon=taxon,
        )
        .values_list("entity_id", flat=True)
        .distinct()
    )
    if holders:
        holders = sorted(holders)
        raise IdentityConflict(_held_by(namespace, lookup, holders), holders)

    with transaction.atomic():
        Entity.objects.bulk_create(
            [
                Entity(
                    entity_type=entity_type,
                    identity_key=key,
                    label=label,
                    label_key=label.upper()[:255],
                    taxon=taxon,
                    **new,
                )
            ],
            ignore_conflicts=True,
        )
        entity = _same_entity(
            Entity.objects.get(identity_key=key), entity_type, taxon, key
        )
        EntityIdentifier.objects.bulk_create(
            [
                EntityIdentifier(
                    entity=entity,
                    namespace=namespace,
                    value=value.strip(),
                    lookup=lookup,
                    version=version,
                    scope_key=scope,
                    role=IdentifierRole.PRIMARY,
                )
            ],
            ignore_conflicts=True,
        )
        # Skipped only when a concurrent creator wrote it first, for this same
        # entity. Any other holder means rows written around this function, and
        # the entity is not created without its defining identifier.
        holder = (
            EntityIdentifier.objects.filter(
                namespace=namespace,
                lookup=lookup,
                scope_key=scope,
                role=IdentifierRole.PRIMARY,
            )
            .values_list("entity_id", flat=True)
            .first()
        )
        if holder != entity.pk:
            raise IdentityConflict(
                f"{key}: the primary identifier belongs to entity {holder}, "
                f"not to entity {entity.pk}.",
                [holder] if holder is not None else [],
            )
    return entity


def add_identifier(entity, namespace, value, role) -> EntityIdentifier:
    """Attach a cross-reference, secondary accession, alias or symbol.

    Primary identifiers are written only by create_entity, with their entity.
    No import stores these before their provenance can be recorded (RF1a).
    """
    if role not in NON_PRIMARY_ROLES:
        raise ValueError(
            f"add_identifier writes {', '.join(NON_PRIMARY_ROLES)} identifiers, "
            f"not {role!r}; a primary identifier comes only with its entity."
        )
    namespace = _row(IdentifierNamespace, namespace, "namespace")
    allowed = _allowed_types(namespace)
    if entity.entity_type_id not in allowed:
        raise NamespaceTypeError(
            f"{namespace.slug} does not identify {entity.entity_type_id} entities."
        )
    if namespace.scope == Scope.TAXON and entity.taxon_id is None:
        raise TaxonPolicyError(f"{namespace.slug} identifiers belong to a taxon.")
    fixed = namespace.fixed_taxon_id
    if fixed is not None and entity.taxon_id != fixed:
        raise TaxonPolicyError(
            f"{namespace.slug} identifiers belong to taxon {fixed}, "
            f"not {entity.taxon_id}."
        )
    lookup, version = normalize(namespace, value)
    _check_form(namespace, allowed, lookup, entity.entity_type_id)
    identifier, _ = EntityIdentifier.objects.get_or_create(
        entity=entity,
        namespace=namespace,
        lookup=lookup,
        scope_key=scope_key(namespace, entity.taxon_id),
        role=role,
        defaults={"value": value.strip(), "version": version},
    )
    return identifier


def _key(value, model):
    """The registry row's key a caller gave, as a row or a slug; None for
    anything else, which _row refuses."""
    pk = value.pk if isinstance(value, model) else value
    return pk if isinstance(pk, str) else None


def _row(model, value, what):
    """The row as stored, read again when a row is given: none of its
    attributes is trusted."""
    pk = value.pk if isinstance(value, model) else value
    try:
        return model.objects.get(pk=pk)
    except (model.DoesNotExist, ValueError, TypeError):
        raise ValueError(f"Unknown {what} {value!r}.") from None


def _taxon(value) -> Taxon | None:
    """The taxon a caller gave, read as the read-only rules read it
    (_Registry.known): a row, or a taxid that is an int, never a bool, a
    string or a float that the database would turn into one."""
    if value is None or isinstance(value, Taxon):
        return value
    row = None
    if isinstance(value, int) and not isinstance(value, bool) and storable([value]):
        row = Taxon.objects.filter(pk=value).first()
    if row is None:
        raise TaxonPolicyError(f"Taxon {value!r} is not in the registry.")
    return row


def _allowed_types(namespace) -> set[str]:
    return set(namespace.allowed_entity_types.values_list("slug", flat=True))


def _check_form(namespace, allowed, lookup, type_slug):
    problem = _form_problem(namespace, allowed, lookup, type_slug)
    if problem:
        raise problem.exception()


def _same_entity(entity, entity_type, taxon, key) -> Entity:
    taxon_id = taxon.taxid if taxon else None
    if entity.entity_type_id != entity_type.slug or entity.taxon_id != taxon_id:
        raise IdentityConflict(
            _identifies_another(
                key,
                {
                    "pk": entity.pk,
                    "entity_type_id": entity.entity_type_id,
                    "taxon_id": entity.taxon_id,
                },
            ),
            [entity.pk],
        )
    return entity


def _identifies_another(key, entity) -> str:
    return (
        f"{key} already identifies entity {entity['pk']}, a "
        f"{entity['entity_type_id']} of taxon {entity['taxon_id']}."
    )


def _held_by(namespace, lookup, holders) -> str:
    return (
        f"{namespace.slug} {lookup} is already held by entity "
        f"{', '.join(map(str, sorted(holders)))}; it does not define a new one."
    )


def _describe(entity_type, taxon) -> str:
    slug = entity_type.slug if isinstance(entity_type, EntityType) else entity_type
    taxid = taxon.taxid if isinstance(taxon, Taxon) else taxon
    return f"{slug} entities of taxon {taxid}" if taxid else f"{slug} entities"

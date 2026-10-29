"""The write path for entities and their identifiers.

A check constraint cannot span tables, so the rules that do are enforced
here, in one tested place:

- the entity type's taxon policy (a gene needs a taxon);
- which entity types a namespace may identify, and, where one namespace
  identifies both protein entries and isoforms, which of the two a value is;
- the identity rule: an entity is created only from the one namespace that
  defines identity for its type and taxon, and never from a value that an
  existing entity of that type and taxon already holds, in any role.
"""

from django.db import transaction

from .identity import identity_key, normalize, scope_key, uniprot_entity_type
from .models import (
    Entity,
    EntityIdentifier,
    EntityType,
    IdentifierNamespace,
    IdentifierRole,
    Scope,
    Taxon,
    TaxonPolicy,
)

# Namespaces that identify both tell them apart by the value's form.
ISOFORM_PAIR = {"protein", "protein_isoform"}
NON_PRIMARY_ROLES = (
    IdentifierRole.XREF,
    IdentifierRole.SECONDARY,
    IdentifierRole.ALIAS,
    IdentifierRole.SYMBOL,
)


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


class IdentityConflict(EntityError):
    """The value already names a different entity, or is held by one."""

    def __init__(self, message: str, entity_ids):
        super().__init__(message)
        self.entity_ids = tuple(sorted(entity_ids))

    @property
    def entity_id(self) -> int | None:
        """The existing entity, when exactly one holds the value."""
        return self.entity_ids[0] if len(self.entity_ids) == 1 else None


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
    levels = [defining.filter(scope=Scope.GLOBAL, fixed_taxon__isnull=True)]
    if taxon is not None:
        levels.insert(0, defining.filter(scope=Scope.TAXON, fixed_taxon=taxon))
    for level in levels:
        found = list(level)
        if len(found) > 1:
            slugs = ", ".join(namespace.slug for namespace in found)
            raise IdentityNamespaceError(
                f"Several namespaces define identity for {_describe(entity_type, taxon)}"
                f" ({slugs}); the registry allows one."
            )
        if found:
            return found[0]
    return None


def create_entity(entity_type, label, taxon, namespace, value) -> Entity:
    """The entity this value defines, created with its primary identifier if
    it does not exist yet.

    entity_type and namespace are rows or slugs, taxon a row, a taxid or None.
    Creating the same entity again returns the existing row, also when two
    transactions race: the insert is ON CONFLICT DO NOTHING on identity_key,
    followed by a re-select. New entities are provisional; nothing yet marks
    one as confirmed by a reference release.
    """
    entity_type = _row(EntityType, entity_type, "entity type")
    namespace = _row(IdentifierNamespace, namespace, "namespace")
    taxon = _taxon(taxon)
    _check_taxon_policy(entity_type, taxon)

    allowed = _allowed_types(namespace)
    if entity_type.slug not in allowed:
        raise NamespaceTypeError(
            f"{namespace.slug} does not identify {entity_type.slug} entities."
        )
    lookup, version = normalize(namespace, value)
    _check_form(namespace, allowed, lookup, entity_type.slug)

    defining = identity_namespace(entity_type, taxon)
    if defining is None:
        raise IdentityNamespaceError(
            f"No namespace defines identity for {_describe(entity_type, taxon)},"
            " so none can be created."
        )
    if defining.slug != namespace.slug:
        raise IdentityNamespaceError(
            f"{_describe(entity_type, taxon)} are created only from "
            f"{defining.slug} identifiers, not from {namespace.slug}."
        )

    label = label.strip()
    if not label:
        raise ValueError("An entity needs a label.")
    # SQLite does not enforce the column size, so it is checked here.
    label_length = Entity._meta.get_field("label").max_length
    if len(label) > label_length:
        raise ValueError(f"An entity label is at most {label_length} characters.")
    scope = scope_key(namespace, taxon.taxid if taxon else None)
    key = identity_key(namespace, scope, lookup)

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
        raise IdentityConflict(
            f"{namespace.slug} {lookup} is already held by entity "
            f"{', '.join(map(str, holders))}; it does not define a new one.",
            holders,
        )

    with transaction.atomic():
        Entity.objects.bulk_create(
            [
                Entity(
                    entity_type=entity_type,
                    identity_key=key,
                    label=label,
                    label_key=label.upper()[:255],
                    taxon=taxon,
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


def _row(model, value, what):
    if isinstance(value, model):
        return value
    try:
        return model.objects.get(pk=value)
    except model.DoesNotExist:
        raise ValueError(f"Unknown {what} {value!r}.") from None


def _taxon(value) -> Taxon | None:
    if value is None or isinstance(value, Taxon):
        return value
    try:
        return Taxon.objects.get(pk=value)
    except (Taxon.DoesNotExist, ValueError, TypeError):
        raise TaxonPolicyError(f"Taxon {value!r} is not in the registry.") from None


def _check_taxon_policy(entity_type, taxon):
    if entity_type.taxon_policy == TaxonPolicy.REQUIRED and taxon is None:
        raise TaxonPolicyError(f"A {entity_type.slug} needs a taxon.")
    if entity_type.taxon_policy == TaxonPolicy.FORBIDDEN and taxon is not None:
        raise TaxonPolicyError(f"A {entity_type.slug} has no taxon.")


def _allowed_types(namespace) -> set[str]:
    return set(namespace.allowed_entity_types.values_list("slug", flat=True))


def _check_form(namespace, allowed, lookup, type_slug):
    if ISOFORM_PAIR <= allowed:
        form = uniprot_entity_type(lookup)
        if form != type_slug:
            raise NamespaceTypeError(
                f"{namespace.slug} {lookup} is a {form} identifier, not a {type_slug}."
            )


def _same_entity(entity, entity_type, taxon, key) -> Entity:
    taxon_id = taxon.taxid if taxon else None
    if entity.entity_type_id != entity_type.slug or entity.taxon_id != taxon_id:
        raise IdentityConflict(
            f"{key} already identifies entity {entity.pk}, a {entity.entity_type_id}"
            f" of taxon {entity.taxon_id}.",
            [entity.pk],
        )
    return entity


def _describe(entity_type, taxon) -> str:
    slug = entity_type.slug if isinstance(entity_type, EntityType) else entity_type
    taxid = taxon.taxid if isinstance(taxon, Taxon) else taxon
    return f"{slug} entities of taxon {taxid}" if taxid else f"{slug} entities"

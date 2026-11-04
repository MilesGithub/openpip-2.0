"""What an adapter reads besides its stream: the run's context.

A run reads the registry once, before it reads its file, into an in-memory
snapshot (load_registry_snapshot): the namespaces with their rules, the
entity types, the taxa, the relationship types with their allowed entity
types and orientation, the score types, and the synthetic namespaces. The
adapter reads it, and never the database, so map_record makes no query. The
registry cannot change under a run: every batch's write transaction checks
the database's registry version again, and graph_sync_registry bumps it with
any change of rules.

A synthetic namespace is a registry namespace whose fixed taxon is 32630,
NCBI Taxonomy's synthetic construct (today synthetic_gene and
synthetic_protein): a synthetic release names nothing else.
"""

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cached_property
from types import MappingProxyType

from graph.models import (
    SYNTHETIC_TAXON,
    EntityType,
    IdentifierNamespace,
    RelationshipType,
    ScoreType,
    Taxon,
)
from graph.registry import registry_state

from .manifest import EMPTY_MANIFEST, Manifest


@dataclass(frozen=True)
class NamespaceRules:
    """A namespace's rules, with IdentifierNamespace's field names, so
    graph.identity's functions take it as they take a row."""

    slug: str
    curie_prefix: str
    scope: str
    fixed_taxon_id: int | None
    case_policy: str
    version_policy: str
    pattern: str
    defines_identity: bool
    allowed_entity_types: frozenset[str]


@dataclass(frozen=True)
class EntityTypeRules:
    slug: str
    taxon_policy: str


@dataclass(frozen=True)
class RelationshipTypeRules:
    slug: str
    family: str
    orientation: str
    polarity: str
    subject_types: frozenset[str]
    object_types: frozenset[str]
    self_pairs_allowed: bool
    is_reference: bool


@dataclass(frozen=True)
class ScoreTypeRules:
    slug: str
    value_kind: str
    direction: str
    range_min: float | None
    range_max: float | None
    null_model: str


@dataclass(frozen=True)
class RegistrySnapshot:
    """The registry as one run reads it. Each mapping is keyed by slug (taxa
    by taxid)."""

    # The registry version the database held when it was read.
    version: str | None
    namespaces: Mapping[str, NamespaceRules] = field(default_factory=dict)
    entity_types: Mapping[str, EntityTypeRules] = field(default_factory=dict)
    taxa: frozenset[int] = frozenset()
    relationship_types: Mapping[str, RelationshipTypeRules] = field(
        default_factory=dict
    )
    score_types: Mapping[str, ScoreTypeRules] = field(default_factory=dict)

    __hash__ = None

    def __post_init__(self):
        for name in ("namespaces", "entity_types", "relationship_types", "score_types"):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))
        object.__setattr__(self, "taxa", frozenset(self.taxa))

    @cached_property
    def synthetic_namespaces(self) -> frozenset[str]:
        """The namespaces fixed to the synthetic construct's taxon."""
        return frozenset(
            slug
            for slug, rules in self.namespaces.items()
            if rules.fixed_taxon_id == SYNTHETIC_TAXON
        )


def load_registry_snapshot() -> RegistrySnapshot:
    """The registry as stored now, in nine queries."""
    allowed = defaultdict(set)
    through = IdentifierNamespace.allowed_entity_types.through
    for slug, type_slug in through.objects.values_list(
        "identifiernamespace_id", "entitytype_id"
    ):
        allowed[slug].add(type_slug)
    namespaces = {
        row.slug: NamespaceRules(
            slug=row.slug,
            curie_prefix=row.curie_prefix,
            scope=row.scope,
            fixed_taxon_id=row.fixed_taxon_id,
            case_policy=row.case_policy,
            version_policy=row.version_policy,
            pattern=row.pattern,
            defines_identity=row.defines_identity,
            allowed_entity_types=frozenset(allowed[row.slug]),
        )
        for row in IdentifierNamespace.objects.order_by("slug")
    }
    entity_types = {
        slug: EntityTypeRules(slug, policy)
        for slug, policy in EntityType.objects.values_list("slug", "taxon_policy")
    }
    sides = {}
    for name in ("allowed_subject_types", "allowed_object_types"):
        found = defaultdict(set)
        for slug, type_slug in getattr(
            RelationshipType, name
        ).through.objects.values_list("relationshiptype_id", "entitytype_id"):
            found[slug].add(type_slug)
        sides[name] = found
    relationship_types = {
        row["slug"]: RelationshipTypeRules(
            subject_types=frozenset(sides["allowed_subject_types"][row["slug"]]),
            object_types=frozenset(sides["allowed_object_types"][row["slug"]]),
            **row,
        )
        for row in RelationshipType.objects.values(
            "slug",
            "family",
            "orientation",
            "polarity",
            "self_pairs_allowed",
            "is_reference",
        )
    }
    score_types = {
        row["slug"]: ScoreTypeRules(**row)
        for row in ScoreType.objects.values(
            "slug", "value_kind", "direction", "range_min", "range_max", "null_model"
        )
    }
    return RegistrySnapshot(
        version=registry_state().database,
        namespaces=namespaces,
        entity_types=entity_types,
        taxa=frozenset(Taxon.objects.values_list("taxid", flat=True)),
        relationship_types=relationship_types,
        score_types=score_types,
    )


@dataclass(frozen=True)
class RunContext:
    """What an adapter reads besides its stream (the `ctx` of
    Adapter.iter_records and Adapter.map_record)."""

    registry: RegistrySnapshot
    # The release's flag: a synthetic release names synthetic constructs and
    # synthetic papers only.
    is_synthetic: bool
    # The release's manifest; a release without one declares nothing.
    manifest: Manifest = field(default_factory=lambda: EMPTY_MANIFEST)
    release_id: int | None = None
    dataset_id: int | None = None

    __hash__ = None

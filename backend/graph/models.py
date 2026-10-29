"""Typed entities and their identifiers, and the registries they are typed by.

These tables sit beside the PPI tables and never reference them: no foreign
key leaves this app except, later, to `user`. A key into `protein` or
`dataset` would be emptied by `migrate_legacy.py --reset`, which truncates
with CASCADE.
"""

from django.db import models
from django.db.models import Q
from django.db.models.functions import Upper

# ── Closed sets ─────────────────────────────────────────────────────────────
# Every value the design names is here from the start, so the registry JSON
# can use one without a migration.


class TaxonPolicy(models.TextChoices):
    REQUIRED = "required"
    OPTIONAL = "optional"
    FORBIDDEN = "forbidden"


class IdentityScheme(models.TextChoices):
    # <namespace>|<scope key>|<lookup> of the entity's defining identifier.
    IDENTIFIER = "identifier"
    # For genomic regions and variants, which arrive later.
    COORDINATES = "coordinates"
    ALLELE = "allele"
    SOURCE_LOCAL = "source_local"


class Scope(models.TextChoices):
    # A value means one thing whatever the organism (UniProtKB accessions).
    GLOBAL = "global"
    # A value is unique only within a taxon (organism-owned nomenclatures).
    TAXON = "taxon"
    # A value is unique only within a genome assembly (coordinates, later).
    ASSEMBLY = "assembly"


class CasePolicy(models.TextChoices):
    # Case tells values apart (FlyBase: h is hairy, H is Hairless).
    EXACT = "exact"
    # Values are defined in upper case; anything else is the same value.
    UPPER = "upper"
    # Case never tells values apart, and a match says when it differed.
    INSENSITIVE = "insensitive"


class VersionPolicy(models.TextChoices):
    NONE = "none"
    # A trailing ".<digits>" is a version: split off and kept beside the lookup.
    SPLIT = "split"


class EntityStatus(models.TextChoices):
    ACTIVE = "active"
    # Created from evidence rather than from a reference release.
    PROVISIONAL = "provisional"
    MERGED = "merged"
    RETIRED = "retired"


class IdentifierRole(models.TextChoices):
    # The entity's defining identifier, exactly one per entity.
    PRIMARY = "primary"
    # An identifier in another resource (an NCBI Gene ID on an HGNC gene).
    XREF = "xref"
    # A former primary value: a UniProt secondary accession, an HGNC previous
    # symbol.
    SECONDARY = "secondary"
    ALIAS = "alias"
    # The current official symbol in an organism-owned symbol namespace.
    SYMBOL = "symbol"


# ── Registries ──────────────────────────────────────────────────────────────
# Rows come only from the reviewed JSON in graph/registry/, loaded by
# `manage.py graph_sync_registry`; no migration writes them. Slug and taxid
# primary keys keep the foreign keys to them readable and stable.


class Taxon(models.Model):
    """An NCBI Taxonomy taxon. PSI-MI's pseudo-taxa (-1 "in vitro" and the
    rest) are never taxa: they describe an assay, not an organism."""

    taxid = models.IntegerField(primary_key=True)
    scientific_name = models.CharField(max_length=255)
    common_name = models.CharField(max_length=255, blank=True)
    rank = models.CharField(max_length=64, blank=True)
    # Lets a strain match its species without either being rewritten.
    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="children",
    )
    merged_into = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="merged_taxa",
    )
    # Where the names and rank were checked.
    label_source = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=Q(taxid__gt=0), name="graph_taxon_taxid_positive"
            ),
        ]

    def __str__(self):
        return f"{self.scientific_name} ({self.taxid})"


class EntityType(models.Model):
    slug = models.SlugField(max_length=50, primary_key=True)
    label = models.CharField(max_length=100)
    definition = models.TextField()
    taxon_policy = models.CharField(max_length=16, choices=TaxonPolicy.choices)
    identity_scheme = models.CharField(max_length=16, choices=IdentityScheme.choices)
    assembly_required = models.BooleanField(default=False)
    display_token = models.CharField(max_length=50, blank=True)

    def __str__(self):
        return self.slug


class IdentifierNamespace(models.Model):
    slug = models.SlugField(max_length=50, primary_key=True)
    label = models.CharField(max_length=100)
    # Written before a value to name this namespace: "uniprotkb:P04637".
    curie_prefix = models.CharField(max_length=50)
    allowed_entity_types = models.ManyToManyField(EntityType, related_name="namespaces")
    scope = models.CharField(max_length=16, choices=Scope.choices)
    # For organism-owned nomenclatures: HGNC IDs name human genes only.
    fixed_taxon = models.ForeignKey(
        Taxon,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="namespaces",
    )
    case_policy = models.CharField(max_length=16, choices=CasePolicy.choices)
    version_policy = models.CharField(max_length=16, choices=VersionPolicy.choices)
    # Whether an entity of an allowed type may be created from a value here.
    # At most one namespace does, per entity type and taxon.
    defines_identity = models.BooleanField(default=False)
    # A regular expression the whole lookup must match.
    pattern = models.CharField(max_length=500)
    # A link to a value's record; "{lookup}" is replaced by the lookup.
    url_template = models.CharField(max_length=500, blank=True)
    # The document these rules were copied from.
    authority = models.TextField()

    def __str__(self):
        return self.slug


class RegistryState(models.Model):
    """The registry version graph_sync_registry last loaded: one row."""

    id = models.SmallIntegerField(primary_key=True, default=1)
    version = models.CharField(max_length=32)
    synced_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.CheckConstraint(check=Q(id=1), name="graph_registry_state_one_row"),
        ]

    def __str__(self):
        return self.version


# ── Entities ────────────────────────────────────────────────────────────────


class Entity(models.Model):
    """One typed node: a gene, a protein entry or a protein isoform, each its
    own entity even when they share a name.

    Write through graph.entities.create_entity, which enforces the rules
    that span tables (a gene needs a taxon; only the namespace that defines
    identity for the type and taxon creates one).
    """

    entity_type = models.ForeignKey(
        EntityType, on_delete=models.PROTECT, related_name="entities"
    )
    # <namespace>|<scope key>|<lookup> of the defining identifier, which fits
    # in 400 characters (50 + 64 + 255 and two separators). Never changes.
    # Unique, so two imports creating the same entity at once get one row.
    identity_key = models.CharField(max_length=400, unique=True)
    # A display name, not identity.
    label = models.CharField(max_length=255)
    # The label upper-cased, for prefix search. On PostgreSQL db_index also
    # creates the varchar_pattern_ops index that LIKE 'ABC%' needs under a
    # non-C collation.
    label_key = models.CharField(max_length=255, db_index=True)
    description = models.TextField(blank=True)
    taxon = models.ForeignKey(
        Taxon,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="entities",
    )
    status = models.CharField(
        max_length=16, choices=EntityStatus.choices, default=EntityStatus.PROVISIONAL
    )
    # Set only by the audited merge, which comes later.
    merged_into = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="merged_entities",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["entity_type", "taxon"], name="graph_entity_type_taxon"
            ),
        ]
        constraints = [
            models.CheckConstraint(
                check=Q(status__in=EntityStatus.values),
                name="graph_entity_status_valid",
            ),
            models.CheckConstraint(
                check=(
                    Q(status=EntityStatus.MERGED, merged_into__isnull=False)
                    | (~Q(status=EntityStatus.MERGED) & Q(merged_into__isnull=True))
                ),
                name="graph_entity_merged_into_iff_merged",
            ),
        ]

    def __str__(self):
        return f"{self.entity_type_id} {self.label}"


class EntityIdentifier(models.Model):
    entity = models.ForeignKey(
        Entity, on_delete=models.CASCADE, related_name="identifiers"
    )
    namespace = models.ForeignKey(
        IdentifierNamespace, on_delete=models.PROTECT, related_name="identifiers"
    )
    # As the source wrote it, prefix removed.
    value = models.CharField(max_length=255)
    # Normalized by the namespace's case and version policies (graph.identity).
    lookup = models.CharField(max_length=255)
    version = models.CharField(max_length=32, blank=True, default="")
    # "" for global namespaces, "t:<taxid>" or "a:<assembly>" for scoped ones.
    # Never NULL, so the unique keys behave the same on PostgreSQL and SQLite.
    scope_key = models.CharField(max_length=64, blank=True, default="")
    role = models.CharField(max_length=16, choices=IdentifierRole.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # A defining identifier names at most one entity...
            models.UniqueConstraint(
                fields=["namespace", "lookup", "scope_key"],
                condition=Q(role=IdentifierRole.PRIMARY),
                name="graph_ident_primary_unique",
            ),
            # ...and each entity has exactly one, written with it.
            models.UniqueConstraint(
                fields=["entity"],
                condition=Q(role=IdentifierRole.PRIMARY),
                name="graph_ident_one_primary",
            ),
            # Cross-references, secondary accessions, aliases and symbols are
            # not unique across entities: a UniProt split leaves the old
            # accession on every new entry, and an HGNC previous symbol can
            # point at several genes. They only ever produce candidates.
            models.UniqueConstraint(
                fields=["entity", "namespace", "lookup", "scope_key", "role"],
                name="graph_ident_unique_row",
            ),
            models.CheckConstraint(
                check=Q(role__in=IdentifierRole.values),
                name="graph_ident_role_valid",
            ),
        ]
        indexes = [
            models.Index(
                fields=["namespace", "lookup", "scope_key"],
                name="graph_ident_ns_lookup_scope",
            ),
            models.Index(fields=["lookup"], name="graph_ident_lookup"),
            # Finds case-mismatch candidates in case-exact namespaces without
            # a table scan.
            models.Index(Upper("lookup"), name="graph_ident_lookup_upper"),
        ]

    def __str__(self):
        return f"{self.namespace_id}:{self.value} ({self.role})"

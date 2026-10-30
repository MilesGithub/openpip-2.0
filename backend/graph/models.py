"""Typed entities and their identifiers, the registries they are typed by, the
provenance and licensing records of the releases that bring data in, and the
relationships between entities with the evidence each release states for them.

These tables sit beside the PPI tables and never reference them: the only
foreign keys that leave this app point at `user`, for who decided, recorded
or triggered something (SET_NULL, with the username kept beside each). A key
into `protein` or `dataset` would be emptied by `migrate_legacy.py --reset`,
which truncates with CASCADE.
"""

import contextvars
import json
import math
import re
import sys
from collections import defaultdict
from decimal import Decimal
from functools import lru_cache

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import connections, models, router, transaction
from django.db.models import F, Q
from django.db.models.fields.related_descriptors import (
    ReverseManyToOneDescriptor,
    create_reverse_many_to_one_manager,
)
from django.db.models.functions import Upper
from django.utils import timezone
from django.utils.functional import cached_property

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


# ── Closed sets: provenance and licensing ───────────────────────────────────


class DataSourceKind(models.TextChoices):
    LAB = "lab"
    CONSORTIUM = "consortium"
    DATABASE = "database"
    # This portal's own curation, the synthetic demonstration included.
    THIS_PORTAL = "this_portal"
    OTHER = "other"


class DatasetStatus(models.TextChoices):
    # Catalogue description only, gating nothing (SourceDataset.status).
    # candidate: known, with nothing imported.
    CANDIDATE = "candidate"
    ACTIVE = "active"
    WITHDRAWN = "withdrawn"


class PublicationRole(models.TextChoices):
    PRIMARY = "primary"
    DESCRIBES = "describes"
    UPDATE = "update"
    ERRATUM = "erratum"
    RELATED = "related"


class ReleaseKind(models.TextChoices):
    # Entities it creates are provisional until a reference release states
    # them (architecture §4.1).
    EVIDENCE = "evidence"
    # From a reference adapter, such as an HGNC or UniProt release.
    REFERENCE = "reference"


class ReleaseStatus(models.TextChoices):
    DRAFT = "draft"
    # Committed but not published: it can still be discarded back to draft.
    STAGED = "staged"
    PUBLISHED = "published"
    # A published correction names it in `supersedes`: out of default views.
    SUPERSEDED = "superseded"
    WITHDRAWN = "withdrawn"


class FileRole(models.TextChoices):
    DATA = "data"
    ENTITIES = "entities"
    MANIFEST = "manifest"
    README = "readme"
    LICENCE_TEXT = "licence_text"
    SUPPLEMENTARY = "supplementary"


class ImportMode(models.TextChoices):
    DRY_RUN = "dry_run"
    COMMIT = "commit"


class ImportState(models.TextChoices):
    RUNNING = "running"
    # Stopped part-way; a commit run in this state is resumed (G5).
    FAILED = "failed"
    COMPLETED = "completed"
    # Given up by a discard, and never resumed.
    ABANDONED = "abandoned"


class RecordKeyBasis(models.TextChoices):
    # The source's own record id.
    SOURCE_ID = "source_id"
    # The line number in the checksummed file.
    LINE_NUMBER = "line_number"
    # A hash of the content, plus its occurrence.
    CONTENT_HASH = "content_hash"


class RecordStatus(models.TextChoices):
    MAPPED = "mapped"
    UNRESOLVED = "unresolved"
    AMBIGUOUS = "ambiguous"
    REJECTED = "rejected"
    SKIPPED = "skipped"


class DueDiligenceStatus(models.TextChoices):
    OPEN = "open"
    COMPLETE = "complete"
    BLOCKED = "blocked"


class Permission(models.TextChoices):
    PERMITTED = "permitted"
    PERMITTED_WITH_CONDITIONS = "permitted_with_conditions"
    NOT_PERMITTED = "not_permitted"
    # What every term is until a person records it. Never a permission.
    UNKNOWN = "unknown"


# What "permitted" means wherever a rule asks for it: unknown is never
# permission, and neither is not_permitted.
PERMITTED_VALUES = frozenset(
    {Permission.PERMITTED, Permission.PERMITTED_WITH_CONDITIONS}
)


class ShareAlike(models.TextChoices):
    # Adapted material must carry the same licence, as CC BY-SA requires.
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


class ApprovalScope(models.TextChoices):
    INTERNAL = "internal"
    PUBLIC_VIEW = "public_view"
    PUBLIC_BULK = "public_bulk"
    # Sending a release's identifiers to an outside service (architecture D9).
    EXTERNAL_SERVICES = "external_services"


class Decision(models.TextChoices):
    APPROVED = "approved"
    DENIED = "denied"


# ── Closed sets: relationships and evidence ─────────────────────────────────
# No allowed value anywhere is called "negative" except GeneticSign.NEGATIVE,
# whose meaning its score type's null model defines (architecture §4.3).


class RelationshipFamily(models.TextChoices):
    # Every family of architecture §4.2, so a later family needs no
    # constraint change.
    PHYSICAL = "physical"
    REGULATION = "regulation"
    RNA = "rna"
    GENETIC = "genetic"
    MODIFICATION = "modification"
    LIGAND_RECEPTOR = "ligand_receptor"
    ASSOCIATION = "association"
    PHENOTYPE = "phenotype"
    MEMBERSHIP = "membership"
    # A link between entity layers (encodes, isoform_of), not an
    # evidence-backed claim about biology.
    REFERENCE = "reference"


class Orientation(models.TextChoices):
    # A-B is B-A: the pair is stored lower entity id first.
    SYMMETRIC = "symmetric"
    # A causal direction: A->B and B->A are different claims.
    DIRECTED = "directed"
    # Two roles in a fixed order (gene, protein entry), with no causal claim.
    ROLE_ORDERED = "role_ordered"


class Polarity(models.TextChoices):
    # Which of effect and genetic_sign the type's evidence may carry.
    NONE = "none"
    EFFECT = "effect"
    GENETIC_SIGN = "genetic_sign"


class Outcome(models.TextChoices):
    SUPPORTED = "supported"
    # Tested, and explicitly reported as not found (MITAB column 36).
    TESTED_ABSENT = "tested_absent"
    INCONCLUSIVE = "inconclusive"
    # Measured, but below the source's threshold: neither support nor
    # absence.
    NOT_SIGNIFICANT = "not_significant"


class EvidenceBasis(models.TextChoices):
    # What the claim rests on.
    EXPERIMENTAL = "experimental"
    COMPUTATIONAL_PREDICTION = "computational_prediction"
    COMPUTATIONAL_INFERENCE = "computational_inference"
    STATISTICAL = "statistical"
    TEXT_MINING = "text_mining"
    UNKNOWN = "unknown"


class AssertionMethod(models.TextChoices):
    # How the source made the statement, kept apart from the basis.
    MANUAL_ASSERTION = "manual_assertion"
    AUTOMATIC_ASSERTION = "automatic_assertion"
    UNKNOWN = "unknown"


class Effect(models.TextChoices):
    ACTIVATING = "activating"
    INHIBITING = "inhibiting"
    # The source says the effect is unknown; NULL means it states none.
    UNKNOWN = "unknown"
    # A summary value for types without effect polarity, never stored.
    NOT_APPLICABLE = "not_applicable"


class GeneticSign(models.TextChoices):
    NEGATIVE = "negative"
    POSITIVE = "positive"


class InteractionLevel(models.TextChoices):
    # PSI-MI MI:0914 and its subtree outside MI:0915: co-purification.
    ASSOCIATION = "association"
    # MI:0915 outside MI:0407.
    PHYSICAL_ASSOCIATION = "physical_association"
    # MI:0407 and its subtree.
    DIRECT_INTERACTION = "direct_interaction"
    NOT_STATED = "not_stated"


# How strong each level's claim is: an edge shows the strongest level its
# supporting evidence states (architecture D20).
LEVEL_STRENGTH = {
    InteractionLevel.DIRECT_INTERACTION: 3,
    InteractionLevel.PHYSICAL_ASSOCIATION: 2,
    InteractionLevel.ASSOCIATION: 1,
    InteractionLevel.NOT_STATED: 0,
}


class ParticipantSide(models.TextChoices):
    SUBJECT = "subject"
    OBJECT = "object"


class ResolutionBasis(models.TextChoices):
    # How the source's identifier was mapped to the entity (architecture
    # §4.1).
    PRIMARY = "primary"
    XREF_UNIQUE = "xref_unique"
    ALIAS_UNIQUE = "alias_unique"
    # The source named an isoform, recorded on its entry (open question B12).
    ISOFORM_ENTRY = "isoform_entry"
    SOURCE_LOCAL = "source_local"
    CURATED = "curated"


class MethodRole(models.TextChoices):
    DETECTION = "detection"
    PARTICIPANT_IDENTIFICATION = "participant_identification"
    EVIDENCE_CODE = "evidence_code"


class EvidencePublicationRole(models.TextChoices):
    REPORTED_IN = "reported_in"
    METHOD_REFERENCE = "method_reference"
    CITED = "cited"


class ContextFacet(models.TextChoices):
    HOST_TAXON = "host_taxon"
    TISSUE = "tissue"
    CELL_TYPE = "cell_type"
    CELL_LINE = "cell_line"
    CONDITION = "condition"
    PERTURBATION = "perturbation"
    COMPARTMENT = "compartment"
    # PSI-MI's pseudo-taxa (-1 "in vitro" and the rest) belong here, never
    # under host_taxon.
    ASSAY_SYSTEM = "assay_system"
    DEVELOPMENTAL_STAGE = "developmental_stage"
    TIMEPOINT = "timepoint"
    GENOME_ASSEMBLY = "genome_assembly"
    OTHER = "other"


class ScoreValueKind(models.TextChoices):
    PROBABILITY = "probability"
    P_VALUE = "p_value"
    Q_VALUE = "q_value"
    RANK = "rank"
    COUNT = "count"
    CONTINUOUS = "continuous"


class ScoreDirection(models.TextChoices):
    HIGHER_IS_STRONGER = "higher_is_stronger"
    LOWER_IS_STRONGER = "lower_is_stronger"
    # The sign means something relative to the score type's null model.
    SIGNED = "signed"
    UNORDERED = "unordered"


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


# ── Foreign keys whose rows keep their rules from the other side ────────────
# A reverse many-to-one manager (release.approvals, dataset.releases) adds
# rows with one update() through the model's plain base manager, which no
# record rule sees (Record rules, below). Through a GuardedForeignKey, add()
# saves each row instead, so its rules apply, and set() goes through add().


def _saving_related_manager(base):
    class GuardedRelatedManager(base):
        def __call__(self, *, manager):
            plain = super().__call__(manager=manager)
            return _saving_related_manager(type(plain))(self.instance)

        def add(self, *objs, bulk=True):
            # Each row through save(). Django saves them inside a block
            # without a savepoint, where a refusal would leave the caller's
            # transaction unusable; this block has one.
            db = router.db_for_write(self.model, instance=self.instance)
            with transaction.atomic(using=db):
                return super().add(*objs, bulk=False)

        add.alters_data = True

        def set(self, objs, *, bulk=True, clear=False):
            db = router.db_for_write(self.model, instance=self.instance)
            with transaction.atomic(using=db):
                return super().set(objs, bulk=bulk, clear=clear)

        set.alters_data = True

    return GuardedRelatedManager


class _SavingReverseDescriptor(ReverseManyToOneDescriptor):
    @cached_property
    def related_manager_cls(self):
        return _saving_related_manager(
            create_reverse_many_to_one_manager(
                self.rel.related_model._default_manager.__class__, self.rel
            )
        )


class GuardedForeignKey(models.ForeignKey):
    """A ForeignKey whose reverse manager saves each row it adds. The column
    and the migrations are a plain ForeignKey's."""

    related_accessor_class = _SavingReverseDescriptor

    def deconstruct(self):
        name, _path, args, kwargs = super().deconstruct()
        return name, "django.db.models.ForeignKey", args, kwargs


# ── Entities ────────────────────────────────────────────────────────────────


# What an entity is: a retyped entity is a new entity and an audited merge
# (architecture §3.4), and the evidence rules read its type and taxon
# (endpoint types, synthetic separation). Each name and attname maps to its
# field.
FROZEN_ENTITY_FIELDS = {
    "entity_type": "entity_type",
    "entity_type_id": "entity_type",
    "identity_key": "identity_key",
    "taxon": "taxon",
    "taxon_id": "taxon",
}


def _frozen_entity_refusal(names, pk=None):
    """The refusal for a write that names a frozen field, or None."""
    frozen = sorted(
        {FROZEN_ENTITY_FIELDS[n] for n in names if n in FROZEN_ENTITY_FIELDS}
    )
    if not frozen:
        return None
    which = f"entity {pk}'s" if pk is not None else "an entity's"
    return _refusal(
        Entity,
        frozen[0],
        f"is what the entity is, set when it is created and never changed; "
        f"{which} type, identity key and taxon stay as written.",
    )


class EntityQuerySet(models.QuerySet):
    """Entity's default queryset: an update() never moves created_by_release
    off the release that introduced an entity, and never changes an entity's
    type, identity key or taxon (by name, whatever the value).
    Release.created_entities' remove() and clear(), taxon.entities' remove()
    and clear(), and a bulk_update() go through it."""

    def update(self, **kwargs):
        refusal = _frozen_entity_refusal(kwargs)
        if refusal is not None:
            raise refusal
        if not {"created_by_release", "created_by_release_id"} & kwargs.keys():
            return super().update(**kwargs)
        self._for_write = True
        with transaction.atomic(using=self.db):
            held = sorted(
                pk
                for pk, release_id in self.order_by("pk")
                .select_for_update()
                .values_list("pk", "created_by_release_id")
                if release_id is not None
            )
            if held:
                raise _refusal(Entity, "created_by_release", _introduced_rule(held[0]))
            return super().update(**kwargs)

    update.alters_data = True

    def bulk_update(self, objs, fields, batch_size=None):
        refusal = _frozen_entity_refusal(fields)
        if refusal is not None:
            raise refusal
        # As GuardedQuerySet.bulk_update: a savepoint, so that a refusal
        # leaves the caller's transaction usable.
        self._for_write = True
        with transaction.atomic(using=self.db):
            return super().bulk_update(objs, fields, batch_size=batch_size)

    bulk_update.alters_data = True

    def bulk_create(
        self,
        objs,
        batch_size=None,
        ignore_conflicts=False,
        update_conflicts=False,
        update_fields=None,
        unique_fields=None,
    ):
        # create_entity inserts with ignore_conflicts; an upsert would rewrite
        # what an existing entity is.
        if update_conflicts:
            raise _refusal(
                Entity,
                None,
                "bulk_create(update_conflicts=True) would rewrite stored rows.",
            )
        return super().bulk_create(
            objs,
            batch_size=batch_size,
            ignore_conflicts=ignore_conflicts,
            update_fields=update_fields,
            unique_fields=unique_fields,
        )

    bulk_create.alters_data = True


def _introduced_rule(pk):
    return (
        "names the release that introduced the entity, and is set once: from "
        f"NULL to a release, never from one; entity {pk} has one."
    )


class Entity(models.Model):
    """One typed node: a gene, a protein entry or a protein isoform, each its
    own entity even when they share a name.

    Write through graph.entities.create_entity, which enforces the rules
    that span tables (a gene needs a taxon; only the namespace that defines
    identity for the type and taxon creates one). Its type, identity key and
    taxon never change once written, on any path (FROZEN_ENTITY_FIELDS):
    entity_type.entities and taxon.entities save each row they add.
    """

    entity_type = GuardedForeignKey(
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
    taxon = GuardedForeignKey(
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
    # The release that introduced the entity: the first writer, kept when
    # other releases name it too. NULL for entities created without one. Set
    # once, on every write path (EntityQuerySet, save()), so PROTECT and the
    # release's frozen kind hold.
    created_by_release = GuardedForeignKey(
        "DatasetRelease",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="created_entities",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = EntityQuerySet.as_manager()

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

    def save(
        self, force_insert=False, force_update=False, using=None, update_fields=None
    ):
        if self.pk is None or force_insert:
            return super().save(
                force_insert=force_insert,
                force_update=force_update,
                using=using,
                update_fields=update_fields,
            )
        if update_fields is not None and not update_fields:
            return
        written = self._written_names(update_fields)
        using = using or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            # The stored row decides, read before anything is written.
            stored = (
                Entity._base_manager.using(using)
                .select_for_update()
                .filter(pk=self.pk)
                .first()
            )
            if stored is not None:
                self._check_frozen(stored, written)
                held = stored.created_by_release_id
                if (
                    "created_by_release" in written
                    and held is not None
                    and held != self.created_by_release_id
                ):
                    raise _refusal(
                        self, "created_by_release", _introduced_rule(self.pk)
                    )
            return super().save(
                force_insert=force_insert,
                force_update=force_update,
                using=using,
                update_fields=update_fields,
            )

    save.alters_data = True

    def _written_names(self, update_fields) -> set:
        """The fields an update writes, by name: update_fields, or the loaded
        ones (Django writes only those of a deferred instance)."""
        fields = [
            field for field in self._meta.concrete_fields if not field.primary_key
        ]
        if update_fields is not None:
            given = set(update_fields)
            return {
                field.name
                for field in fields
                if field.name in given or field.attname in given
            }
        deferred = self.get_deferred_fields()
        return {field.name for field in fields if field.attname not in deferred}

    def _check_frozen(self, stored, written):
        for name in sorted(set(FROZEN_ENTITY_FIELDS.values()) & written):
            field = self._meta.get_field(name)
            if _differs(
                field, getattr(self, field.attname), getattr(stored, field.attname)
            ):
                raise _frozen_entity_refusal([name], self.pk)


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


# ── Record rules ────────────────────────────────────────────────────────────
# The provenance and licensing rows below keep their rules on every write
# path, not only in the services that normally write them: save() and
# delete(), and the default manager's update(), delete() and bulk_create(),
# which bulk_update() and the many-to-many managers go through. Their foreign
# keys are GuardedForeignKeys, whose reverse managers add rows with save().
# Each model states its rules in its guard_* hooks. The base manager is never
# set in Meta, so it stays Django's plain one: save() itself writes through
# it, and so does the deletion collector (a user's SET_NULL, a draft
# release's cascade).


class ImmutableRecordError(Exception):
    """Nothing was written: the write breaks a rule of the record it touches.

    Raised by every refusal of the record rules. The message names the model,
    the field that decides, and the rule.
    """


# Django's URLField also accepts ftp and ftps. Links stored here are http(s)
# only (architecture §11), whatever the write path.
HTTP_URL = URLValidator(schemes=["http", "https"])
# Set while GuardedQuerySet.bulk_update() runs, which checked its links on the
# objects; the update() it issues receives them as expressions. A context
# variable, because bulk_update() updates a fresh queryset.
_BULK_URLS_CHECKED = contextvars.ContextVar("bulk_urls_checked", default=False)

# A URI scheme, as in "javascript:" (RFC 3986, section 3.1).
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")
# A path from the repository root, with an optional #anchor.
_REPOSITORY_PATH = re.compile(r"[A-Za-z0-9_.][A-Za-z0-9_./-]*(#[A-Za-z0-9_.-]+)?")
SHA256_HEX = re.compile(r"[0-9a-f]{64}")
# A release version is the source's own label, kept URL-safe: v1, 2024-06,
# v1+m2 (a re-mapping of v1's files, architecture §4.4).
RELEASE_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}")


def validate_notes_link(value):
    """An http(s) URL, or a path relative to the repository root such as
    docs/project/generalization/DATASET_DUE_DILIGENCE.md#trrust. Never another
    scheme (javascript:, file:, data:), an absolute path or a parent path."""
    if not value:
        return
    if _SCHEME.match(value):
        HTTP_URL(value)
        return
    path = value.split("#", 1)[0]
    if not _REPOSITORY_PATH.fullmatch(value) or ".." in path.split("/"):
        raise ValidationError(
            "Enter an http(s) URL or a path relative to the repository root.",
            code="invalid",
        )


def _refusal(model, field, rule) -> ImmutableRecordError:
    model = model if isinstance(model, type) else type(model)
    where = f"{model.__name__}.{field}" if field else model.__name__
    return ImmutableRecordError(f"{where}: {rule}")


def _check_url(model, name, value):
    if value in (None, ""):
        return
    try:
        HTTP_URL(value)
    except ValidationError:
        raise _refusal(
            model, name, f"only http and https URLs are stored, not {value!r}."
        ) from None


def _check_urls(instance, names):
    for field in instance._meta.concrete_fields:
        if field.name in names and isinstance(field, models.URLField):
            _check_url(type(instance), field.name, getattr(instance, field.attname))


def _as_stored(value):
    """A JSON value as PostgreSQL's jsonb gives it back, which keeps numbers
    as decimals: a float written with an exponent and at least 1 comes back
    as the integer its digits state (1e+16 as 10000000000000000), and -0.0 as
    0.0. Probed on PostgreSQL 16; every other number comes back as written.
    SQLite keeps the text json.dumps wrote, so _json_text compares in this
    form on PostgreSQL only. graph.claims stores qualifiers in this form on
    every backend, so the form read back is the form a claim gives, and
    recording one twice compares equal."""
    if isinstance(value, float):
        if value == 0:
            return 0.0
        written = repr(value)
        return int(Decimal(written)) if "e" in written and abs(value) >= 1 else value
    if isinstance(value, list):
        return [_as_stored(item) for item in value]
    if isinstance(value, dict):
        return {key: _as_stored(item) for key, item in value.items()}
    return value


def _unique_pairs(pairs):
    # Keys JSON writes alike ({True: .., "true": ..}) would collapse into one.
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("a JSON object with a repeated key")
    return dict(pairs)


def _json_text(field, value, jsonb=False) -> str:
    # Canonical JSON text: true, 1 and 1.0 stay distinct. On PostgreSQL in the
    # form jsonb keeps numbers (_as_stored: -0.0 as 0.0, 1e+16 as
    # 10000000000000000); SQLite keeps the text json.dumps wrote, so there the
    # comparison is exact. Keys that JSON writes alike raise ValueError, which
    # _differs counts as a change.
    plain = json.loads(
        json.dumps(value, cls=field.encoder), object_pairs_hook=_unique_pairs
    )
    return json.dumps(_as_stored(plain) if jsonb else plain, sort_keys=True)


def _differs(field, new, old, *, jsonb=False) -> bool:
    if isinstance(field, models.JSONField):
        # As canonical JSON text: Python finds True == 1 and 1.0 == 1, which
        # JSON stores as different values.
        try:
            return _json_text(field, new, jsonb) != _json_text(field, old, jsonb)
        except (TypeError, ValueError):
            return True
    if new == old:
        return False
    try:
        return field.to_python(new) != field.to_python(old)
    except ValidationError:
        return True


@lru_cache(maxsize=None)
def _actor_fields(model) -> tuple:
    """The model's foreign keys to the user that keep a <name>_username."""
    names = {field.name for field in model._meta.concrete_fields}
    return tuple(
        field.name
        for field in model._meta.concrete_fields
        if field.many_to_one
        and field.related_model._meta.label_lower == settings.AUTH_USER_MODEL.lower()
        and f"{field.name}_username" in names
    )


def _actor():
    """Who decided, recorded or triggered something. SET_NULL keeps the row
    when the account is deleted, and the <name>_username beside it keeps the
    name."""
    return models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )


def _locked_releases(using, release_ids) -> dict:
    """The releases, locked in id order: their state decides the write."""
    ids = sorted({pk for pk in release_ids if pk is not None})
    if not ids:
        return {}
    rows = (
        DatasetRelease._base_manager.using(using)
        .select_for_update()
        .filter(pk__in=ids)
        .order_by("pk")
        .only("status", "store_raw_rows", "is_synthetic", "dataset")
    )
    return {row.pk: row for row in rows}


def _require_releases(model, using, release_ids, statuses, rule) -> dict:
    releases = _locked_releases(using, release_ids)
    for release in releases.values():
        if release.status not in statuses:
            raise _refusal(
                model, "release", f"{rule}; release {release.pk} is {release.status}."
            )
    return releases


def _release_ids(queryset) -> list:
    return list(queryset.order_by().values_list("release_id", flat=True).distinct())


def _other_release(model, name, target, target_id, owner, release_id):
    return _refusal(
        model,
        name,
        f"belongs to the row's own release; {target.__name__} {target_id} "
        f"is in release {owner}, not {release_id}.",
    )


def _check_same_release(model, name, target, rows, using):
    """Refuse a row whose `name` key points at a row of another release."""
    ids = {getattr(row, f"{name}_id") for row in rows} - {None}
    owners = dict(
        target._base_manager.using(using)
        .filter(pk__in=ids)
        .values_list("pk", "release_id")
    )
    for row in rows:
        target_id = getattr(row, f"{name}_id")
        owner = owners.get(target_id)
        if owner is not None and owner != row.release_id:
            raise _other_release(model, name, target, target_id, owner, row.release_id)


def share_lock(model, using, keys):
    """Hold these registry rows in shared mode until the transaction ends.

    A relationship type's orientation, polarity, family, allowed types and
    self-pair rule, a score type's kind, direction and range, and an entity
    type's or namespace's rules decide what a write may store. A writer that
    validates against them takes this lock first, and graph_sync_registry
    locks every row it changes (FOR NO KEY UPDATE) before it counts the rows
    that use it, so whichever comes second sees the other's result. The
    sync's lock conflicts with this one but not with the key-share lock a
    foreign-key check takes at commit, so a row a writer only references (a
    taxon) never joins a cycle. Within one call, writers take shared locks in
    the sync's order (entity types, namespaces, relationship types, score
    types; each in key order), so neither waits on the other in a cycle. A
    caller that makes several such calls in one transaction (G4, G5) takes
    its shared locks in that order, or all of them before the first call.

    PostgreSQL only: Django's select_for_update() has no shared mode, and
    SQLite serializes writers anyway. Call it inside a transaction.
    """
    keys = sorted({key for key in keys if key is not None})
    connection = connections[using or router.db_for_write(model)]
    if not keys or connection.vendor != "postgresql":
        return
    quote = connection.ops.quote_name
    table, column = quote(model._meta.db_table), quote(model._meta.pk.column)
    placeholders = ", ".join(["%s"] * len(keys))
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT 1 FROM {table} WHERE {column} IN ({placeholders}) "
            f"ORDER BY {column} FOR SHARE",
            keys,
        )


class GuardedQuerySet(models.QuerySet):
    """The default manager's queryset for models with record rules.

    update(), delete() and bulk_create() ask the model's class hooks first,
    in one transaction; bulk_update() goes through update(). A hook refuses,
    or returns the rows it checked and locked, which are then the only rows
    written.
    """

    def update(self, **kwargs):
        self._for_write = True
        with transaction.atomic(using=self.db):
            names = set()
            for name, value in kwargs.items():
                field = self.model._meta.get_field(name)
                names.add(field.name)
                if isinstance(field, models.URLField):
                    if not hasattr(value, "resolve_expression"):
                        _check_url(self.model, field.name, value)
                    elif not _BULK_URLS_CHECKED.get():
                        raise _refusal(
                            self.model,
                            field.name,
                            "an update() sets a link only to a URL it can check.",
                        )
            rows = self.model.guard_queryset_update(self, frozenset(names))
            return super(GuardedQuerySet, rows).update(**kwargs)

    update.alters_data = True

    def bulk_update(self, objs, fields, batch_size=None):
        # Django's bulk_update() passes every value to update() as an
        # expression (CASE WHEN), so the links are checked here, on the
        # objects, before it does.
        objs = list(objs)
        for name in fields:
            field = self.model._meta.get_field(name)
            if not isinstance(field, models.URLField):
                continue
            for obj in objs:
                value = getattr(obj, field.attname)
                if hasattr(value, "resolve_expression"):
                    raise _refusal(
                        self.model,
                        field.name,
                        "an update() sets a link only to a URL it can check.",
                    )
                _check_url(self.model, field.name, value)
        # Django's bulk_update() calls update() inside a block without a
        # savepoint, where a refusal would leave the caller's transaction
        # unusable. This block has one, as every other path does.
        self._for_write = True
        token = _BULK_URLS_CHECKED.set(True)
        try:
            with transaction.atomic(using=self.db):
                return super().bulk_update(objs, fields, batch_size=batch_size)
        finally:
            _BULK_URLS_CHECKED.reset(token)

    bulk_update.alters_data = True

    def delete(self):
        self._for_write = True
        with transaction.atomic(using=self.db):
            rows = self.model.guard_queryset_delete(self)
            return super(GuardedQuerySet, rows).delete()

    delete.alters_data = True
    # As QuerySet.delete: the manager offers no delete() of every row.
    delete.queryset_only = True

    def bulk_create(
        self,
        objs,
        batch_size=None,
        ignore_conflicts=False,
        update_conflicts=False,
        update_fields=None,
        unique_fields=None,
    ):
        objs = list(objs)
        if update_conflicts:
            raise _refusal(
                self.model,
                None,
                "bulk_create(update_conflicts=True) would rewrite stored rows.",
            )
        self._for_write = True
        with transaction.atomic(using=self.db):
            for obj in objs:
                obj.prepare_insert()
            self.model.guard_bulk_create(objs, self.db)
            return super().bulk_create(
                objs,
                batch_size=batch_size,
                ignore_conflicts=ignore_conflicts,
                update_fields=update_fields,
                unique_fields=unique_fields,
            )

    bulk_create.alters_data = True


class GuardedModel(models.Model):
    """A model whose rows keep their rules on every write path (with its
    foreign keys GuardedForeignKeys, so that the reverse managers' add()
    saves each row).

    save() runs in one transaction. It locks the other rows its rules read
    (lock_for_write: a release before the rows that belong to it, the order
    the delete cascade takes), re-reads the stored row with
    select_for_update() through the base manager instead of trusting what the
    instance was loaded with, sets the derived fields, and asks guard_insert()
    or guard_update(). delete() re-reads the row by its pk the same way and
    asks guard_delete() of the stored row, so a pk-only instance, or one whose
    keys were changed in memory, is judged by what is stored; a pk that names
    no row deletes nothing. A hook refuses with ImmutableRecordError before
    anything is written. The defaults allow an insert and refuse every other
    write; each model allows what its rules allow. No guard trusts an
    instance's foreign keys or cached related rows beyond what the write
    itself stores.
    """

    objects = GuardedQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(
        self, force_insert=False, force_update=False, using=None, update_fields=None
    ):
        if update_fields is not None and not update_fields:
            return
        using = using or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            locked = self.lock_for_write(using, self.pk is None or force_insert)
            stored = None
            if self.pk is not None and not force_insert:
                stored = (
                    type(self)
                    ._base_manager.using(using)
                    .select_for_update()
                    .filter(pk=self.pk)
                    .first()
                )
            partial = update_fields is not None or (
                stored is not None and bool(self.get_deferred_fields())
            )
            written = self._written(stored, update_fields)
            self.derive(stored, written)
            _check_urls(self, written)
            if stored is None:
                changed = written
                self.guard_insert(using, locked)
            else:
                changed = self._changed(stored, written, using)
                if "created_at" in changed:
                    raise _refusal(self, "created_at", "never changes.")
                self.guard_update(stored, changed, using, locked)
            super().save(
                force_insert=force_insert,
                force_update=force_update,
                using=using,
                update_fields=written if partial else None,
            )
            self.after_save(stored, changed, using)

    save.alters_data = True

    def delete(self, using=None, keep_parents=False):
        using = using or router.db_for_write(type(self), instance=self)
        if self.pk is None:
            return super().delete(using=using, keep_parents=keep_parents)
        with transaction.atomic(using=using):
            stored = self.stored_for_delete(using)
            if stored is None:
                return 0, {self._meta.label: 0}
            stored.guard_delete(using)
            # The deletion collector deletes by pk alone.
            return super().delete(using=using, keep_parents=keep_parents)

    delete.alters_data = True

    def stored_for_delete(self, using):
        """The row as stored, locked, once the rows whose state decides its
        deletion are: what a delete is decided on. None when the pk names no
        row."""
        return (
            type(self)
            ._base_manager.using(using)
            .select_for_update()
            .filter(pk=self.pk)
            .first()
        )

    def prepare_insert(self):
        """What save() does before an insert's rules are asked, for
        bulk_create(): the derived fields and the link check."""
        names = frozenset(
            field.name for field in self._meta.concrete_fields if not field.primary_key
        )
        written = names | self.companions(names)
        self.derive(None, written)
        _check_urls(self, written)

    def _written(self, stored, update_fields) -> frozenset:
        """The fields this save writes, by name: all of them on an insert,
        update_fields (or the loaded ones) on a partial update, and the
        fields derived from those."""
        fields = [
            field for field in self._meta.concrete_fields if not field.primary_key
        ]
        if update_fields is not None:
            given = set(update_fields)
            known = {field.name for field in fields} | {
                field.attname for field in fields
            }
            if given - known:
                raise ValueError(
                    "The following fields do not exist in this model, are m2m "
                    "fields, or are non-concrete fields: "
                    + ", ".join(sorted(given - known))
                )
            names = {
                field.name
                for field in fields
                if field.name in given or field.attname in given
            }
        elif stored is not None:
            deferred = self.get_deferred_fields()
            names = {field.name for field in fields if field.attname not in deferred}
        else:
            names = {field.name for field in fields}
        return frozenset(names | self.companions(names))

    def _changed(self, stored, written, using) -> frozenset:
        """The written fields whose value differs from the stored row's,
        leaving out the auto_now bookkeeping. JSON is compared in the form
        the database keeps it: jsonb's on PostgreSQL, exactly elsewhere."""
        jsonb = connections[using].vendor == "postgresql"
        changed = set()
        for name in written:
            field = self._meta.get_field(name)
            if getattr(field, "auto_now", False):
                continue
            new = getattr(self, field.attname)
            if _differs(field, new, getattr(stored, field.attname), jsonb=jsonb):
                changed.add(name)
        return frozenset(changed)

    def _after(self, name, stored, written):
        """The value the row holds once this write is done."""
        attname = self._meta.get_field(name).attname
        if stored is None or name in written:
            return getattr(self, attname)
        return getattr(stored, attname)

    # ── Hooks ──

    def lock_for_write(self, using, inserting):
        """Lock the other rows whose state decides this write, before the
        row itself is read. `inserting`: the save has no pk or forces an
        insert; otherwise the stored row, if there is one, decides. Returns
        what the guards need of them."""
        return None

    def companions(self, names) -> set:
        """Fields written whenever these are, because derived from them."""
        return {
            f"{name}_username" for name in _actor_fields(type(self)) if name in names
        }

    def derive(self, stored, written):
        """Set the fields derived from others. By default, the username kept
        beside each actor that the write sets: taken from the account now,
        and kept when the account goes."""
        for name in _actor_fields(type(self)):
            if name not in written:
                continue
            user_id = getattr(self, f"{name}_id")
            if user_id is None or (
                stored is not None and getattr(stored, f"{name}_id") == user_id
            ):
                continue
            setattr(self, f"{name}_username", getattr(self, name).get_username())

    def guard_insert(self, using, locked):
        """Refuse an insert the rules forbid."""

    def guard_update(self, stored, changed, using, locked):
        """Refuse an update the rules forbid; `changed` names the fields
        whose stored value it changes."""
        if changed:
            raise _refusal(self, min(changed), "never changes once written.")

    def after_save(self, stored, changed, using):
        """Writes that go with this one, in its transaction."""

    def guard_delete(self, using):
        raise _refusal(self, None, f"row {self.pk} is never deleted.")

    @classmethod
    def guard_queryset_update(cls, queryset, names):
        raise _refusal(
            cls,
            min(names) if names else None,
            "a queryset update() is refused: save each row, so that its rules "
            "apply.",
        )

    @classmethod
    def guard_queryset_delete(cls, queryset):
        raise _refusal(cls, None, "rows are not deleted through a queryset.")

    @classmethod
    def guard_bulk_create(cls, objs, using):
        raise _refusal(
            cls,
            None,
            "bulk_create() is refused: save each row, so that its rules apply.",
        )


class Editable(GuardedModel):
    """Catalogue description: any field may change and rows may be deleted
    (PROTECT keeps the rows that others use). Only the shared rules apply."""

    class Meta:
        abstract = True

    def guard_update(self, stored, changed, using, locked):
        pass

    def guard_delete(self, using):
        pass

    @classmethod
    def guard_queryset_update(cls, queryset, names):
        return queryset

    @classmethod
    def guard_queryset_delete(cls, queryset):
        return queryset

    @classmethod
    def guard_bulk_create(cls, objs, using):
        pass


class ReleaseScoped(GuardedModel):
    """A row that belongs to one release, whose status decides its writes.

    Rows are deleted only while their release is a draft, so a release keeps
    what it was built from once it is staged; a draft release's deletion
    takes them with it (CASCADE).
    """

    class Meta:
        abstract = True

    def lock_for_write(self, using, inserting):
        # An update is decided by the stored row's release, never by one
        # given in memory; a row's release never changes.
        release_id = self.release_id
        if not inserting:
            release_id = self._stored_release_id(using) or release_id
        return _locked_releases(using, [release_id]).get(release_id)

    def stored_for_delete(self, using):
        # The stored release is locked before the row, the order the delete
        # cascade from the release takes.
        release_id = self._stored_release_id(using)
        if release_id is None:
            return None
        _locked_releases(using, [release_id])
        return super().stored_for_delete(using)

    def _stored_release_id(self, using):
        return (
            type(self)
            ._base_manager.using(using)
            .filter(pk=self.pk)
            .values_list("release_id", flat=True)
            .first()
        )

    def guard_delete(self, using):
        _require_releases(
            type(self),
            using,
            [self.release_id],
            {ReleaseStatus.DRAFT},
            "rows are deleted only while their release is a draft",
        )

    @classmethod
    def guard_queryset_delete(cls, queryset):
        ids = _release_ids(queryset)
        _require_releases(
            cls,
            queryset.db,
            ids,
            {ReleaseStatus.DRAFT},
            "rows are deleted only while their release is a draft",
        )
        return queryset.filter(release_id__in=ids)


# ── Catalogue: sources, datasets and publications ───────────────────────────


class DataSource(Editable):
    """Who publishes datasets: a lab, a consortium, a database, this portal."""

    slug = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=255)
    kind = models.CharField(max_length=16, choices=DataSourceKind.choices)
    url = models.URLField(max_length=500, blank=True, validators=[HTTP_URL])
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=Q(kind__in=DataSourceKind.values),
                name="graph_datasource_kind_valid",
            ),
        ]

    def __str__(self):
        return self.slug


class SourceDataset(Editable):
    """A study or resource as its publisher defines it; its releases carry
    the data. Named so to avoid the legacy datasets.Dataset."""

    slug = models.SlugField(max_length=100, unique=True)
    title = models.CharField(max_length=255)
    short_title = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)
    data_source = GuardedForeignKey(
        DataSource, on_delete=models.PROTECT, related_name="datasets"
    )
    homepage = models.URLField(max_length=500, blank=True, validators=[HTTP_URL])
    # Descriptive catalogue metadata that gates nothing: data leave view only
    # through an audited release withdrawal or an approval revocation.
    status = models.CharField(
        max_length=16, choices=DatasetStatus.choices, default=DatasetStatus.CANDIDATE
    )
    publications = models.ManyToManyField(
        "Publication",
        through="SourceDatasetPublication",
        blank=True,
        related_name="datasets",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=Q(status__in=DatasetStatus.values),
                name="graph_dataset_status_valid",
            ),
        ]

    def __str__(self):
        return self.slug


_NO_PUBLICATION_ID = Q(pmid="", doi="", pmcid="", imex_id="")


class Publication(GuardedModel):
    """One paper, whichever identifiers name it (graph.publications).

    A paper with none of the four identifiers is named by a local key, unique
    within its dataset, from the release manifest. Identifiers are stored
    normalized, and each is set once: blank to a value, as the co-referent
    merge fills it, never from one value to another. is_synthetic is fixed
    when the paper is created: evidence of synthetic releases cites only
    synthetic papers, and of the others none.
    """

    IDENTIFIER_FIELDS = ("pmid", "doi", "pmcid", "imex_id")
    SET_ONCE_FIELDS = frozenset({*IDENTIFIER_FIELDS, "local_key", "local_dataset"})
    CREATION_FIELDS = frozenset({"is_synthetic"})

    pmid = models.CharField(max_length=16, blank=True, default="")
    # As given, prefix removed.
    doi = models.CharField(max_length=255, blank=True, default="")
    # Always derived from doi: its ASCII letters lower-cased.
    doi_lookup = models.CharField(
        max_length=255, blank=True, default="", editable=False
    )
    pmcid = models.CharField(max_length=16, blank=True, default="")
    imex_id = models.CharField(max_length=32, blank=True, default="")
    title = models.TextField(blank=True)
    authors = models.TextField(blank=True)
    journal = models.CharField(max_length=255, blank=True, default="")
    year = models.PositiveSmallIntegerField(null=True, blank=True)
    url = models.URLField(max_length=500, blank=True, validators=[HTTP_URL])
    is_synthetic = models.BooleanField(default=False)
    local_key = models.CharField(max_length=100, blank=True, default="")
    local_dataset = GuardedForeignKey(
        SourceDataset,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="local_publications",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["pmid"], condition=~Q(pmid=""), name="graph_pub_pmid_unique"
            ),
            models.UniqueConstraint(
                fields=["doi_lookup"],
                condition=~Q(doi_lookup=""),
                name="graph_pub_doi_unique",
            ),
            models.UniqueConstraint(
                fields=["pmcid"], condition=~Q(pmcid=""), name="graph_pub_pmcid_unique"
            ),
            models.UniqueConstraint(
                fields=["imex_id"],
                condition=~Q(imex_id=""),
                name="graph_pub_imex_unique",
            ),
            models.UniqueConstraint(
                fields=["local_dataset", "local_key"],
                condition=~Q(local_key=""),
                name="graph_pub_local_key_unique",
            ),
            models.CheckConstraint(
                check=Q(local_key="", local_dataset__isnull=True)
                | (~Q(local_key="") & Q(local_dataset__isnull=False)),
                name="graph_pub_local_key_dataset",
            ),
            # A local key names a paper that has none of the four.
            models.CheckConstraint(
                check=Q(local_key="") | _NO_PUBLICATION_ID,
                name="graph_pub_local_key_alone",
            ),
            models.CheckConstraint(
                check=~(_NO_PUBLICATION_ID & Q(local_key="")),
                name="graph_pub_has_identifier",
            ),
            models.CheckConstraint(
                check=Q(doi="", doi_lookup="") | (~Q(doi="") & ~Q(doi_lookup="")),
                name="graph_pub_doi_and_lookup",
            ),
            models.CheckConstraint(
                check=Q(is_synthetic=False) | _NO_PUBLICATION_ID,
                name="graph_pub_synthetic_no_ids",
            ),
        ]

    def __str__(self):
        for name in self.IDENTIFIER_FIELDS:
            if getattr(self, name):
                return f"{name}:{getattr(self, name)}"
        return f"{self.local_dataset_id}:{self.local_key}"

    def companions(self, names):
        extra = super().companions(names)
        return extra | {"doi_lookup"} if {"doi", "doi_lookup"} & names else extra

    def derive(self, stored, written):
        super().derive(stored, written)
        if "doi_lookup" in written:
            # graph.publications imports this module, so it is imported here.
            from .publications import doi_lookup

            doi = self._after("doi", stored, written)
            self.doi_lookup = doi_lookup(doi) if isinstance(doi, str) else doi

    def guard_insert(self, using, locked):
        self._check_identifiers(self.SET_ONCE_FIELDS)

    def guard_update(self, stored, changed, using, locked):
        fixed = changed & self.CREATION_FIELDS
        if fixed:
            raise _refusal(
                self, min(fixed), "is fixed when the publication is created."
            )
        for name in sorted(changed & self.SET_ONCE_FIELDS):
            if getattr(stored, self._meta.get_field(name).attname) not in (
                "",
                None,
                False,
            ):
                raise _refusal(
                    self,
                    name,
                    "is set once: it changes from blank to a value (a co-referent "
                    "merge), never from one value.",
                )
        self._check_identifiers(changed)

    def guard_delete(self, using):
        pass

    @classmethod
    def guard_queryset_delete(cls, queryset):
        return queryset

    @classmethod
    def guard_bulk_create(cls, objs, using):
        for publication in objs:
            publication._check_identifiers(cls.SET_ONCE_FIELDS)

    def _check_identifiers(self, names):
        from .publications import IDENTIFIER_NORMALIZERS, PublicationError

        for name, normalize in IDENTIFIER_NORMALIZERS.items():
            value = getattr(self, name)
            if name not in names or value in ("", None):
                continue
            try:
                normalized = normalize(value)
            except PublicationError as error:
                raise _refusal(self, name, str(error)) from None
            if normalized != value:
                raise _refusal(
                    self, name, f"is stored normalized: {value!r} is {normalized!r}."
                )
        key = self.local_key
        if "local_key" in names and (not isinstance(key, str) or key != key.strip()):
            raise _refusal(
                self, "local_key", f"is stored without surrounding spaces, not {key!r}."
            )


class SourceDatasetPublication(models.Model):
    """A paper that describes a dataset, with its role. Descriptive: a link
    may be added at any time, an erratum included."""

    dataset = models.ForeignKey(
        SourceDataset, on_delete=models.CASCADE, related_name="publication_links"
    )
    publication = models.ForeignKey(
        Publication, on_delete=models.PROTECT, related_name="dataset_links"
    )
    role = models.CharField(max_length=16, choices=PublicationRole.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["dataset", "publication", "role"],
                name="graph_dataset_pub_unique",
            ),
            models.CheckConstraint(
                check=Q(role__in=PublicationRole.values),
                name="graph_dataset_pub_role_valid",
            ),
        ]

    def __str__(self):
        return f"{self.dataset_id} {self.role} {self.publication_id}"


# ── Releases ────────────────────────────────────────────────────────────────


class DatasetRelease(GuardedModel):
    """One versioned snapshot of a dataset: the unit of import, licensing,
    visibility and withdrawal.

    What the release is (its dataset, version, adapter, files and declared
    taxa) is fixed once it leaves draft, and everything else once it is
    published. After that only its status moves on, by TRANSITIONS, with who
    published or withdrew it set once. graph.releases publishes and
    withdraws, and applies the rules that span releases: a save moves a
    release from staged to published, or from published to superseded, only
    with `_lifecycle` set, as graph.releases.publish_release sets it (cleared
    after each save).
    """

    TRANSITIONS = frozenset(
        {
            (ReleaseStatus.DRAFT, ReleaseStatus.STAGED),
            # A discard returns a staged release to draft (G5).
            (ReleaseStatus.STAGED, ReleaseStatus.DRAFT),
            (ReleaseStatus.STAGED, ReleaseStatus.PUBLISHED),
            (ReleaseStatus.PUBLISHED, ReleaseStatus.SUPERSEDED),
            (ReleaseStatus.STAGED, ReleaseStatus.WITHDRAWN),
            (ReleaseStatus.PUBLISHED, ReleaseStatus.WITHDRAWN),
            (ReleaseStatus.SUPERSEDED, ReleaseStatus.WITHDRAWN),
        }
    )
    # Their rules span releases (an internal approval; a correction never
    # hides public data, nor stands beside the release it corrects), which
    # only publish_release checks.
    PUBLISHING_TRANSITIONS = frozenset(
        {
            (ReleaseStatus.STAGED, ReleaseStatus.PUBLISHED),
            (ReleaseStatus.PUBLISHED, ReleaseStatus.SUPERSEDED),
        }
    )
    # Fixed when the release is created: flipping is_synthetic would skip
    # due diligence.
    CREATION_FIELDS = frozenset({"dataset", "is_synthetic"})
    # Fixed once the release is not a draft (declared taxa too: ReleaseTaxon).
    DRAFT_FIELDS = frozenset(
        {
            "version",
            "supersedes",
            "adapter",
            "adapter_version",
            "mapping_version",
            "store_raw_rows",
            "release_date",
            "retrieved_at",
            "source_url",
        }
    )
    # Fixed once it is published, superseded or withdrawn. An embargo lapses
    # at its date; a release that must be hidden is withdrawn.
    UNPUBLISHED_FIELDS = frozenset({"stats", "embargo_until"})
    PUBLISHED_FIELDS = frozenset(
        {"published_at", "published_by", "published_by_username"}
    )
    WITHDRAWN_FIELDS = frozenset(
        {"withdrawn_at", "withdrawn_reason", "withdrawn_by", "withdrawn_by_username"}
    )

    dataset = GuardedForeignKey(
        SourceDataset, on_delete=models.PROTECT, related_name="releases"
    )
    version = models.CharField(max_length=64)
    release_date = models.DateField(null=True, blank=True)
    retrieved_at = models.DateTimeField(null=True, blank=True)
    source_url = models.URLField(max_length=1000, blank=True, validators=[HTTP_URL])
    # Conservative: the entities an evidence release creates are provisional.
    # Editable while a draft that no entity names; fixed after.
    kind = models.CharField(
        max_length=16, choices=ReleaseKind.choices, default=ReleaseKind.EVIDENCE
    )
    status = models.CharField(
        max_length=16, choices=ReleaseStatus.choices, default=ReleaseStatus.DRAFT
    )
    # The release this one corrects. At most one release that was published
    # holds the place (graph_release_supersedes_once); drafts, staged
    # releases and those withdrawn before publication never do.
    supersedes = GuardedForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="corrections",
    )
    is_synthetic = models.BooleanField(default=False)
    embargo_until = models.DateTimeField(null=True, blank=True)
    declared_taxa = models.ManyToManyField(
        Taxon, through="ReleaseTaxon", blank=True, related_name="releases"
    )
    adapter = models.CharField(max_length=64, blank=True)
    adapter_version = models.CharField(max_length=32, blank=True)
    mapping_version = models.CharField(max_length=32, blank=True)
    store_raw_rows = models.BooleanField(default=True)
    # Written at commit (G5).
    stats = models.JSONField(default=dict)
    published_at = models.DateTimeField(null=True, blank=True)
    published_by = _actor()
    published_by_username = models.CharField(max_length=150, blank=True)
    withdrawn_at = models.DateTimeField(null=True, blank=True)
    withdrawn_reason = models.TextField(blank=True)
    withdrawn_by = _actor()
    withdrawn_by_username = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["dataset", "version"], name="graph_release_dataset_version"
            ),
            models.CheckConstraint(
                check=Q(status__in=ReleaseStatus.values),
                name="graph_release_status_valid",
            ),
            models.CheckConstraint(
                check=Q(kind__in=ReleaseKind.values), name="graph_release_kind_valid"
            ),
            models.CheckConstraint(
                check=~Q(supersedes=F("pk")), name="graph_release_not_self"
            ),
            models.CheckConstraint(
                check=(
                    Q(status=ReleaseStatus.WITHDRAWN, withdrawn_at__isnull=False)
                    & ~Q(withdrawn_reason="")
                    & ~Q(withdrawn_by_username="")
                )
                | (
                    ~Q(status=ReleaseStatus.WITHDRAWN)
                    & Q(
                        withdrawn_at__isnull=True,
                        withdrawn_reason="",
                        withdrawn_by_username="",
                    )
                ),
                name="graph_release_withdrawn_fields",
            ),
            models.CheckConstraint(
                check=~Q(status__in=[ReleaseStatus.PUBLISHED, ReleaseStatus.SUPERSEDED])
                | (Q(published_at__isnull=False) & ~Q(published_by_username="")),
                name="graph_release_published_fields",
            ),
            models.CheckConstraint(
                check=~Q(status__in=[ReleaseStatus.DRAFT, ReleaseStatus.STAGED])
                | Q(published_at__isnull=True),
                name="graph_release_unpublished_null",
            ),
            models.UniqueConstraint(
                fields=["supersedes"],
                condition=Q(published_at__isnull=False),
                name="graph_release_supersedes_once",
            ),
        ]

    _lifecycle = False

    def __str__(self):
        return f"{self.dataset_id}/{self.version}"

    def save(
        self, force_insert=False, force_update=False, using=None, update_fields=None
    ):
        try:
            super().save(
                force_insert=force_insert,
                force_update=force_update,
                using=using,
                update_fields=update_fields,
            )
        finally:
            self._lifecycle = False

    save.alters_data = True

    def guard_insert(self, using, locked):
        self._check_version()
        target = self._check_supersedes(using)
        # Never two published releases where one corrects the other.
        if (
            target is not None
            and self.status == ReleaseStatus.PUBLISHED
            and target["status"]
            not in (ReleaseStatus.SUPERSEDED, ReleaseStatus.WITHDRAWN)
        ):
            raise _refusal(
                self,
                "supersedes",
                "a release inserted as published corrects one already superseded "
                f"or withdrawn; release {self.supersedes_id} is {target['status']}.",
            )

    def guard_update(self, stored, changed, using, locked):
        old = stored.status
        new = self.status if "status" in changed else old
        fixed = changed & self.CREATION_FIELDS
        if fixed:
            raise _refusal(self, min(fixed), "is fixed when the release is created.")
        if "status" in changed and (old, new) not in self.TRANSITIONS:
            raise _refusal(
                self, "status", f"a release does not go from {old} to {new}."
            )
        if (
            "status" in changed
            and (old, new) in self.PUBLISHING_TRANSITIONS
            and not self._lifecycle
        ):
            raise _refusal(
                self,
                "status",
                f"goes from {old} to {new} only through "
                "graph.releases.publish_release, which applies the rules that span "
                "releases.",
            )
        if "kind" in changed:
            if old != ReleaseStatus.DRAFT:
                raise _refusal(
                    self,
                    "kind",
                    f"changes only while the release is a draft; release {self.pk} "
                    f"is {old}.",
                )
            if (
                Entity._base_manager.using(using)
                .filter(created_by_release=self)
                .exists()
            ):
                raise _refusal(
                    self,
                    "kind",
                    "is fixed once an entity names the release as the one that "
                    "created it.",
                )
        drafted = changed & self.DRAFT_FIELDS
        if drafted and old != ReleaseStatus.DRAFT:
            raise _refusal(
                self,
                min(drafted),
                f"changes only while the release is a draft; release {self.pk} is {old}.",
            )
        unpublished = changed & self.UNPUBLISHED_FIELDS
        if unpublished and old not in (ReleaseStatus.DRAFT, ReleaseStatus.STAGED):
            raise _refusal(
                self,
                min(unpublished),
                f"is fixed once the release is published; release {self.pk} is {old}.",
            )
        published = changed & self.PUBLISHED_FIELDS
        if published and (old, new) != (ReleaseStatus.STAGED, ReleaseStatus.PUBLISHED):
            raise _refusal(
                self, min(published), "is set once, when a staged release is published."
            )
        withdrawn = changed & self.WITHDRAWN_FIELDS
        if withdrawn and (
            new != ReleaseStatus.WITHDRAWN or old == ReleaseStatus.WITHDRAWN
        ):
            raise _refusal(
                self, min(withdrawn), "is set once, when the release is withdrawn."
            )
        if "version" in changed:
            self._check_version()
        if "supersedes" in changed:
            self._check_supersedes(using)

    def guard_delete(self, using):
        status = (
            DatasetRelease._base_manager.using(using)
            .select_for_update()
            .filter(pk=self.pk)
            .values_list("status", flat=True)
            .first()
        )
        if status is not None and status != ReleaseStatus.DRAFT:
            raise _refusal(
                self,
                "status",
                f"a release is deleted only while it is a draft; release {self.pk} "
                f"is {status}.",
            )

    @classmethod
    def guard_queryset_delete(cls, queryset):
        releases = _locked_releases(queryset.db, queryset.values_list("pk", flat=True))
        for release in releases.values():
            if release.status != ReleaseStatus.DRAFT:
                raise _refusal(
                    cls,
                    "status",
                    "a release is deleted only while it is a draft; release "
                    f"{release.pk} is {release.status}.",
                )
        return queryset.filter(pk__in=list(releases))

    def _check_version(self):
        version = self.version
        if not isinstance(version, str) or not RELEASE_VERSION.fullmatch(version):
            raise _refusal(
                self,
                "version",
                "is the source's label, URL-safe: a letter or digit, then up to 63 "
                f"letters, digits or . _ + -; not {version!r}.",
            )

    def _check_supersedes(self, using):
        """The release this one corrects, if any, as a dict of the fields the
        rules read: same dataset, same synthetic flag, never itself."""
        if self.supersedes_id is None:
            return None
        if self.supersedes_id == self.pk:
            raise _refusal(self, "supersedes", "a release never supersedes itself.")
        target = (
            DatasetRelease._base_manager.using(using)
            .filter(pk=self.supersedes_id)
            .values("dataset_id", "is_synthetic", "status")
            .first()
        )
        if target is None:
            return None
        if target["dataset_id"] != self.dataset_id:
            raise _refusal(
                self,
                "supersedes",
                "a correction belongs to the dataset of the release it corrects; "
                f"release {self.supersedes_id} is in dataset {target['dataset_id']}, "
                f"not {self.dataset_id}.",
            )
        if target["is_synthetic"] != self.is_synthetic:
            raise _refusal(
                self,
                "supersedes",
                "a correction keeps the synthetic flag of the release it corrects; "
                f"release {self.supersedes_id} has is_synthetic="
                f"{target['is_synthetic']}.",
            )
        return target


class ReleasePublication(models.Model):
    """A paper that describes a release, with its role. Descriptive: a link
    may be added after publication, an erratum included."""

    release = models.ForeignKey(
        DatasetRelease, on_delete=models.CASCADE, related_name="publication_links"
    )
    publication = models.ForeignKey(
        Publication, on_delete=models.PROTECT, related_name="release_links"
    )
    role = models.CharField(max_length=16, choices=PublicationRole.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["release", "publication", "role"],
                name="graph_release_pub_unique",
            ),
            models.CheckConstraint(
                check=Q(role__in=PublicationRole.values),
                name="graph_release_pub_role_valid",
            ),
        ]

    def __str__(self):
        return f"{self.release_id} {self.role} {self.publication_id}"


class ReleaseTaxon(ReleaseScoped):
    """A taxon a release declares: the explicit through model of
    DatasetRelease.declared_taxa. The many-to-many managers of both sides
    (release.declared_taxa, taxon.releases) add with bulk_create() and remove
    and clear with a queryset delete() on this model's default manager, so
    every path meets the same rule: declared taxa change only while the
    release is a draft. Those managers call it inside a block of their own
    without a savepoint, so a refusal there leaves the caller's transaction
    marked for rollback, as any error inside them does."""

    RULE = "a release's declared taxa change only while it is a draft"

    release = GuardedForeignKey(
        DatasetRelease, on_delete=models.CASCADE, related_name="release_taxa"
    )
    # PROTECT: a declared taxon is never deleted from the registry.
    taxon = GuardedForeignKey(
        Taxon, on_delete=models.PROTECT, related_name="release_taxa"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["release", "taxon"], name="graph_release_taxon_unique"
            ),
        ]

    def __str__(self):
        return f"{self.release_id} {self.taxon_id}"

    def guard_insert(self, using, release):
        if release is not None and release.status != ReleaseStatus.DRAFT:
            raise _refusal(
                self,
                "release",
                f"{self.RULE}; release {release.pk} is {release.status}.",
            )

    def guard_update(self, stored, changed, using, release):
        raise _refusal(
            self,
            min(changed) if changed else "taxon",
            "a declared taxon is added or removed, never changed.",
        )

    @classmethod
    def guard_bulk_create(cls, objs, using):
        _require_releases(
            cls,
            using,
            [row.release_id for row in objs],
            {ReleaseStatus.DRAFT},
            cls.RULE,
        )


# ── Files, import runs and source records ───────────────────────────────────


class SourceFile(ReleaseScoped):
    """A file of a release, with its checksum. Added only while the release
    is a draft that no one has decided on, so that its content is fixed
    before anyone approves it; never changed."""

    release = GuardedForeignKey(
        DatasetRelease, on_delete=models.CASCADE, related_name="files"
    )
    role = models.CharField(max_length=16, choices=FileRole.choices)
    original_name = models.CharField(max_length=255)
    # Relative to the private root G4 adds (GRAPH_FILES_ROOT), never served.
    storage_path = models.CharField(max_length=500)
    media_type = models.CharField(max_length=100, blank=True)
    size = models.PositiveBigIntegerField()
    # Lower-case hexadecimal.
    sha256 = models.CharField(max_length=64)
    retrieved_from = models.URLField(max_length=1000, blank=True, validators=[HTTP_URL])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["release", "sha256"], name="graph_file_release_sha256"
            ),
            models.CheckConstraint(
                check=Q(role__in=FileRole.values), name="graph_file_role_valid"
            ),
        ]

    def __str__(self):
        return self.original_name

    def guard_insert(self, using, release):
        SourceFile._check_inserts(
            [self], using, {release.pk: release} if release else {}
        )

    def guard_update(self, stored, changed, using, release):
        raise _refusal(
            self,
            min(changed) if changed else "sha256",
            "a source file is registered once and never updated.",
        )

    @classmethod
    def guard_bulk_create(cls, objs, using):
        releases = _locked_releases(using, [file.release_id for file in objs])
        cls._check_inserts(objs, using, releases)

    @classmethod
    def _check_inserts(cls, files, using, releases):
        for file in files:
            if not isinstance(file.sha256, str) or not SHA256_HEX.fullmatch(
                file.sha256
            ):
                raise _refusal(
                    cls,
                    "sha256",
                    f"is 64 lower-case hexadecimal digits, not {file.sha256!r}.",
                )
        for release in releases.values():
            if release.status != ReleaseStatus.DRAFT:
                raise _refusal(
                    cls,
                    "release",
                    "files are added only while their release is a draft; release "
                    f"{release.pk} is {release.status}.",
                )
        decided = sorted(
            UsageApproval._base_manager.using(using)
            .filter(release_id__in=list(releases))
            .values_list("release_id", flat=True)
            .distinct()
        )
        if decided:
            raise _refusal(
                cls,
                "release",
                "a release's files are fixed before anyone decides on it; release "
                f"{decided[0]} has an approval decision.",
            )


class ImportRun(ReleaseScoped):
    """One attempt to import a release: a dry run, or a commit that G5
    resumes from its checkpoint. Its outcome changes as it runs; what ran, on
    what, and who started it never do."""

    MUTABLE_FIELDS = frozenset(
        {"state", "parameters", "checkpoint", "report", "error", "finished_at"}
    )
    _FIXED = (
        "is fixed when the run starts; only its state, parameters, checkpoint, "
        "report, error and finished_at change."
    )

    release = GuardedForeignKey(
        DatasetRelease, on_delete=models.CASCADE, related_name="import_runs"
    )
    # RESTRICT: a file a run read goes only with its whole draft release.
    file = GuardedForeignKey(
        SourceFile,
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="import_runs",
    )
    adapter = models.CharField(max_length=64)
    adapter_version = models.CharField(max_length=32)
    mapping_version = models.CharField(max_length=32, blank=True)
    mode = models.CharField(max_length=16, choices=ImportMode.choices)
    state = models.CharField(
        max_length=16, choices=ImportState.choices, default=ImportState.RUNNING
    )
    parameters = models.JSONField(default=dict)
    checkpoint = models.JSONField(default=dict)
    report = models.JSONField(default=dict)
    error = models.TextField(blank=True)
    triggered_by = _actor()
    triggered_by_username = models.CharField(max_length=150, blank=True)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=Q(mode__in=ImportMode.values), name="graph_run_mode_valid"
            ),
            models.CheckConstraint(
                check=Q(state__in=ImportState.values), name="graph_run_state_valid"
            ),
            # At most one open (resumable) commit run per release: G5 resumes
            # it, and a discard marks it abandoned.
            models.UniqueConstraint(
                fields=["release"],
                condition=Q(
                    mode=ImportMode.COMMIT,
                    state__in=[ImportState.RUNNING, ImportState.FAILED],
                ),
                name="graph_run_one_open_commit",
            ),
        ]

    def __str__(self):
        return f"{self.mode} run {self.pk} of release {self.release_id}"

    def guard_insert(self, using, release):
        ImportRun._check_inserts(
            [self], using, {release.pk: release} if release else {}
        )

    def guard_update(self, stored, changed, using, release):
        fixed = changed - self.MUTABLE_FIELDS
        if fixed:
            raise _refusal(self, min(fixed), self._FIXED)

    @classmethod
    def guard_queryset_update(cls, queryset, names):
        fixed = names - cls.MUTABLE_FIELDS
        if fixed:
            raise _refusal(cls, min(fixed), cls._FIXED)
        return queryset

    @classmethod
    def guard_bulk_create(cls, objs, using):
        releases = _locked_releases(using, [run.release_id for run in objs])
        cls._check_inserts(objs, using, releases)

    @classmethod
    def _check_inserts(cls, runs, using, releases):
        _check_same_release(cls, "file", SourceFile, runs, using)
        for run in runs:
            release = releases.get(run.release_id)
            if (
                run.mode == ImportMode.COMMIT
                and release is not None
                and release.status != ReleaseStatus.DRAFT
            ):
                raise _refusal(
                    cls,
                    "mode",
                    "a commit run starts only while its release is a draft; release "
                    f"{release.pk} is {release.status}.",
                )


class SourceRecord(ReleaseScoped):
    """One row of a release file, kept as read, with a stable key. Its
    content never changes; its status and issues follow the latest run while
    the release is a draft or staged (each run's report keeps its own copy).
    A record that evidence was recorded from stays mapped: its assertions
    rest on that mapping.
    """

    MUTABLE_FIELDS = frozenset({"status", "issues"})
    WRITABLE = frozenset({ReleaseStatus.DRAFT, ReleaseStatus.STAGED})
    _WRITABLE_RULE = "records are written only while their release is a draft or staged"
    _CONTENT = (
        "is the record as read and never changes; only its status and issues "
        "follow the latest run."
    )
    _EVIDENCE_RULE = (
        "stays mapped once evidence is recorded from the record; record {pk} "
        "has assertions."
    )

    release = GuardedForeignKey(
        DatasetRelease, on_delete=models.CASCADE, related_name="records"
    )
    # RESTRICT on file and run: a draft release's deletion takes its files,
    # runs and records together, but a file or run that records point at is
    # never deleted alone.
    file = GuardedForeignKey(
        SourceFile, on_delete=models.RESTRICT, related_name="records"
    )
    # The run that first wrote the record.
    import_run = GuardedForeignKey(
        ImportRun, on_delete=models.RESTRICT, related_name="records"
    )
    record_key = models.CharField(max_length=255)
    record_key_basis = models.CharField(max_length=16, choices=RecordKeyBasis.choices)
    line_number = models.PositiveIntegerField()
    # The verbatim column map; NULL when the release does not store raw rows,
    # with the file, line and hash kept.
    raw = models.JSONField(null=True, blank=True)
    raw_sha256 = models.CharField(max_length=64)
    status = models.CharField(max_length=16, choices=RecordStatus.choices)
    issues = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["release", "record_key"], name="graph_record_release_key"
            ),
            models.CheckConstraint(
                check=Q(record_key_basis__in=RecordKeyBasis.values),
                name="graph_record_basis_valid",
            ),
            models.CheckConstraint(
                check=Q(status__in=RecordStatus.values),
                name="graph_record_status_valid",
            ),
            models.CheckConstraint(
                check=Q(line_number__gt=0), name="graph_record_line_positive"
            ),
        ]
        indexes = [
            models.Index(
                fields=["release", "status"], name="graph_record_release_status"
            ),
        ]

    def __str__(self):
        return self.record_key

    def guard_insert(self, using, release):
        SourceRecord._check_inserts(
            [self], using, {release.pk: release} if release else {}
        )

    def guard_update(self, stored, changed, using, release):
        content = changed - self.MUTABLE_FIELDS
        if content:
            raise _refusal(self, min(content), self._CONTENT)
        # Any save writes updated_at, so none is made once the release is
        # published, superseded or withdrawn.
        if release is not None and release.status not in self.WRITABLE:
            raise _refusal(
                self,
                min(changed) if changed else "status",
                f"{self._WRITABLE_RULE}; release {release.pk} is {release.status}.",
            )
        if (
            "status" in changed
            and self.status != RecordStatus.MAPPED
            and EvidenceAssertion._base_manager.using(using)
            .filter(source_record_id=self.pk)
            .exists()
        ):
            raise _refusal(self, "status", self._EVIDENCE_RULE.format(pk=self.pk))

    @classmethod
    def guard_queryset_update(cls, queryset, names):
        content = names - cls.MUTABLE_FIELDS - {"updated_at"}
        if content:
            raise _refusal(cls, min(content), cls._CONTENT)
        ids = _release_ids(queryset)
        _require_releases(cls, queryset.db, ids, cls.WRITABLE, cls._WRITABLE_RULE)
        if "status" in names:
            # The hook sees names, not values: any status update of a record
            # with evidence is refused.
            with_evidence = (
                EvidenceAssertion._base_manager.using(queryset.db)
                .filter(source_record__in=queryset.order_by().values("pk"))
                .order_by("source_record_id")
                .values_list("source_record_id", flat=True)
                .first()
            )
            if with_evidence is not None:
                raise _refusal(
                    cls, "status", cls._EVIDENCE_RULE.format(pk=with_evidence)
                )
        return queryset.filter(release_id__in=ids)

    @classmethod
    def guard_bulk_create(cls, objs, using):
        releases = _locked_releases(using, [record.release_id for record in objs])
        cls._check_inserts(objs, using, releases)

    @classmethod
    def _check_inserts(cls, records, using, releases):
        for release in releases.values():
            if release.status not in cls.WRITABLE:
                raise _refusal(
                    cls,
                    "release",
                    f"{cls._WRITABLE_RULE}; release {release.pk} is {release.status}.",
                )
        _check_same_release(cls, "file", SourceFile, records, using)
        _check_same_release(cls, "import_run", ImportRun, records, using)
        for record in records:
            release = releases.get(record.release_id)
            if (
                release is not None
                and not release.store_raw_rows
                and record.raw is not None
            ):
                raise _refusal(
                    cls,
                    "raw",
                    f"is NULL when the release does not store raw rows; release "
                    f"{release.pk} keeps only the file, line and hash.",
                )
            digest = record.raw_sha256
            if not isinstance(digest, str) or not SHA256_HEX.fullmatch(digest):
                raise _refusal(
                    cls,
                    "raw_sha256",
                    f"is 64 lower-case hexadecimal digits, not {digest!r}.",
                )


# ── Licensing: due diligence, licence snapshots and approvals ───────────────

# graph_dd_complete_reviewed's message, for the services that refuse with it.
DUE_DILIGENCE_COMPLETE_RULE = (
    "Due diligence is complete only with a reviewer and a link to the reviewed "
    "notes."
)


class DueDiligenceRecord(GuardedModel):
    """A dataset's due diligence: one record, which may exist before any
    release. Approvals of the dataset's non-synthetic releases rely on it
    (graph.approvals).

    Its creation, and every change to what it says (AUDITED_FIELDS), writes
    one DueDiligenceChange in the same transaction, whatever the write path.
    Such a write names its actor: set `_changed_by` (an active staff user)
    and optionally `_change_note` on the instance before save(); without one
    the save is refused. An edit of `notes` alone needs neither.
    `completed_at` belongs to the model: set when the status becomes
    complete, cleared when it leaves complete, and set again by a save with
    `_reconfirm` set (licensing.confirm_due_diligence: due diligence
    confirmed to cover newly recorded terms). The three attributes are
    cleared after each save.
    """

    AUDITED_FIELDS = (
        "status",
        "terms_time_limited",
        "reviewer",
        "reviewed_on",
        "notes_link",
        "completed_at",
    )

    dataset = models.OneToOneField(
        SourceDataset, on_delete=models.PROTECT, related_name="due_diligence"
    )
    status = models.CharField(
        max_length=16,
        choices=DueDiligenceStatus.choices,
        default=DueDiligenceStatus.OPEN,
    )
    # Who reviewed; they need not have an account.
    reviewer = models.CharField(max_length=255, blank=True)
    reviewed_on = models.DateField(null=True, blank=True)
    # The reviewed notes that answer the brief's §7 checklist.
    notes_link = models.CharField(
        max_length=1000, blank=True, validators=[validate_notes_link]
    )
    notes = models.TextField(blank=True)
    # Approval expiry is deferred: time-limited terms block approval instead.
    terms_time_limited = models.BooleanField(default=False)
    # When due diligence was last confirmed complete.
    completed_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    _changed_by = None
    _change_note = ""
    _reconfirm = False

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=Q(status__in=DueDiligenceStatus.values),
                name="graph_dd_status_valid",
            ),
            models.CheckConstraint(
                check=~Q(status=DueDiligenceStatus.COMPLETE)
                | (~Q(reviewer="") & ~Q(notes_link="")),
                name="graph_dd_complete_reviewed",
                violation_error_message=DUE_DILIGENCE_COMPLETE_RULE,
            ),
            models.CheckConstraint(
                check=Q(status=DueDiligenceStatus.COMPLETE, completed_at__isnull=False)
                | (
                    ~Q(status=DueDiligenceStatus.COMPLETE)
                    & Q(completed_at__isnull=True)
                ),
                name="graph_dd_completed_at",
            ),
        ]

    def __str__(self):
        return f"due diligence of {self.dataset_id} ({self.status})"

    def companions(self, names):
        extra = super().companions(names)
        if "status" in names or self._reconfirm:
            extra = extra | {"completed_at"}
        return extra

    def derive(self, stored, written):
        super().derive(stored, written)
        if "completed_at" not in written:
            return
        status = self._after("status", stored, written)
        if status != DueDiligenceStatus.COMPLETE:
            if self._reconfirm:
                raise _refusal(
                    self,
                    "completed_at",
                    "due diligence is confirmed only on a complete record.",
                )
            self.completed_at = None
        elif (
            stored is None
            or stored.status != DueDiligenceStatus.COMPLETE
            or self._reconfirm
        ):
            self.completed_at = timezone.now()
        else:
            self.completed_at = stored.completed_at

    def lock_for_write(self, using, inserting):
        # A record created complete is confirmed when it is written. Locking
        # the dataset first serializes the creation with
        # licensing.record_licence, which locks it too, so that confirmation
        # never predates a snapshot it could not see.
        if inserting:
            list(
                SourceDataset._base_manager.using(using)
                .select_for_update()
                .filter(pk=self.dataset_id)
                .values_list("pk", flat=True)
            )
        return None

    def guard_insert(self, using, locked):
        self._check_notes_link()
        self._check_actor("status")

    def guard_update(self, stored, changed, using, locked):
        if "dataset" in changed:
            raise _refusal(
                self, "dataset", "a due-diligence record stays with its dataset."
            )
        if "notes_link" in changed:
            self._check_notes_link()
        audited = changed & set(self.AUDITED_FIELDS)
        if audited:
            self._check_actor(min(audited))

    def after_save(self, stored, changed, using):
        if stored is None or changed & set(self.AUDITED_FIELDS):
            values = (
                DueDiligenceRecord._base_manager.using(using)
                .filter(pk=self.pk)
                .values(*self.AUDITED_FIELDS)
                .get()
            )
            DueDiligenceChange(
                record=self,
                changed_by=self._changed_by,
                note=self._change_note,
                **values,
            ).save(using=using)
        self._changed_by = None
        self._change_note = ""
        self._reconfirm = False

    def _check_actor(self, field):
        # The account as stored decides, not the object given in memory.
        from django.contrib.auth import get_user_model

        actor, user_model = self._changed_by, get_user_model()
        account = None
        if isinstance(actor, user_model) and actor.pk is not None:
            account = (
                user_model._base_manager.filter(pk=actor.pk)
                .values("is_active", "is_staff")
                .first()
            )
        if account is None or not (account["is_active"] and account["is_staff"]):
            raise _refusal(
                self,
                field,
                "a change to due diligence names its actor: set _changed_by to an "
                "active staff user before save().",
            )

    def _check_notes_link(self):
        try:
            validate_notes_link(self.notes_link)
        except ValidationError:
            raise _refusal(
                self,
                "notes_link",
                "is an http(s) URL or a path relative to the repository root, not "
                f"{self.notes_link!r}.",
            ) from None


class DueDiligenceChange(GuardedModel):
    """One change to a due-diligence record: its values after the change, who
    made it and why. Written by DueDiligenceRecord.save(); never changed or
    deleted."""

    record = GuardedForeignKey(
        DueDiligenceRecord, on_delete=models.PROTECT, related_name="history"
    )
    status = models.CharField(max_length=16, choices=DueDiligenceStatus.choices)
    terms_time_limited = models.BooleanField()
    reviewer = models.CharField(max_length=255, blank=True)
    reviewed_on = models.DateField(null=True, blank=True)
    notes_link = models.CharField(max_length=1000, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    changed_at = models.DateTimeField(default=timezone.now)
    changed_by = _actor()
    changed_by_username = models.CharField(max_length=150, blank=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["changed_at", "id"]
        constraints = [
            models.CheckConstraint(
                check=Q(status__in=DueDiligenceStatus.values),
                name="graph_ddchange_status_valid",
            ),
            models.CheckConstraint(
                check=~Q(changed_by_username=""), name="graph_ddchange_actor"
            ),
        ]

    def __str__(self):
        return f"{self.status}, by {self.changed_by_username}"

    def lock_for_write(self, using, inserting):
        return (
            DueDiligenceRecord._base_manager.using(using)
            .select_for_update()
            .filter(pk=self.record_id)
            .values(*DueDiligenceRecord.AUDITED_FIELDS)
            .first()
        )

    def guard_insert(self, using, record):
        if record is None:
            return
        for name in DueDiligenceRecord.AUDITED_FIELDS:
            field = self._meta.get_field(name)
            value = getattr(self, field.attname)
            if _differs(field, value, record[name]):
                raise _refusal(
                    self,
                    name,
                    "states its record's value after the change; record "
                    f"{self.record_id} has {record[name]!r}, not {value!r}.",
                )

    def guard_update(self, stored, changed, using, record):
        raise _refusal(
            self,
            min(changed) if changed else "record",
            "the due-diligence history is append-only: a row never changes.",
        )


class ReleaseLicence(GuardedModel):
    """A snapshot of a release's licence terms, as a person read them.

    Never pre-filled: every term is unknown or blank until recorded, and no
    code infers one. Immutable from insertion: corrected or newer terms are a
    new snapshot. The latest snapshot of a release, the one with the highest
    id, holds its current terms.
    """

    release = GuardedForeignKey(
        DatasetRelease, on_delete=models.PROTECT, related_name="licences"
    )
    # Exactly as stated; "" means unknown.
    licence_name = models.CharField(max_length=255, blank=True)
    # Only when the source states one.
    spdx_id = models.CharField(max_length=64, blank=True)
    licence_url = models.URLField(max_length=1000, blank=True, validators=[HTTP_URL])
    terms_quoted = models.TextField(blank=True)
    # Where and when the terms were read.
    read_at = models.URLField(max_length=1000, blank=True, validators=[HTTP_URL])
    read_on = models.DateField(null=True, blank=True)
    attribution = models.TextField(blank=True)
    redistribution = models.CharField(
        max_length=32, choices=Permission.choices, default=Permission.UNKNOWN
    )
    commercial_use = models.CharField(
        max_length=32, choices=Permission.choices, default=Permission.UNKNOWN
    )
    modification = models.CharField(
        max_length=32, choices=Permission.choices, default=Permission.UNKNOWN
    )
    share_alike = models.CharField(
        max_length=16, choices=ShareAlike.choices, default=ShareAlike.UNKNOWN
    )
    obligations = models.TextField(blank=True)
    ambiguity_notes = models.TextField(blank=True)
    reviewer = models.CharField(max_length=255, blank=True)
    recorded_at = models.DateTimeField(default=timezone.now)
    recorded_by = _actor()
    recorded_by_username = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=Q(redistribution__in=Permission.values),
                name="graph_lic_redistribution_valid",
            ),
            models.CheckConstraint(
                check=Q(commercial_use__in=Permission.values),
                name="graph_lic_commercial_use_valid",
            ),
            models.CheckConstraint(
                check=Q(modification__in=Permission.values),
                name="graph_lic_modification_valid",
            ),
            models.CheckConstraint(
                check=Q(share_alike__in=ShareAlike.values),
                name="graph_lic_share_alike_valid",
            ),
            models.CheckConstraint(
                check=~Q(recorded_by_username=""), name="graph_licence_recorded_by"
            ),
        ]

    def __str__(self):
        return self.licence_name or "unknown licence"

    @property
    def records_terms(self) -> bool:
        """The snapshot states terms, with where and when they were read: what
        an approval relies on."""
        stated = self.licence_name.strip() or self.terms_quoted.strip()
        return bool(stated and self.read_at and self.read_on)

    def guard_update(self, stored, changed, using, locked):
        raise _refusal(
            self,
            min(changed) if changed else "release",
            "a licence snapshot never changes: record corrected terms as a new "
            "snapshot.",
        )


class UsageApproval(GuardedModel):
    """A recorded decision on one use of a release: approved or denied, with
    who decided, why, and the licence snapshot relied on.

    A decision is never edited. After insertion only its revoked fields are
    set, once, by a revocation; a new decision is a new row. Whether a
    decision is effective is graph.approvals' question.
    """

    REVOKE_FIELDS = frozenset(
        {"revoked_at", "revoked_by", "revoked_by_username", "revoke_reason"}
    )

    release = GuardedForeignKey(
        DatasetRelease, on_delete=models.PROTECT, related_name="approvals"
    )
    scope = models.CharField(max_length=32, choices=ApprovalScope.choices)
    decision = models.CharField(max_length=16, choices=Decision.choices)
    # The snapshot relied on: one of the release's own.
    licence = GuardedForeignKey(
        ReleaseLicence,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="approvals",
    )
    rationale = models.TextField()
    # A URL or text.
    evidence = models.TextField(blank=True)
    decided_by = _actor()
    decided_by_username = models.CharField(max_length=150, blank=True)
    decided_at = models.DateTimeField(default=timezone.now)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = _actor()
    revoked_by_username = models.CharField(max_length=150, blank=True)
    revoke_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # One active decision per release and scope.
            models.UniqueConstraint(
                fields=["release", "scope"],
                condition=Q(revoked_at__isnull=True),
                name="graph_approval_one_active",
            ),
            models.CheckConstraint(
                check=Q(scope__in=ApprovalScope.values),
                name="graph_approval_scope_valid",
            ),
            models.CheckConstraint(
                check=Q(decision__in=Decision.values),
                name="graph_approval_decision_valid",
            ),
            models.CheckConstraint(
                check=~Q(rationale=""), name="graph_approval_rationale"
            ),
            models.CheckConstraint(
                check=~Q(decided_by_username=""), name="graph_approval_decided_by"
            ),
            models.CheckConstraint(
                check=~Q(decision=Decision.APPROVED) | Q(licence__isnull=False),
                name="graph_approval_licence",
            ),
            # A recorded rationale and evidence (open question C2).
            models.CheckConstraint(
                check=~Q(
                    decision=Decision.APPROVED,
                    scope__in=[ApprovalScope.PUBLIC_VIEW, ApprovalScope.PUBLIC_BULK],
                )
                | ~Q(evidence=""),
                name="graph_approval_public_evidence",
            ),
            models.CheckConstraint(
                check=Q(
                    revoked_at__isnull=True, revoke_reason="", revoked_by_username=""
                )
                | (
                    Q(revoked_at__isnull=False)
                    & ~Q(revoke_reason="")
                    & ~Q(revoked_by_username="")
                ),
                name="graph_approval_revoked_fields",
            ),
        ]

    def __str__(self):
        return f"{self.scope} {self.decision}"

    @property
    def active(self) -> bool:
        return self.revoked_at is None

    def guard_insert(self, using, locked):
        if self.licence_id is None:
            return
        owner = (
            ReleaseLicence._base_manager.using(using)
            .filter(pk=self.licence_id)
            .values_list("release_id", flat=True)
            .first()
        )
        if owner is not None and owner != self.release_id:
            raise _refusal(
                self,
                "licence",
                "a decision relies on a snapshot of its own release; licence "
                f"{self.licence_id} belongs to release {owner}, not {self.release_id}.",
            )

    def guard_update(self, stored, changed, using, locked):
        edited = changed - self.REVOKE_FIELDS
        if edited:
            raise _refusal(
                self,
                min(edited),
                "a decision is never edited: only its revoked fields are set, once. "
                "Record a new decision instead.",
            )
        if not changed:
            return
        if stored.revoked_at is not None:
            raise _refusal(
                self,
                min(changed),
                f"decision {self.pk} is already revoked; a revocation is set once.",
            )
        if self.revoked_at is None:
            raise _refusal(
                self,
                min(changed),
                "the revoked fields are set together, by a revocation.",
            )


# ── Relationships and evidence: shared rules ────────────────────────────────

# NCBI Taxonomy's "synthetic construct" (taxa.json): synthetic releases name
# only its entities, and no other release names any (graph.entities, and the
# evidence guards below).
SYNTHETIC_TAXON = 32630
# The physical type for rows whose source states no interaction type: its
# assertions carry none, at level not_stated (architecture D20).
UNSPECIFIED_PHYSICAL_TYPE = "protein_interaction_unspecified"
# <source>:<name>, or stats:<name> for statistics openPIP computes.
SCORE_TYPE_SLUG = re.compile(r"[a-z][a-z0-9_]*:[a-z0-9][a-z0-9_.-]*")
# Limits on what a source's own words take (architecture §8).
STATED_TEXT_MAX = 10_000
QUALIFIERS_MAX = 10_000


def _finite(name) -> Q:
    """NULL or a finite float. Probes on PostgreSQL 16 showed this check
    rejecting NaN and both infinities; SQLite rejects the infinities and
    stores NaN as NULL."""
    return Q(**{f"{name}__isnull": True}) | Q(
        **{f"{name}__gte": -sys.float_info.max, f"{name}__lte": sys.float_info.max}
    )


def _check_text(model, name, value, limit, *, blank=False):
    """A text value as the evidence rules store it: a str of at most `limit`
    characters (SQLite does not enforce column sizes), without NUL, and not
    blank unless `blank`."""
    if not isinstance(value, str):
        raise _refusal(model, name, f"is text, not {value!r}.")
    if "\x00" in value:
        raise _refusal(model, name, "contains a NUL character (U+0000).")
    if len(value) > limit:
        raise _refusal(model, name, f"is at most {limit} characters, not {len(value)}.")
    if not blank and not value.strip():
        raise _refusal(model, name, "is never blank.")


def _check_closed(model, name, value, choices, *, null=False):
    if value is None and null:
        return
    if value not in choices.values:
        allowed = ", ".join(choices.values) + (", or NULL" if null else "")
        raise _refusal(model, name, f"is one of {allowed}; not {value!r}.")


def qualifiers_problem(value) -> str | None:
    """Why an assertion's qualifiers are not stored as they stand, or None.

    They are a JSON object with string keys, made only of what json.loads
    returns (a tuple is a list once round-tripped; NaN and infinity have no
    JSON form), with no NUL in a key or a string, and at most QUALIFIERS_MAX
    characters as JSON. graph.claims round-trips a claim's qualifiers through
    json.loads(json.dumps(q, allow_nan=False)) before validating, storing and
    comparing them, so what it stores passes.
    """
    if not isinstance(value, dict):
        return f"are a JSON object, not {type(value).__name__}."
    problem = _plain_json_problem(value, "")
    if problem:
        return problem
    size = len(json.dumps(value, ensure_ascii=False, allow_nan=False))
    if size > QUALIFIERS_MAX:
        return f"are at most {QUALIFIERS_MAX} characters as JSON, not {size}."
    return None


def _plain_json_problem(value, path) -> str | None:
    where = f"qualifiers{path}"
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                return f"have a key that is not a string at {where}: {key!r}."
            if "\x00" in key:
                return f"have a key with a NUL character at {where}."
            problem = _plain_json_problem(item, f"{path}[{key!r}]")
            if problem:
                return problem
        return None
    # A tuple is not a list here: JSON would read it back as one.
    if isinstance(value, list):
        for index, item in enumerate(value):
            problem = _plain_json_problem(item, f"{path}[{index}]")
            if problem:
                return problem
        return None
    if isinstance(value, str):
        return f"have a NUL character in {where}." if "\x00" in value else None
    if isinstance(value, float):
        if math.isfinite(value):
            return None
        return f"have {value!r} at {where}, which JSON cannot hold."
    if value is None or isinstance(value, int):
        return None
    return (
        f"hold a {type(value).__name__} at {where}: store what json.loads "
        "returns, as a round trip through JSON gives it."
    )


# ── Registries: relationship types and score types ──────────────────────────
# As the registries above: rows come only from the reviewed JSON, loaded by
# graph_sync_registry. What evidence may say depends on them, so a writer that
# validates against a row holds it with share_lock() until it commits, and the
# sync locks a row before it changes it.


class RelationshipType(models.Model):
    """What a relationship claims, and what it does not: its family, how its
    endpoints are ordered, whether its evidence carries an effect or a
    genetic sign, and which entity types stand on each side (architecture
    §3.3, §4.2)."""

    slug = models.SlugField(max_length=50, primary_key=True)
    label = models.CharField(max_length=100)
    # What the edge claims, and what it does not.
    definition = models.TextField()
    family = models.CharField(max_length=24, choices=RelationshipFamily.choices)
    orientation = models.CharField(max_length=16, choices=Orientation.choices)
    polarity = models.CharField(max_length=16, choices=Polarity.choices)
    allowed_subject_types = models.ManyToManyField(
        EntityType, related_name="relationship_types_as_subject"
    )
    allowed_object_types = models.ManyToManyField(
        EntityType, related_name="relationship_types_as_object"
    )
    # Such as "regulator" and "target".
    subject_role_label = models.CharField(max_length=50)
    object_role_label = models.CharField(max_length=50)
    # Always [] in G3: identity qualifiers wait for P2.
    identity_qualifiers = models.JSONField(default=list)
    self_pairs_allowed = models.BooleanField()
    # The PSI-MI CURIEs the type corresponds to: a descriptive crosswalk.
    psi_mi_anchors = models.JSONField(default=list)
    display_token = models.CharField(max_length=50, blank=True)
    # A link between entity layers, not an evidence-backed claim.
    is_reference = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=Q(family__in=RelationshipFamily.values),
                name="graph_reltype_family_valid",
            ),
            models.CheckConstraint(
                check=Q(orientation__in=Orientation.values),
                name="graph_reltype_orient_valid",
            ),
            models.CheckConstraint(
                check=Q(polarity__in=Polarity.values),
                name="graph_reltype_polarity_valid",
            ),
            models.CheckConstraint(
                check=Q(is_reference=True, family=RelationshipFamily.REFERENCE)
                | (Q(is_reference=False) & ~Q(family=RelationshipFamily.REFERENCE)),
                name="graph_reltype_reference",
            ),
            # A reference link has two roles in a fixed order, and no effect.
            models.CheckConstraint(
                check=~Q(family=RelationshipFamily.REFERENCE)
                | Q(orientation=Orientation.ROLE_ORDERED, polarity=Polarity.NONE),
                name="graph_reltype_reference_shape",
            ),
        ]

    def __str__(self):
        return self.slug


class ScoreType(models.Model):
    """What a score's value means: its kind, which direction is stronger,
    the range it may take, what a signed value's sign is relative to, and
    the document that defines it (architecture §3.3)."""

    # <source>:<name> or stats:<name> (SCORE_TYPE_SLUG).
    slug = models.CharField(max_length=64, primary_key=True)
    label = models.CharField(max_length=100)
    definition = models.TextField()
    value_kind = models.CharField(max_length=16, choices=ScoreValueKind.choices)
    direction = models.CharField(max_length=24, choices=ScoreDirection.choices)
    range_min = models.FloatField(null=True, blank=True)
    range_max = models.FloatField(null=True, blank=True)
    unit = models.CharField(max_length=50, blank=True)
    # What a signed value's sign is relative to.
    null_model = models.TextField(blank=True)
    # Where the score is defined: a URL or a path in this repository.
    defining_document = models.TextField()
    comparable_across_datasets = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=Q(value_kind__in=ScoreValueKind.values),
                name="graph_scoretype_kind_valid",
            ),
            models.CheckConstraint(
                check=Q(direction__in=ScoreDirection.values),
                name="graph_scoretype_dir_valid",
            ),
            models.CheckConstraint(
                check=_finite("range_min"), name="graph_scoretype_min_finite"
            ),
            models.CheckConstraint(
                check=_finite("range_max"), name="graph_scoretype_max_finite"
            ),
            models.CheckConstraint(
                check=Q(range_min__isnull=True)
                | Q(range_max__isnull=True)
                | Q(range_min__lte=F("range_max")),
                name="graph_scoretype_range",
            ),
        ]

    def __str__(self):
        return self.slug


# ── Vocabulary terms ────────────────────────────────────────────────────────


class Term(GuardedModel):
    """A controlled-vocabulary term, by its CURIE (graph.terms).

    A registry with a natural key (architecture §3.3): the keys that point at
    it hold the CURIE, so the independence key and the method filter need no
    join. graph.terms.get_or_create_term writes one inside an approved
    commit, from the CURIE and label a source states, and never invents one.
    Only a draft release's commit states a label, so the guard refuses a
    label_release that is not a draft on every insert path. Nothing changes
    or deletes a term through the ORM; the deletion of the draft release
    that supplied a label empties label_release through the deletion
    collector, as the actor keys are emptied. bulk_create() is allowed, for
    the race-safe insert, with save()'s checks on every row.
    """

    curie = models.CharField(max_length=64, primary_key=True)
    # From the CURIE's prefix (graph.terms.ONTOLOGIES). No database check, so
    # an ontology is added without a constraint change; the guard checks it.
    ontology = models.CharField(max_length=16)
    # As the first commit supplied it; a PSI-MI crosswalk term keeps the
    # crosswalk's label (graph.terms).
    label = models.CharField(max_length=255)
    # The release whose commit supplied the label: NULL when a crosswalk
    # term's first commit stated another label, or once that draft release is
    # deleted.
    label_release = GuardedForeignKey(
        DatasetRelease,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    definition = models.TextField(blank=True)
    obsolete = models.BooleanField(default=False)
    # '' until V1 loads versioned ontology releases.
    ontology_release = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.curie

    def guard_insert(self, using, locked):
        Term._check_rows([self], using)

    @classmethod
    def guard_bulk_create(cls, objs, using):
        cls._check_rows(objs, using)

    @classmethod
    def _check_rows(cls, terms, using):
        # graph.terms imports this module, so it is imported here.
        from .terms import TermError, normalize_curie, ontology_of

        for term in terms:
            try:
                canonical = normalize_curie(term.curie)
            except TermError as error:
                raise _refusal(cls, "curie", str(error)) from None
            if canonical != term.curie:
                raise _refusal(
                    cls,
                    "curie",
                    f"is stored canonical: {term.curie!r} is {canonical!r}.",
                )
            ontology = ontology_of(canonical)
            if term.ontology != ontology:
                raise _refusal(
                    cls,
                    "ontology",
                    f"follows the CURIE's prefix: {canonical} is a {ontology} term, "
                    f"not {term.ontology!r}.",
                )
            _check_text(cls, "label", term.label, 255)
            _check_text(cls, "ontology_release", term.ontology_release, 64, blank=True)
        # Only a draft release's commit states a label (graph.terms), and
        # get_or_create_term holds that release's lock already.
        releases = _locked_releases(using, [term.label_release_id for term in terms])
        for term in terms:
            if term.label_release_id is None:
                continue
            release = releases.get(term.label_release_id)
            if release is None or release.status != ReleaseStatus.DRAFT:
                status = release.status if release is not None else "not stored"
                raise _refusal(
                    cls,
                    "label_release",
                    "names the draft release whose commit states the label; "
                    f"release {term.label_release_id} is {status}.",
                )


# ── Relationships ───────────────────────────────────────────────────────────


class Relationship(GuardedModel):
    """The claim itself: a type and two entities, with no counts or effect
    stored on it (architecture §3.6). Assertions from many releases attach
    to one relationship, which is never changed or deleted.

    A symmetric type's pair is stored lower entity id first, so A-B and B-A
    are one row (graph_rel_symmetric_order); for directed and role-ordered
    types the order is the claim, so A->B and B->A are two. graph.claims
    builds the canonical identity. The guard re-checks every rule from the
    database on save() and on bulk_create(), the race-safe insert, with a
    fixed number of queries per call: SQLite's INSERT OR IGNORE would drop a
    row that breaks a check constraint without a word.
    """

    relationship_type = GuardedForeignKey(
        RelationshipType, on_delete=models.PROTECT, related_name="+"
    )
    # The regulator, gene or isoform; the lower entity id for symmetric types.
    subject = GuardedForeignKey(Entity, on_delete=models.PROTECT, related_name="+")
    object = GuardedForeignKey(Entity, on_delete=models.PROTECT, related_name="+")
    # orientation != symmetric, copied from the type when the row is created.
    ordered = models.BooleanField()
    # Always '' in G3: identity qualifiers, and the re-keying they need, wait
    # for P2.
    qualifier_key = models.CharField(max_length=160, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["relationship_type", "subject", "object", "qualifier_key"],
                name="graph_rel_identity",
            ),
            models.CheckConstraint(
                check=Q(ordered=True) | Q(subject_id__lte=F("object_id")),
                name="graph_rel_symmetric_order",
            ),
        ]
        indexes = [
            models.Index(
                fields=["subject", "relationship_type"], name="graph_rel_subject_type"
            ),
            models.Index(
                fields=["object", "relationship_type"], name="graph_rel_object_type"
            ),
        ]

    def __str__(self):
        arrow = "->" if self.ordered else "--"
        return f"{self.relationship_type_id} {self.subject_id}{arrow}{self.object_id}"

    def guard_insert(self, using, locked):
        Relationship._check_rows([self], using)

    @classmethod
    def guard_bulk_create(cls, objs, using):
        cls._check_rows(objs, using)

    @classmethod
    def _check_rows(cls, rows, using):
        """Every row's rules, in five queries whatever the number of rows: the
        types (share-locked first), their allowed subject and object types,
        and the endpoints."""
        type_ids = {row.relationship_type_id for row in rows} - {None}
        share_lock(RelationshipType, using, type_ids)
        types = {
            row["slug"]: row
            for row in RelationshipType.objects.using(using)
            .filter(pk__in=type_ids)
            .values("slug", "orientation", "self_pairs_allowed")
        }
        allowed = {
            name: _allowed_types(getattr(RelationshipType, name).through, using, types)
            for name in ("allowed_subject_types", "allowed_object_types")
        }
        entity_ids = {row.subject_id for row in rows} | {row.object_id for row in rows}
        entities = {
            row["pk"]: row
            for row in Entity._base_manager.using(using)
            .filter(pk__in=entity_ids - {None})
            .values("pk", "entity_type_id", "status")
        }
        for row in rows:
            row._check_identity(types, allowed, entities)

    def _check_identity(self, types, allowed, entities):
        rel_type = types.get(self.relationship_type_id)
        if rel_type is None:
            raise _refusal(
                self,
                "relationship_type",
                f"names no relationship type: {self.relationship_type_id!r}.",
            )
        slug = rel_type["slug"]
        if self.qualifier_key != "":
            raise _refusal(
                self,
                "qualifier_key",
                "is '' until identity qualifiers arrive (P2); no type declares "
                f"one, so {self.qualifier_key!r} is refused.",
            )
        ordered = rel_type["orientation"] != Orientation.SYMMETRIC
        if self.ordered is not ordered:
            raise _refusal(
                self,
                "ordered",
                f"follows the type's orientation: {slug} is "
                f"{rel_type['orientation']}, so ordered is {ordered}, not "
                f"{self.ordered!r}.",
            )
        if not ordered and not (
            self.subject_id is not None
            and self.object_id is not None
            and self.subject_id <= self.object_id
        ):
            raise _refusal(
                self,
                "subject",
                f"a {slug} pair is symmetric, stored lower entity id first: "
                f"subject {self.subject_id}, object {self.object_id}.",
            )
        for side, name in (
            ("subject", "allowed_subject_types"),
            ("object", "allowed_object_types"),
        ):
            pk = getattr(self, f"{side}_id")
            entity = entities.get(pk)
            if entity is None:
                raise _refusal(self, side, f"names no entity: {pk!r}.")
            if entity["status"] in (EntityStatus.MERGED, EntityStatus.RETIRED):
                raise _refusal(
                    self,
                    side,
                    f"is an active or provisional entity; entity {pk} is "
                    f"{entity['status']}, and its evidence belongs on its "
                    "successor.",
                )
            permitted = sorted(allowed[name].get(slug, ()))
            if entity["entity_type_id"] not in permitted:
                raise _refusal(
                    self,
                    side,
                    f"a {slug} {side} is a {' or '.join(permitted) or 'type none'}; "
                    f"entity {pk} is a {entity['entity_type_id']}.",
                )
        if self.subject_id == self.object_id and not rel_type["self_pairs_allowed"]:
            raise _refusal(
                self,
                "object",
                f"{slug} relates two different entities; entity {self.subject_id} "
                "is both.",
            )


def _allowed_types(through, using, types) -> dict:
    """{relationship type: entity type slugs} from one many-to-many table."""
    allowed = defaultdict(set)
    rows = (
        through.objects.using(using)
        .filter(relationshiptype_id__in=list(types))
        .values_list("relationshiptype_id", "entitytype_id")
    )
    for type_slug, entity_type in rows:
        allowed[type_slug].add(entity_type)
    return allowed


# ── Evidence ────────────────────────────────────────────────────────────────


class EvidenceAssertion(ReleaseScoped):
    """One statement a source record makes about one relationship: its
    outcome, effect or genetic sign, level, methods, publications, scores and
    context, and the source's own wording (architecture §3.6).

    It belongs to its source record's release. graph.claims.record_claims
    writes it once with save() (bulk_create() is refused: the release lock
    already serializes every writer of a record), while that release is a
    draft and its commit run is running; it is never saved again, changed or
    not, and it is deleted only while the release is a draft. The guard
    re-checks from the database the rules that compare it with other rows:
    its record and run, its relationship's type (polarity, outcome, level),
    its terms' ontologies and the PSI-MI crosswalk, the synthetic separation
    of its endpoints, and its normalized qualifiers, of which
    qualifiers["threshold"] belongs to a not-significant assertion, which
    always states one. What needs its scores (a genetic sign's signed score,
    whether a not-significant result's criteria name its scores and one of
    them fails) is record_claims' to check before anything is written.
    """

    # The effects a source can state; NULL means it states none.
    STORED_EFFECTS = frozenset({Effect.ACTIVATING, Effect.INHIBITING, Effect.UNKNOWN})

    relationship = GuardedForeignKey(
        Relationship, on_delete=models.PROTECT, related_name="assertions"
    )
    # The source record's, copied so filters need no join.
    release = GuardedForeignKey(
        DatasetRelease, on_delete=models.CASCADE, related_name="+"
    )
    # RESTRICT on record and run: they go only with their whole draft release.
    source_record = GuardedForeignKey(
        SourceRecord, on_delete=models.RESTRICT, related_name="+"
    )
    # The commit run that first wrote it.
    import_run = GuardedForeignKey(
        ImportRun, on_delete=models.RESTRICT, related_name="+"
    )
    # Deterministic and content-derived within the record (graph.claims).
    assertion_key = models.CharField(max_length=400)
    outcome = models.CharField(max_length=16, choices=Outcome.choices)
    evidence_basis = models.CharField(max_length=32, choices=EvidenceBasis.choices)
    assertion_method = models.CharField(max_length=24, choices=AssertionMethod.choices)
    eco_term = GuardedForeignKey(
        Term, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    # NULL: the source states none.
    effect = models.CharField(
        max_length=16, choices=Effect.choices, null=True, blank=True
    )
    genetic_sign = models.CharField(
        max_length=16, choices=GeneticSign.choices, null=True, blank=True
    )
    # The PSI-MI interaction type as stated, never upgraded.
    interaction_type = GuardedForeignKey(
        Term, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    # Physical-family evidence only (architecture D20).
    interaction_level = models.CharField(
        max_length=24, choices=InteractionLevel.choices, null=True, blank=True
    )
    relationship_as_stated = models.TextField(blank=True)
    effect_as_stated = models.TextField(blank=True)
    # A group is (release, evidence_group): records of one n-ary observation.
    evidence_group = models.CharField(max_length=255, blank=True)
    # Further mapped source fields. "threshold" is reserved for the criterion
    # a not-significant result failed (graph.claims).
    qualifiers = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["source_record", "assertion_key"],
                name="graph_assert_record_key",
            ),
            models.CheckConstraint(
                check=Q(outcome__in=Outcome.values), name="graph_assert_outcome_valid"
            ),
            models.CheckConstraint(
                check=Q(evidence_basis__in=EvidenceBasis.values),
                name="graph_assert_basis_valid",
            ),
            models.CheckConstraint(
                check=Q(assertion_method__in=AssertionMethod.values),
                name="graph_assert_method_valid",
            ),
            models.CheckConstraint(
                check=Q(effect__isnull=True) | Q(effect__in=Effect.values),
                name="graph_assert_effect_valid",
            ),
            models.CheckConstraint(
                check=Q(genetic_sign__isnull=True)
                | Q(genetic_sign__in=GeneticSign.values),
                name="graph_assert_sign_valid",
            ),
            models.CheckConstraint(
                check=Q(interaction_level__isnull=True)
                | Q(interaction_level__in=InteractionLevel.values),
                name="graph_assert_level_valid",
            ),
            models.CheckConstraint(
                check=~Q(effect__isnull=False, genetic_sign__isnull=False),
                name="graph_assert_effect_xor_sign",
            ),
            # Tested-absent, inconclusive and not-significant assertions carry
            # no effect and no sign.
            models.CheckConstraint(
                check=Q(outcome=Outcome.SUPPORTED)
                | (
                    Q(genetic_sign__isnull=True)
                    & (Q(effect__isnull=True) | Q(effect=Effect.NOT_APPLICABLE))
                ),
                name="graph_assert_bare_unless_sup",
            ),
        ]
        indexes = [
            models.Index(
                fields=["relationship", "release"], name="graph_assert_rel_release"
            ),
            models.Index(
                fields=["release", "outcome"], name="graph_assert_release_outcome"
            ),
        ]

    def __str__(self):
        return f"{self.source_record_id}/{self.assertion_key}"

    def guard_insert(self, using, release):
        if release is None:
            raise _refusal(self, "release", f"names no release: {self.release_id!r}.")
        if release.status != ReleaseStatus.DRAFT:
            raise _refusal(
                self,
                "release",
                "evidence is written only while its release is a draft; release "
                f"{release.pk} is {release.status}.",
            )
        self._check_values()
        self._check_run_and_record(using)
        relationship, rel_type = self._relationship(using)
        self._check_polarity(rel_type)
        self._check_threshold()
        self._check_terms(using)
        self._check_level(rel_type)
        self._check_endpoints(using, relationship, release)

    def guard_update(self, stored, changed, using, release):
        raise _refusal(
            self,
            min(changed) if changed else None,
            f"assertion {self.pk} is stored, and a stored assertion is never "
            "saved again, changed or not: a correction is a new release.",
        )

    def _check_values(self):
        """The rules that read this row alone."""
        model = type(self)
        _check_text(model, "assertion_key", self.assertion_key, 400)
        for name in ("relationship_as_stated", "effect_as_stated"):
            _check_text(model, name, getattr(self, name), STATED_TEXT_MAX, blank=True)
        _check_text(model, "evidence_group", self.evidence_group, 255, blank=True)
        _check_closed(model, "outcome", self.outcome, Outcome)
        _check_closed(model, "evidence_basis", self.evidence_basis, EvidenceBasis)
        _check_closed(model, "assertion_method", self.assertion_method, AssertionMethod)
        _check_closed(model, "genetic_sign", self.genetic_sign, GeneticSign, null=True)
        _check_closed(
            model,
            "interaction_level",
            self.interaction_level,
            InteractionLevel,
            null=True,
        )
        effect = self.effect
        if effect == Effect.NOT_APPLICABLE:
            raise _refusal(
                self,
                "effect",
                "not_applicable is the summary for types without effect polarity, "
                "and is never stored.",
            )
        if effect is not None and effect not in self.STORED_EFFECTS:
            if isinstance(effect, str) and effect.strip().lower() == "negative":
                rule = (
                    "'negative' is ambiguous as an effect (inhibiting? a negative "
                    "genetic interaction? tested and not found?), so it is refused, "
                    "never coerced."
                )
            else:
                rule = (
                    "is activating, inhibiting, unknown, or NULL when the source "
                    f"states none; not {effect!r}."
                )
            raise _refusal(self, "effect", rule)
        problem = qualifiers_problem(self.qualifiers)
        if problem:
            raise _refusal(self, "qualifiers", problem)

    def _check_run_and_record(self, using):
        # The run is locked after the release, as record_claims locks them.
        run = (
            ImportRun._base_manager.using(using)
            .select_for_update()
            .filter(pk=self.import_run_id)
            .values("release_id", "mode", "state")
            .first()
        )
        if run is None:
            raise _refusal(
                self, "import_run", f"names no import run: {self.import_run_id!r}."
            )
        if run["release_id"] != self.release_id:
            raise _other_release(
                type(self),
                "import_run",
                ImportRun,
                self.import_run_id,
                run["release_id"],
                self.release_id,
            )
        if run["mode"] != ImportMode.COMMIT or run["state"] != ImportState.RUNNING:
            raise _refusal(
                self,
                "import_run",
                "evidence is written by a running commit run; run "
                f"{self.import_run_id} is a {run['mode']} run, {run['state']}.",
            )
        record = (
            SourceRecord._base_manager.using(using)
            .filter(pk=self.source_record_id)
            .values("release_id", "status")
            .first()
        )
        if record is None:
            raise _refusal(
                self,
                "source_record",
                f"names no source record: {self.source_record_id!r}.",
            )
        if record["release_id"] != self.release_id:
            raise _other_release(
                type(self),
                "source_record",
                SourceRecord,
                self.source_record_id,
                record["release_id"],
                self.release_id,
            )
        if record["status"] != RecordStatus.MAPPED:
            raise _refusal(
                self,
                "source_record",
                "evidence is recorded only from a mapped record; record "
                f"{self.source_record_id} is {record['status']}.",
            )

    def _relationship(self, using):
        relationship = (
            Relationship._base_manager.using(using)
            .filter(pk=self.relationship_id)
            .values("relationship_type_id", "subject_id", "object_id")
            .first()
        )
        if relationship is None:
            raise _refusal(
                self,
                "relationship",
                f"names no relationship: {self.relationship_id!r}.",
            )
        type_id = relationship["relationship_type_id"]
        share_lock(RelationshipType, using, [type_id])
        rel_type = (
            RelationshipType.objects.using(using)
            .filter(pk=type_id)
            .values("slug", "family", "polarity")
            .get()
        )
        return relationship, rel_type

    def _check_polarity(self, rel_type):
        """One encoding per polarity: effects only on effect types, signs only
        on sign types, and neither on an assertion that is not supported."""
        polarity, slug = rel_type["polarity"], rel_type["slug"]
        if self.effect is not None and polarity != Polarity.EFFECT:
            raise _refusal(
                self,
                "effect",
                "only a type with effect polarity carries an effect; "
                f"{slug} has polarity {polarity}.",
            )
        if self.genetic_sign is not None and polarity != Polarity.GENETIC_SIGN:
            raise _refusal(
                self,
                "genetic_sign",
                "only a type with genetic_sign polarity carries a sign; "
                f"{slug} has polarity {polarity}.",
            )
        if self.outcome != Outcome.SUPPORTED:
            for name in ("effect", "genetic_sign"):
                if getattr(self, name) is not None:
                    raise _refusal(
                        self,
                        name,
                        "an assertion that is not supported carries no effect and "
                        f"no sign; this one is {self.outcome}.",
                    )

    def _check_threshold(self):
        """Rule 8 on this row alone: only a not_significant assertion states
        the criterion it failed, and it always does. Whether the criteria name
        its scores and one of them fails is record_claims' to check."""
        threshold = self.qualifiers.get("threshold")
        if self.outcome == Outcome.NOT_SIGNIFICANT:
            if not isinstance(threshold, list) or not threshold:
                raise _refusal(
                    self,
                    "qualifiers",
                    "a not_significant assertion records the significance criterion "
                    'it failed in qualifiers["threshold"], a non-empty list of '
                    "criteria.",
                )
        elif "threshold" in self.qualifiers:
            raise _refusal(
                self,
                "qualifiers",
                'qualifiers["threshold"] is the criterion a not_significant result '
                f"failed, and is refused on a {self.outcome} assertion.",
            )

    def _check_terms(self, using):
        wanted = (
            ("eco_term", self.eco_term_id, "eco"),
            ("interaction_type", self.interaction_type_id, "psi-mi"),
        )
        curies = [curie for _, curie, _ in wanted if curie is not None]
        if not curies:
            return
        found = dict(
            Term._base_manager.using(using)
            .filter(pk__in=curies)
            .values_list("pk", "ontology")
        )
        for name, curie, ontology in wanted:
            if curie is None:
                continue
            if curie not in found:
                raise _refusal(self, name, f"names no term: {curie!r}.")
            if found[curie] != ontology:
                raise _refusal(
                    self, name, f"is a {ontology} term; {curie} is {found[curie]}."
                )

    def _check_level(self, rel_type):
        """The stored level, read by every label and level filter (D20)."""
        from .terms import PHYSICAL_INTERACTION_LEVELS

        level, term = self.interaction_level, self.interaction_type_id
        slug = rel_type["slug"]
        if rel_type["family"] != RelationshipFamily.PHYSICAL:
            for name, value in (
                ("interaction_level", level),
                ("interaction_type", term),
            ):
                if value is not None:
                    raise _refusal(
                        self,
                        name,
                        "belongs to physical-family evidence only; "
                        f"{slug} is {rel_type['family']}.",
                    )
            return
        if level is None:
            raise _refusal(
                self,
                "interaction_level",
                f"is stated on {slug} evidence: not_stated when the source states "
                "no interaction type.",
            )
        if slug == UNSPECIFIED_PHYSICAL_TYPE and (
            term is not None or level != InteractionLevel.NOT_STATED
        ):
            raise _refusal(
                self,
                "interaction_type" if term is not None else "interaction_level",
                f"{slug} evidence states no interaction type, at level not_stated.",
            )
        if term is None:
            if level not in (InteractionLevel.NOT_STATED, InteractionLevel.ASSOCIATION):
                raise _refusal(
                    self,
                    "interaction_level",
                    f"{level} rests on the interaction type the source states; "
                    "without one the level is not_stated, or association assigned "
                    "by openPIP (open question B14).",
                )
            return
        crosswalk = PHYSICAL_INTERACTION_LEVELS.get(term)
        if crosswalk is None:
            raise _refusal(
                self,
                "interaction_type",
                f"{term} is not in the PSI-MI physical crosswalk "
                "(graph.terms.PHYSICAL_INTERACTION_LEVELS), so it is never "
                "physical-interaction evidence.",
            )
        if level != crosswalk[0]:
            raise _refusal(
                self,
                "interaction_level",
                f"is the crosswalk's level for {term}, {crosswalk[0]}; not {level}.",
            )

    def _check_endpoints(self, using, relationship, release):
        """Synthetic separation: a synthetic release's evidence joins synthetic
        constructs only, and no other release's evidence names one."""
        ends = (relationship["subject_id"], relationship["object_id"])
        taxa = dict(
            Entity._base_manager.using(using)
            .filter(pk__in=ends)
            .values_list("pk", "taxon_id")
        )
        for pk in ends:
            taxon = taxa.get(pk)
            if (taxon == SYNTHETIC_TAXON) == release.is_synthetic:
                continue
            if release.is_synthetic:
                rule = (
                    "a synthetic release's evidence joins synthetic constructs only "
                    f"(taxon {SYNTHETIC_TAXON}); entity {pk} is of taxon {taxon}."
                )
            else:
                rule = (
                    f"synthetic constructs (taxon {SYNTHETIC_TAXON}) appear only in "
                    f"synthetic releases' evidence; entity {pk} is one, and release "
                    f"{release.pk} is not synthetic."
                )
            raise _refusal(self, "relationship", rule)


class AssertionPart(ReleaseScoped):
    """What the parts of an assertion share: the assertion, and its release,
    copied so filters need no join. A part is inserted only while the release
    is a draft, never saved again, and deleted only while the release is a
    draft (or with its assertion).

    record_claims writes each kind with one bulk_create(), whose guard runs a
    fixed number of queries whatever the number of rows: the releases locked
    once, in id order; the assertions read in one query; each of the kind's
    rules checked over all rows in one more.
    """

    # What check_parts reads of each row's assertion and its relationship.
    ASSERTION_FIELDS = ()

    class Meta:
        abstract = True

    def guard_insert(self, using, release):
        type(self)._check_inserts(
            [self], using, {release.pk: release} if release else {}
        )

    def guard_update(self, stored, changed, using, release):
        raise _refusal(
            self,
            min(changed) if changed else None,
            f"row {self.pk} is written with its assertion and never saved again.",
        )

    @classmethod
    def guard_bulk_create(cls, objs, using):
        releases = _locked_releases(using, [row.release_id for row in objs])
        cls._check_inserts(objs, using, releases)

    @classmethod
    def _check_inserts(cls, rows, using, releases):
        for row in rows:
            release = releases.get(row.release_id)
            if release is None:
                raise _refusal(cls, "release", f"names no release: {row.release_id!r}.")
            if release.status != ReleaseStatus.DRAFT:
                raise _refusal(
                    cls,
                    "release",
                    "evidence is written only while its release is a draft; release "
                    f"{release.pk} is {release.status}.",
                )
        assertions = {
            row["pk"]: row
            for row in EvidenceAssertion._base_manager.using(using)
            .filter(pk__in={row.assertion_id for row in rows} - {None})
            .values("pk", "release_id", *cls.ASSERTION_FIELDS)
        }
        for row in rows:
            assertion = assertions.get(row.assertion_id)
            if assertion is None:
                raise _refusal(
                    cls, "assertion", f"names no assertion: {row.assertion_id!r}."
                )
            if assertion["release_id"] != row.release_id:
                raise _other_release(
                    cls,
                    "assertion",
                    EvidenceAssertion,
                    row.assertion_id,
                    assertion["release_id"],
                    row.release_id,
                )
        cls.check_parts(rows, assertions, using, releases)

    @classmethod
    def check_parts(cls, rows, assertions, using, releases):
        """The kind's own rules, over all rows at once."""


class AssertionParticipant(AssertionPart):
    """One side of an assertion as the source stated it: the entity its
    identifier was mapped to, the identifier exactly as written, and how it
    was resolved (architecture §3.6).

    The relationship holds the canonical order. For an ordered type a side
    and its endpoint coincide; for a symmetric type the two participants are
    the relationship's endpoints as a multiset, in whichever order the
    source wrote them. When the source named an isoform and the record maps
    to its entry (open question B12), stated_entity keeps the isoform.
    """

    ASSERTION_FIELDS = (
        "relationship__subject_id",
        "relationship__object_id",
        "relationship__ordered",
    )

    assertion = GuardedForeignKey(
        EvidenceAssertion, on_delete=models.CASCADE, related_name="participants"
    )
    release = GuardedForeignKey(
        DatasetRelease, on_delete=models.CASCADE, related_name="+"
    )
    side = models.CharField(max_length=16, choices=ParticipantSide.choices)
    # The entity the source's identifier was mapped to.
    entity = GuardedForeignKey(Entity, on_delete=models.PROTECT, related_name="+")
    # The protein_isoform the source named, exactly when the basis is
    # isoform_entry.
    stated_entity = GuardedForeignKey(
        Entity, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    # Exactly as written.
    identifier_as_stated = models.CharField(max_length=255)
    resolution_basis = models.CharField(max_length=16, choices=ResolutionBasis.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["assertion", "side"], name="graph_part_one_per_side"
            ),
            models.CheckConstraint(
                check=Q(side__in=ParticipantSide.values), name="graph_part_side_valid"
            ),
            models.CheckConstraint(
                check=Q(resolution_basis__in=ResolutionBasis.values),
                name="graph_part_basis_valid",
            ),
            models.CheckConstraint(
                check=Q(
                    resolution_basis=ResolutionBasis.ISOFORM_ENTRY,
                    stated_entity__isnull=False,
                )
                | (
                    ~Q(resolution_basis=ResolutionBasis.ISOFORM_ENTRY)
                    & Q(stated_entity__isnull=True)
                ),
                name="graph_part_isoform_stated",
            ),
        ]

    def __str__(self):
        return f"{self.side} {self.identifier_as_stated}"

    @classmethod
    def check_parts(cls, rows, assertions, using, releases):
        for row in rows:
            _check_closed(cls, "side", row.side, ParticipantSide)
            _check_closed(
                cls, "resolution_basis", row.resolution_basis, ResolutionBasis
            )
            _check_text(cls, "identifier_as_stated", row.identifier_as_stated, 255)
            isoform = row.resolution_basis == ResolutionBasis.ISOFORM_ENTRY
            if isoform != (row.stated_entity_id is not None):
                raise _refusal(
                    cls,
                    "stated_entity",
                    "is the isoform the source named, set exactly when the "
                    "resolution basis is isoform_entry.",
                )
        # The sides already stored, then these rows: a symmetric pair is
        # compared once both sides exist.
        sides = defaultdict(dict)
        stored = (
            AssertionParticipant._base_manager.using(using)
            .filter(assertion_id__in=list(assertions))
            .values_list("assertion_id", "side", "entity_id")
        )
        for assertion_id, side, entity_id in stored:
            sides[assertion_id][side] = entity_id
        for row in rows:
            assertion = assertions[row.assertion_id]
            ends = (
                assertion["relationship__subject_id"],
                assertion["relationship__object_id"],
            )
            if assertion["relationship__ordered"]:
                expected = ends[0] if row.side == ParticipantSide.SUBJECT else ends[1]
                if row.entity_id != expected:
                    raise _refusal(
                        cls,
                        "entity",
                        f"the {row.side} of an ordered relationship is its {row.side}, "
                        f"entity {expected}; not entity {row.entity_id}.",
                    )
            elif row.entity_id not in ends:
                raise _refusal(
                    cls,
                    "entity",
                    f"is one of the relationship's endpoints, entities {ends[0]} "
                    f"and {ends[1]}; not entity {row.entity_id}.",
                )
            # A second row for one side is the unique constraint's to refuse.
            sides[row.assertion_id].setdefault(row.side, row.entity_id)
        for assertion_id, by_side in sides.items():
            assertion = assertions[assertion_id]
            if assertion["relationship__ordered"] or len(by_side) < 2:
                continue
            ends = sorted(
                (
                    assertion["relationship__subject_id"],
                    assertion["relationship__object_id"],
                )
            )
            named = sorted(by_side.values())
            if named != ends:
                raise _refusal(
                    cls,
                    "entity",
                    f"the participants of assertion {assertion_id} are its "
                    f"relationship's endpoints, entities {ends[0]} and {ends[1]}, "
                    f"as a multiset; they name entities {named[0]} and {named[1]}.",
                )
        cls._check_isoform_entries(
            [
                row
                for row in rows
                if row.resolution_basis == ResolutionBasis.ISOFORM_ENTRY
            ],
            using,
        )

    @classmethod
    def _check_isoform_entries(cls, rows, using):
        """isoform_entry maps a named protein_isoform to its protein entry."""
        if not rows:
            return
        ids = {row.entity_id for row in rows} | {row.stated_entity_id for row in rows}
        types = dict(
            Entity._base_manager.using(using)
            .filter(pk__in=ids)
            .values_list("pk", "entity_type_id")
        )
        for row in rows:
            for name, pk, wanted in (
                ("entity", row.entity_id, "protein"),
                ("stated_entity", row.stated_entity_id, "protein_isoform"),
            ):
                if types.get(pk) != wanted:
                    raise _refusal(
                        cls,
                        name,
                        f"of an isoform_entry participant is a {wanted}; entity "
                        f"{pk} is a {types.get(pk)}.",
                    )


class EvidenceMethod(AssertionPart):
    """A method the source names for the assertion: a PSI-MI detection or
    participant-identification method, or an ECO evidence code."""

    # The ontology each role's terms come from.
    ROLE_ONTOLOGIES = {
        MethodRole.DETECTION: "psi-mi",
        MethodRole.PARTICIPANT_IDENTIFICATION: "psi-mi",
        MethodRole.EVIDENCE_CODE: "eco",
    }

    assertion = GuardedForeignKey(
        EvidenceAssertion, on_delete=models.CASCADE, related_name="methods"
    )
    release = GuardedForeignKey(
        DatasetRelease, on_delete=models.CASCADE, related_name="+"
    )
    term = GuardedForeignKey(Term, on_delete=models.PROTECT, related_name="+")
    role = models.CharField(max_length=32, choices=MethodRole.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["assertion", "term", "role"], name="graph_evmethod_unique"
            ),
            models.CheckConstraint(
                check=Q(role__in=MethodRole.values), name="graph_evmethod_role_valid"
            ),
        ]

    def __str__(self):
        return f"{self.role} {self.term_id}"

    @classmethod
    def check_parts(cls, rows, assertions, using, releases):
        for row in rows:
            _check_closed(cls, "role", row.role, MethodRole)
        found = dict(
            Term._base_manager.using(using)
            .filter(pk__in={row.term_id for row in rows} - {None})
            .values_list("pk", "ontology")
        )
        for row in rows:
            if row.term_id not in found:
                raise _refusal(cls, "term", f"names no term: {row.term_id!r}.")
            wanted = cls.ROLE_ONTOLOGIES[row.role]
            if found[row.term_id] != wanted:
                raise _refusal(
                    cls,
                    "term",
                    f"a {row.role} method is a {wanted} term; {row.term_id} is "
                    f"{found[row.term_id]}.",
                )


class EvidencePublication(AssertionPart):
    """A paper the assertion cites, with its role. At most one paper reports
    an assertion (reported_in): a record that reports several gives one
    assertion per paper (graph.claims)."""

    assertion = GuardedForeignKey(
        EvidenceAssertion, on_delete=models.CASCADE, related_name="publications"
    )
    release = GuardedForeignKey(
        DatasetRelease, on_delete=models.CASCADE, related_name="+"
    )
    publication = GuardedForeignKey(
        Publication, on_delete=models.PROTECT, related_name="+"
    )
    role = models.CharField(max_length=24, choices=EvidencePublicationRole.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["assertion", "publication", "role"],
                name="graph_evpub_unique",
            ),
            models.UniqueConstraint(
                fields=["assertion"],
                condition=Q(role=EvidencePublicationRole.REPORTED_IN),
                name="graph_evpub_one_reported_in",
            ),
            models.CheckConstraint(
                check=Q(role__in=EvidencePublicationRole.values),
                name="graph_evpub_role_valid",
            ),
        ]

    def __str__(self):
        return f"{self.role} {self.publication_id}"

    @classmethod
    def check_parts(cls, rows, assertions, using, releases):
        for row in rows:
            _check_closed(cls, "role", row.role, EvidencePublicationRole)
        papers = {
            pk: (is_synthetic, dataset_id)
            for pk, is_synthetic, dataset_id in Publication._base_manager.using(using)
            .filter(pk__in={row.publication_id for row in rows} - {None})
            .values_list("pk", "is_synthetic", "local_dataset_id")
        }
        for row in rows:
            paper = papers.get(row.publication_id)
            if paper is None:
                raise _refusal(
                    cls,
                    "publication",
                    f"names no publication: {row.publication_id!r}.",
                )
            release = releases[row.release_id]
            is_synthetic, dataset_id = paper
            if is_synthetic != release.is_synthetic:
                raise _refusal(
                    cls,
                    "publication",
                    "a synthetic release cites only synthetic papers, and no other "
                    f"release cites one; publication {row.publication_id} is "
                    f"{'' if is_synthetic else 'not '}synthetic, release "
                    f"{release.pk} is {'' if release.is_synthetic else 'not '}"
                    "synthetic.",
                )
            if dataset_id is not None and dataset_id != release.dataset_id:
                raise _refusal(
                    cls,
                    "publication",
                    "a local publication is cited by its own dataset's releases; "
                    f"publication {row.publication_id} is local to dataset "
                    f"{dataset_id}, release {release.pk} is in dataset "
                    f"{release.dataset_id}.",
                )


class EvidenceScore(AssertionPart):
    """One score on an assertion, of a typed score type: the raw value
    verbatim, and its value as a finite float, or NULL when the raw value
    gives none (graph.scores). One per score type; types are never pooled."""

    assertion = GuardedForeignKey(
        EvidenceAssertion, on_delete=models.CASCADE, related_name="scores"
    )
    release = GuardedForeignKey(
        DatasetRelease, on_delete=models.CASCADE, related_name="+"
    )
    score_type = GuardedForeignKey(
        ScoreType, on_delete=models.PROTECT, related_name="+"
    )
    value = models.FloatField(null=True, blank=True)
    # Verbatim, never blank.
    raw_value = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["assertion", "score_type"], name="graph_score_one_per_type"
            ),
            models.CheckConstraint(
                check=_finite("value"), name="graph_score_value_finite"
            ),
        ]
        indexes = [
            models.Index(fields=["score_type", "value"], name="graph_score_type_value"),
        ]

    def __str__(self):
        return f"{self.score_type_id} {self.raw_value}"

    @classmethod
    def check_parts(cls, rows, assertions, using, releases):
        # graph.scores imports this module, so it is imported here.
        from .scores import score_value_problem

        type_ids = {row.score_type_id for row in rows} - {None}
        share_lock(ScoreType, using, type_ids)
        types = {
            row["slug"]: row
            for row in ScoreType.objects.using(using)
            .filter(pk__in=type_ids)
            .values("slug", "value_kind", "range_min", "range_max")
        }
        for row in rows:
            _check_text(cls, "raw_value", row.raw_value, 255)
            score_type = types.get(row.score_type_id)
            if score_type is None:
                raise _refusal(
                    cls, "score_type", f"names no score type: {row.score_type_id!r}."
                )
            release = releases[row.release_id]
            source = row.score_type_id.split(":", 1)[0]
            if release.is_synthetic and source not in ("synthetic", "stats"):
                raise _refusal(
                    cls,
                    "score_type",
                    "a synthetic release's scores are synthetic: or stats: types; "
                    f"{row.score_type_id} is neither.",
                )
            if not release.is_synthetic and source == "synthetic":
                raise _refusal(
                    cls,
                    "score_type",
                    f"synthetic: score types belong to synthetic releases; release "
                    f"{release.pk} is not synthetic.",
                )
            if row.value is None:
                continue
            value = row.value
            try:
                finite = (
                    not isinstance(value, bool)
                    and isinstance(value, (int, float))
                    and math.isfinite(value)
                )
            except OverflowError:
                finite = False
            if not finite:
                raise _refusal(
                    cls, "value", f"is a finite number, or NULL; not {value!r}."
                )
            problem = score_value_problem(
                score_type["value_kind"],
                score_type["range_min"],
                score_type["range_max"],
                value,
            )
            if problem:
                raise _refusal(cls, "value", f"{problem} ({row.score_type_id}).")


class EvidenceContext(AssertionPart):
    """One stated context of an assertion: a facet, with a term, a host
    taxon or neither, and the raw value as written. An absent facet means
    not stated, never all."""

    assertion = GuardedForeignKey(
        EvidenceAssertion, on_delete=models.CASCADE, related_name="contexts"
    )
    release = GuardedForeignKey(
        DatasetRelease, on_delete=models.CASCADE, related_name="+"
    )
    facet = models.CharField(max_length=24, choices=ContextFacet.choices)
    term = GuardedForeignKey(
        Term, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    # A registered organism, for host_taxon only.
    taxon = GuardedForeignKey(
        Taxon, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    label = models.CharField(max_length=255, blank=True)
    # As written, never blank.
    raw_value = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["assertion", "facet", "raw_value"],
                name="graph_context_unique",
            ),
            models.CheckConstraint(
                check=Q(facet__in=ContextFacet.values),
                name="graph_context_facet_valid",
            ),
            models.CheckConstraint(
                check=~Q(term__isnull=False, taxon__isnull=False),
                name="graph_context_term_or_taxon",
            ),
            models.CheckConstraint(
                check=Q(taxon__isnull=True) | Q(facet=ContextFacet.HOST_TAXON),
                name="graph_context_taxon_host",
            ),
        ]

    def __str__(self):
        return f"{self.facet} {self.raw_value}"

    @classmethod
    def check_parts(cls, rows, assertions, using, releases):
        for row in rows:
            _check_closed(cls, "facet", row.facet, ContextFacet)
            _check_text(cls, "raw_value", row.raw_value, 255)
            _check_text(cls, "label", row.label, 255, blank=True)
            host = row.facet == ContextFacet.HOST_TAXON
            if row.taxon_id is not None and not host:
                raise _refusal(
                    cls, "taxon", f"is set for host_taxon only; this is {row.facet}."
                )
            if host and row.term_id is not None:
                raise _refusal(
                    cls,
                    "term",
                    "a host taxon is a registered taxon or the raw value alone, "
                    "never a term.",
                )
            release = releases[row.release_id]
            if release.is_synthetic and row.taxon_id not in (None, SYNTHETIC_TAXON):
                raise _refusal(
                    cls,
                    "taxon",
                    "a synthetic release's host taxon is the synthetic construct "
                    f"({SYNTHETIC_TAXON}) or unregistered; not {row.taxon_id}.",
                )
            if not release.is_synthetic and row.taxon_id == SYNTHETIC_TAXON:
                raise _refusal(
                    cls,
                    "taxon",
                    f"the synthetic construct ({SYNTHETIC_TAXON}) is the host taxon "
                    f"of synthetic releases only; release {release.pk} is not "
                    "synthetic.",
                )

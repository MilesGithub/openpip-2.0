"""Typed entities and their identifiers, the registries they are typed by, and
the provenance and licensing records of the releases that bring data in.

These tables sit beside the PPI tables and never reference them: the only
foreign keys that leave this app point at `user`, for who decided, recorded
or triggered something (SET_NULL, with the username kept beside each). A key
into `protein` or `dataset` would be emptied by `migrate_legacy.py --reset`,
which truncates with CASCADE.
"""

import contextvars
import re
from functools import lru_cache

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import models, router, transaction
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


class EntityQuerySet(models.QuerySet):
    """Entity's default queryset: an update() never moves created_by_release
    off the release that introduced an entity. Release.created_entities'
    remove() and clear() and a bulk_update() go through it."""

    def update(self, **kwargs):
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
        # As GuardedQuerySet.bulk_update: a savepoint, so that a refusal
        # leaves the caller's transaction usable.
        self._for_write = True
        with transaction.atomic(using=self.db):
            return super().bulk_update(objs, fields, batch_size=batch_size)

    bulk_update.alters_data = True


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
        names = {"created_by_release", "created_by_release_id"}
        if (
            self.pk is None
            or force_insert
            or (update_fields is not None and not names & set(update_fields))
            or "created_by_release_id" in self.get_deferred_fields()
        ):
            return super().save(
                force_insert=force_insert,
                force_update=force_update,
                using=using,
                update_fields=update_fields,
            )
        using = using or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            held = (
                Entity._base_manager.using(using)
                .select_for_update()
                .filter(pk=self.pk)
                .values_list("created_by_release_id", flat=True)
                .first()
            )
            if held is not None and held != self.created_by_release_id:
                raise _refusal(self, "created_by_release", _introduced_rule(self.pk))
            return super().save(
                force_insert=force_insert,
                force_update=force_update,
                using=using,
                update_fields=update_fields,
            )

    save.alters_data = True


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


def _differs(field, new, old) -> bool:
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
        .only("status", "store_raw_rows")
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
            raise _refusal(
                model,
                name,
                f"belongs to the row's own release; {target.__name__} {target_id} "
                f"is in release {owner}, not {row.release_id}.",
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
    or guard_update(). delete() asks guard_delete(). A hook refuses with
    ImmutableRecordError before anything is written. The defaults allow an
    insert and refuse every other write; each model allows what its rules
    allow.
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
            locked = self.lock_for_write(using)
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
                changed = self._changed(stored, written)
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
            self.guard_delete(using)
            return super().delete(using=using, keep_parents=keep_parents)

    delete.alters_data = True

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

    def _changed(self, stored, written) -> frozenset:
        """The written fields whose value differs from the stored row's,
        leaving out the auto_now bookkeeping."""
        changed = set()
        for name in written:
            field = self._meta.get_field(name)
            if getattr(field, "auto_now", False):
                continue
            new = getattr(self, field.attname)
            if _differs(field, new, getattr(stored, field.attname)):
                changed.add(name)
        return frozenset(changed)

    def _after(self, name, stored, written):
        """The value the row holds once this write is done."""
        attname = self._meta.get_field(name).attname
        if stored is None or name in written:
            return getattr(self, attname)
        return getattr(stored, attname)

    # ── Hooks ──

    def lock_for_write(self, using):
        """Lock the other rows whose state decides this write, before the
        row itself is read. Returns what the guards need of them."""
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

    def lock_for_write(self, using):
        return _locked_releases(using, [self.release_id]).get(self.release_id)

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
    merge fills it, never from one value to another.
    """

    IDENTIFIER_FIELDS = ("pmid", "doi", "pmcid", "imex_id")
    SET_ONCE_FIELDS = frozenset(
        {*IDENTIFIER_FIELDS, "local_key", "local_dataset", "is_synthetic"}
    )

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
    """

    MUTABLE_FIELDS = frozenset({"status", "issues"})
    WRITABLE = frozenset({ReleaseStatus.DRAFT, ReleaseStatus.STAGED})
    _WRITABLE_RULE = "records are written only while their release is a draft or staged"
    _CONTENT = (
        "is the record as read and never changes; only its status and issues "
        "follow the latest run."
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

    @classmethod
    def guard_queryset_update(cls, queryset, names):
        content = names - cls.MUTABLE_FIELDS - {"updated_at"}
        if content:
            raise _refusal(cls, min(content), cls._CONTENT)
        ids = _release_ids(queryset)
        _require_releases(cls, queryset.db, ids, cls.WRITABLE, cls._WRITABLE_RULE)
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

    def lock_for_write(self, using):
        # A record created complete is confirmed when it is written. Locking
        # the dataset first serializes the creation with
        # licensing.record_licence, which locks it too, so that confirmation
        # never predates a snapshot it could not see.
        if self.pk is None:
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
        actor = self._changed_by
        if (
            actor is None
            or getattr(actor, "pk", None) is None
            or not (actor.is_active and actor.is_staff)
        ):
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

    def lock_for_write(self, using):
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

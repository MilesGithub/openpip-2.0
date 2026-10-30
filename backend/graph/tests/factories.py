import datetime
import hashlib
import json

import factory
from django.conf import settings
from django.utils import timezone
from graph.identity import normalize, scope_key
from graph.models import (
    ApprovalScope,
    CasePolicy,
    DataSource,
    DataSourceKind,
    DatasetRelease,
    Decision,
    DueDiligenceChange,
    DueDiligenceRecord,
    DueDiligenceStatus,
    Entity,
    EntityIdentifier,
    EntityType,
    FileRole,
    IdentifierNamespace,
    IdentifierRole,
    ImportMode,
    ImportRun,
    Permission,
    Publication,
    PublicationRole,
    RecordKeyBasis,
    RecordStatus,
    ReleaseLicence,
    ReleasePublication,
    ReleaseStatus,
    ReleaseTaxon,
    Scope,
    ShareAlike,
    SourceDataset,
    SourceDatasetPublication,
    SourceFile,
    SourceRecord,
    Taxon,
    TaxonPolicy,
    UsageApproval,
    VersionPolicy,
)


class TaxonFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Taxon
        django_get_or_create = ("taxid",)

    taxid = factory.Sequence(lambda n: 999_990_000 + n)
    scientific_name = factory.Sequence(lambda n: f"Testus species{n}")
    rank = "species"


class EntityTypeFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = EntityType
        django_get_or_create = ("slug",)

    slug = factory.Sequence(lambda n: f"test_type_{n}")
    label = factory.LazyAttribute(lambda o: o.slug)
    definition = "Test only."
    taxon_policy = TaxonPolicy.REQUIRED
    identity_scheme = "identifier"


class IdentifierNamespaceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = IdentifierNamespace
        django_get_or_create = ("slug",)
        # Setting the many-to-many below needs no second save.
        skip_postgeneration_save = True

    slug = factory.Sequence(lambda n: f"test_ns_{n}")
    label = factory.LazyAttribute(lambda o: o.slug)
    curie_prefix = factory.LazyAttribute(lambda o: o.slug)
    scope = Scope.GLOBAL
    case_policy = CasePolicy.EXACT
    version_policy = VersionPolicy.NONE
    pattern = "[A-Za-z0-9][A-Za-z0-9.:-]*"
    authority = "Test only."

    @factory.post_generation
    def allowed_entity_types(self, create, extracted, **kwargs):
        if create and extracted:
            self.allowed_entity_types.set(extracted)


class EntityFactory(factory.django.DjangoModelFactory):
    """An entity row written directly, without the checks in create_entity.

    A human gene unless told otherwise; the type and taxon rows are the
    registry's when it has been synced.
    """

    class Meta:
        model = Entity

    entity_type = factory.SubFactory(EntityTypeFactory, slug="gene")
    identity_key = factory.Sequence(lambda n: f"test||entity-{n}")
    label = factory.Sequence(lambda n: f"Entity {n}")
    label_key = factory.LazyAttribute(lambda o: o.label.upper())
    taxon = factory.SubFactory(TaxonFactory, taxid=9606, scientific_name="Homo sapiens")


class EntityIdentifierFactory(factory.django.DjangoModelFactory):
    """An identifier row, normalized and scoped by its namespace's rules.

    Until RF1a nothing imports a non-primary identifier, so these rows stand
    in for the cross-references, secondary accessions and aliases a reference
    release will state.
    """

    class Meta:
        model = EntityIdentifier

    entity = factory.SubFactory(EntityFactory)
    namespace = factory.SubFactory(IdentifierNamespaceFactory)
    value = factory.Sequence(lambda n: f"ID{n}")
    role = IdentifierRole.ALIAS
    lookup = factory.LazyAttribute(lambda o: normalize(o.namespace, o.value)[0])
    version = factory.LazyAttribute(lambda o: normalize(o.namespace, o.value)[1])
    scope_key = factory.LazyAttribute(
        lambda o: scope_key(o.namespace, o.entity.taxon_id)
    )


# ── Accounts ────────────────────────────────────────────────────────────────


class StaffUserFactory(factory.django.DjangoModelFactory):
    """An active staff account: the actor the record rules and the services
    ask for."""

    class Meta:
        model = settings.AUTH_USER_MODEL

    username = factory.Sequence(lambda n: f"graph-staff-{n}")
    is_staff = True
    is_active = True
    # Unusable, so no password is hashed per account; tests log in with
    # force_login.
    password = "!"


# ── Provenance ──────────────────────────────────────────────────────────────


class DataSourceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = DataSource

    slug = factory.Sequence(lambda n: f"test-source-{n}")
    name = factory.Sequence(lambda n: f"Test source {n}")
    kind = DataSourceKind.LAB


class SourceDatasetFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = SourceDataset

    slug = factory.Sequence(lambda n: f"test-dataset-{n}")
    title = factory.Sequence(lambda n: f"Test dataset {n}")
    data_source = factory.SubFactory(DataSourceFactory)


class PublicationFactory(factory.django.DjangoModelFactory):
    """A paper named by a PMID unless told otherwise. The numbers are far
    above any PMID PubMed has assigned."""

    class Meta:
        model = Publication

    pmid = factory.Sequence(lambda n: str(90_000_000 + n))


class SourceDatasetPublicationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = SourceDatasetPublication

    dataset = factory.SubFactory(SourceDatasetFactory)
    publication = factory.SubFactory(PublicationFactory)
    role = PublicationRole.PRIMARY


PUBLISHED_STATUSES = (ReleaseStatus.PUBLISHED, ReleaseStatus.SUPERSEDED)


class DatasetReleaseFactory(factory.django.DjangoModelFactory):
    """A release, written directly in any status. The fields its status needs
    are filled in (published_at and its username; the withdrawn fields). A
    release withdrawn after its publication needs published_at and
    published_by_username given."""

    class Meta:
        model = DatasetRelease

    dataset = factory.SubFactory(SourceDatasetFactory)
    version = factory.Sequence(lambda n: f"v{n}")
    status = ReleaseStatus.DRAFT
    published_at = factory.LazyAttribute(
        lambda o: timezone.now() if o.status in PUBLISHED_STATUSES else None
    )
    published_by_username = factory.LazyAttribute(
        lambda o: "graph-staff" if o.published_at else ""
    )
    withdrawn_at = factory.LazyAttribute(
        lambda o: timezone.now() if o.status == ReleaseStatus.WITHDRAWN else None
    )
    withdrawn_reason = factory.LazyAttribute(
        lambda o: "Test only." if o.status == ReleaseStatus.WITHDRAWN else ""
    )
    withdrawn_by_username = factory.LazyAttribute(
        lambda o: "graph-staff" if o.status == ReleaseStatus.WITHDRAWN else ""
    )


class ReleasePublicationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ReleasePublication

    release = factory.SubFactory(DatasetReleaseFactory)
    publication = factory.SubFactory(PublicationFactory)
    role = PublicationRole.DESCRIBES


class ReleaseTaxonFactory(factory.django.DjangoModelFactory):
    """A declared taxon. Its release must be a draft."""

    class Meta:
        model = ReleaseTaxon

    release = factory.SubFactory(DatasetReleaseFactory)
    taxon = factory.SubFactory(TaxonFactory)


class SourceFileFactory(factory.django.DjangoModelFactory):
    """A release file. Its release must be a draft that no one has decided
    on."""

    class Meta:
        model = SourceFile

    release = factory.SubFactory(DatasetReleaseFactory)
    role = FileRole.DATA
    original_name = factory.Sequence(lambda n: f"release_{n}.tsv")
    sha256 = factory.Sequence(
        lambda n: hashlib.sha256(f"file {n}".encode()).hexdigest()
    )
    storage_path = factory.LazyAttribute(
        lambda o: f"releases/{o.release.pk}/{o.sha256}/{o.original_name}"
    )
    size = 1024


class ImportRunFactory(factory.django.DjangoModelFactory):
    """A dry run reading a file of its release, unless told otherwise. A
    commit run's release must be a draft."""

    class Meta:
        model = ImportRun

    release = factory.SubFactory(DatasetReleaseFactory)
    file = factory.SubFactory(
        SourceFileFactory, release=factory.SelfAttribute("..release")
    )
    adapter = "test_adapter"
    adapter_version = "1"
    mode = ImportMode.DRY_RUN
    triggered_by_username = "graph-staff"


def raw_sha256(raw) -> str:
    return hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()


class SourceRecordFactory(factory.django.DjangoModelFactory):
    """A record read by a run of its release from one of the release's files.
    The file and run are created too unless given, which needs a draft
    release; the record itself needs a draft or staged one."""

    class Meta:
        model = SourceRecord

    release = factory.SubFactory(DatasetReleaseFactory)
    file = factory.SubFactory(
        SourceFileFactory, release=factory.SelfAttribute("..release")
    )
    import_run = factory.SubFactory(
        ImportRunFactory,
        release=factory.SelfAttribute("..release"),
        file=factory.SelfAttribute("..file"),
    )
    record_key = factory.Sequence(lambda n: f"record-{n}")
    record_key_basis = RecordKeyBasis.SOURCE_ID
    line_number = factory.Sequence(lambda n: n + 2)
    raw = factory.LazyAttribute(lambda o: {"id": o.record_key, "regulator": "SYN-A"})
    raw_sha256 = factory.LazyAttribute(lambda o: raw_sha256(o.raw))
    status = RecordStatus.MAPPED


# ── Licensing ───────────────────────────────────────────────────────────────


class DueDiligenceRecordFactory(factory.django.DjangoModelFactory):
    """A dataset's due-diligence record, saved through its rules: creating
    one is an audited change, so it names an actor, changed_by (a new staff
    account unless given), and writes the first history row. Its reviewer
    and notes link are set, so status=complete works as given."""

    class Meta:
        model = DueDiligenceRecord

    dataset = factory.SubFactory(SourceDatasetFactory)
    status = DueDiligenceStatus.OPEN
    reviewer = "Test reviewer"
    notes_link = "docs/project/generalization/DATASET_DUE_DILIGENCE.md"
    changed_by = factory.SubFactory(StaffUserFactory)
    change_note = ""

    @classmethod
    def _build(cls, model_class, *args, changed_by=None, change_note="", **kwargs):
        record = model_class(*args, **kwargs)
        record._changed_by = changed_by
        record._change_note = change_note
        return record

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        record = cls._build(model_class, *args, **kwargs)
        record.save()
        return record


class DueDiligenceChangeFactory(factory.django.DjangoModelFactory):
    """A history row written directly. It states its record's current
    values, as the record's own save() writes them."""

    class Meta:
        model = DueDiligenceChange

    record = factory.SubFactory(DueDiligenceRecordFactory)
    status = factory.SelfAttribute("record.status")
    terms_time_limited = factory.SelfAttribute("record.terms_time_limited")
    reviewer = factory.SelfAttribute("record.reviewer")
    reviewed_on = factory.SelfAttribute("record.reviewed_on")
    notes_link = factory.SelfAttribute("record.notes_link")
    completed_at = factory.SelfAttribute("record.completed_at")
    changed_by = factory.SubFactory(StaffUserFactory)


class ReleaseLicenceFactory(factory.django.DjangoModelFactory):
    """A licence snapshot. Every term is unknown or blank unless a test
    states it, as real snapshots are until a person records them.

    recorded=True: terms stated, with where and when they were read, as an
    approval needs (ReleaseLicence.records_terms).
    open_terms=True: recorded, with redistribution, modification and
    commercial use permitted and no ShareAlike: what every scope's approval
    needs.
    """

    class Meta:
        model = ReleaseLicence

    class Params:
        recorded = factory.Trait(
            licence_name="Test licence 1.0",
            licence_url="https://example.org/licence",
            read_at="https://example.org/terms",
            read_on=datetime.date(2026, 10, 1),
        )
        open_terms = factory.Trait(
            recorded=True,
            redistribution=Permission.PERMITTED,
            commercial_use=Permission.PERMITTED,
            modification=Permission.PERMITTED,
            share_alike=ShareAlike.NO,
        )

    release = factory.SubFactory(DatasetReleaseFactory)
    recorded_by = factory.SubFactory(StaffUserFactory)


class UsageApprovalFactory(factory.django.DjangoModelFactory):
    """An approved internal decision relying on an open-terms snapshot of the
    same release, with a rationale and evidence, decided by a staff
    account."""

    class Meta:
        model = UsageApproval

    release = factory.SubFactory(DatasetReleaseFactory)
    scope = ApprovalScope.INTERNAL
    decision = Decision.APPROVED
    licence = factory.SubFactory(
        ReleaseLicenceFactory,
        release=factory.SelfAttribute("..release"),
        open_terms=True,
    )
    rationale = "Test only."
    evidence = "https://example.org/evidence"
    decided_by = factory.SubFactory(StaffUserFactory)


# ── Releases ready for approval ─────────────────────────────────────────────
# Written directly, through the record rules but past the services, so tests
# can set up any state; the services' own rules are what the tests exercise.


def confirm_diligence(dataset):
    """The dataset's due diligence, complete and confirmed now: created so,
    or confirmed again as licensing.confirm_due_diligence would."""
    record = DueDiligenceRecord.objects.filter(dataset=dataset).first()
    if record is None:
        return DueDiligenceRecordFactory(
            dataset=dataset, status=DueDiligenceStatus.COMPLETE
        )
    record.status = DueDiligenceStatus.COMPLETE
    record._changed_by = StaffUserFactory()
    record._reconfirm = True
    record.save()
    return record


def approvable_release(*, terms=None, **fields):
    """A release that may be approved for every scope: an open-terms
    snapshot (with `terms` over it), then its dataset's due diligence
    confirmed after it. Any status, as DatasetReleaseFactory."""
    release = DatasetReleaseFactory(**fields)
    ReleaseLicenceFactory(release=release, open_terms=True, **(terms or {}))
    confirm_diligence(release.dataset)
    return release


def approve(release, *scopes, decided_by=None):
    """An active approval of each scope, relying on the release's latest
    snapshot."""
    licence = ReleaseLicence.objects.filter(release=release).order_by("-pk").first()
    return [
        UsageApprovalFactory(
            release=release,
            scope=scope,
            licence=licence,
            decided_by=decided_by or StaffUserFactory(),
        )
        for scope in scopes
    ]

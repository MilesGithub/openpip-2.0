"""Provenance records: sources, datasets, releases, declared taxa, files,
import runs and source records, and the record rules that hold on every
write path."""

import datetime
import json
import threading
import time

import pytest
from django.apps import apps
from django.db import IntegrityError, connection, transaction
from django.db.models import F, ProtectedError, QuerySet, RestrictedError
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from graph.approvals import revoke as revoke_decision
from graph.entities import EntityError, ReleaseProvenanceError, create_entity
from graph.models import (
    ApprovalScope,
    AssertionParticipant,
    DataSource,
    DataSourceKind,
    DatasetRelease,
    DatasetStatus,
    Decision,
    DueDiligenceChange,
    DueDiligenceRecord,
    DueDiligenceStatus,
    Entity,
    EntityIdentifier,
    EntityStatus,
    EvidenceAssertion,
    EvidenceContext,
    EvidenceMethod,
    EvidencePublication,
    EvidenceScore,
    FileRole,
    GuardedForeignKey,
    GuardedQuerySet,
    ImmutableRecordError,
    ImportMode,
    ImportRun,
    ImportState,
    Permission,
    Publication,
    PublicationRole,
    RecordKeyBasis,
    RecordStatus,
    Relationship,
    ReleaseKind,
    ReleaseLicence,
    ReleaseStatus,
    ReleaseTaxon,
    ShareAlike,
    SourceDataset,
    SourceFile,
    SourceRecord,
    Term,
    UsageApproval,
)
from graph.tests.factories import (
    AssertionParticipantFactory,
    DataSourceFactory,
    DatasetReleaseFactory,
    DueDiligenceRecordFactory,
    EntityFactory,
    EvidenceAssertionFactory,
    EvidenceContextFactory,
    EvidenceMethodFactory,
    EvidencePublicationFactory,
    EvidenceScoreFactory,
    ImportRunFactory,
    PublicationFactory,
    RelationshipFactory,
    ReleaseLicenceFactory,
    ReleasePublicationFactory,
    ReleaseTaxonFactory,
    SourceDatasetFactory,
    SourceDatasetPublicationFactory,
    SourceFileFactory,
    SourceRecordFactory,
    StaffUserFactory,
    TaxonFactory,
    TermFactory,
    UsageApprovalFactory,
    approvable_release,
    approve,
    mapped_record,
)
from graph.visibility import Purpose, visible_release_ids

DRAFT = ReleaseStatus.DRAFT
STAGED = ReleaseStatus.STAGED
PUBLISHED = ReleaseStatus.PUBLISHED
SUPERSEDED = ReleaseStatus.SUPERSEDED
WITHDRAWN = ReleaseStatus.WITHDRAWN
STATUSES = [DRAFT, STAGED, PUBLISHED, SUPERSEDED, WITHDRAWN]
LATER = datetime.datetime(2027, 1, 1, tzinfo=datetime.timezone.utc)

GUARDED_MODELS = [
    DataSource,
    SourceDataset,
    Publication,
    DatasetRelease,
    ReleaseTaxon,
    SourceFile,
    ImportRun,
    SourceRecord,
    DueDiligenceRecord,
    DueDiligenceChange,
    ReleaseLicence,
    UsageApproval,
    # G3.
    Term,
    Relationship,
    EvidenceAssertion,
    AssertionParticipant,
    EvidenceMethod,
    EvidencePublication,
    EvidenceScore,
    EvidenceContext,
]
G2_MODELS = {
    "DataSource",
    "SourceDataset",
    "Publication",
    "SourceDatasetPublication",
    "DatasetRelease",
    "ReleasePublication",
    "ReleaseTaxon",
    "SourceFile",
    "ImportRun",
    "SourceRecord",
    "DueDiligenceRecord",
    "DueDiligenceChange",
    "ReleaseLicence",
    "UsageApproval",
}
G3_MODELS = {
    "RelationshipType",
    "ScoreType",
    "Term",
    "Relationship",
    "EvidenceAssertion",
    "AssertionParticipant",
    "EvidenceMethod",
    "EvidencePublication",
    "EvidenceScore",
    "EvidenceContext",
}
# Ways to write a change to a stored row, and to delete one.
WRITE_PATHS = ["save", "save_update_fields", "update", "bulk_update"]
DELETE_PATHS = ["delete", "queryset_delete"]


def stored(obj) -> dict:
    """The row as the database holds it."""
    return type(obj)._base_manager.filter(pk=obj.pk).values().get()


def write(path, obj, **changes):
    """Write these changes to a stored row through one path."""
    model = type(obj)
    if path in ("save", "save_update_fields", "bulk_update"):
        for name, value in changes.items():
            setattr(obj, name, value)
    if path == "save":
        obj.save()
    elif path == "save_update_fields":
        obj.save(update_fields=list(changes))
    elif path == "update":
        model.objects.filter(pk=obj.pk).update(**changes)
    elif path == "bulk_update":
        model.objects.bulk_update([obj], list(changes))
    else:
        raise ValueError(path)


def delete(path, obj):
    if path == "delete":
        obj.delete()
    elif path == "queryset_delete":
        type(obj).objects.filter(pk=obj.pk).delete()
    else:
        raise ValueError(path)


def insert(path, obj):
    if path == "save":
        obj.save()
    elif path == "bulk_create":
        type(obj).objects.bulk_create([obj])
    else:
        raise ValueError(path)


def move(release, status):
    """One status transition, saved, with the fields it needs. Publishing and
    superseding are publish_release's to save, which tests here stand in
    for."""
    release.status = status
    release._lifecycle = status in (PUBLISHED, SUPERSEDED)
    if status == PUBLISHED:
        release.published_at = timezone.now()
        release.published_by_username = "graph-staff"
    if status == WITHDRAWN:
        release.withdrawn_at = timezone.now()
        release.withdrawn_reason = "Test only."
        release.withdrawn_by_username = "graph-staff"
    release.save()


# How a draft reaches each status by the allowed transitions.
ROUTE = {
    DRAFT: (),
    STAGED: (STAGED,),
    PUBLISHED: (STAGED, PUBLISHED),
    SUPERSEDED: (STAGED, PUBLISHED, SUPERSEDED),
    WITHDRAWN: (STAGED, WITHDRAWN),
}


def advance(release, status):
    for step in ROUTE[status]:
        move(release, step)
    return release


# ── The record rules' mechanism ─────────────────────────────────────────────


@pytest.mark.parametrize("model", GUARDED_MODELS, ids=lambda m: m.__name__)
def test_the_default_manager_is_guarded_and_the_base_manager_stays_plain(model):
    # Model.save() and the deletion collector write through the base manager.
    assert type(model._default_manager.all()) is GuardedQuerySet
    assert type(model._base_manager.all()) is QuerySet
    assert model._meta.base_manager_name is None
    # As with Django's own managers, no delete() of every row.
    assert not hasattr(model.objects, "delete")


def _approval_elsewhere():
    approval = UsageApprovalFactory()
    return DatasetReleaseFactory(dataset=approval.release.dataset), approval


def _licence_elsewhere():
    approval = UsageApprovalFactory()
    other = ReleaseLicenceFactory(release=approval.release, open_terms=True)
    return other, approval


def _file_of(release):
    return SourceFileFactory(release=release, sha256="c" * 64)


def _local_publication():
    publication = Publication(local_dataset=SourceDatasetFactory(), local_key="p1")
    publication.save()
    return SourceDatasetFactory(), publication


def _history_row():
    change = DueDiligenceRecordFactory().history.get()
    return DueDiligenceRecordFactory(), change


def _correction():
    target = DatasetReleaseFactory(status=PUBLISHED)
    staged = DatasetReleaseFactory(dataset=target.dataset, status=STAGED)
    return target, staged


def _run_of(record):
    return ImportRunFactory(release=record.release, file=record.file)


def _assertion_elsewhere():
    return RelationshipFactory(), EvidenceAssertionFactory()


def _part_elsewhere(factory_class):
    def make():
        part = factory_class()
        return EvidenceAssertionFactory(release=part.release), part

    return make


# Each reverse many-to-one accessor of a model with record rules: the parent
# it is read from, the row given to add(), and the refusal expected. Each
# moves a stored row to another parent.
REVERSE_ACCESSORS = {
    "dataset.releases": (
        lambda: (SourceDatasetFactory(), DatasetReleaseFactory(status=PUBLISHED)),
        "releases",
        "DatasetRelease.dataset",
    ),
    "release.corrections": (_correction, "corrections", "DatasetRelease.supersedes"),
    "release.release_taxa": (
        lambda: (DatasetReleaseFactory(), ReleaseTaxonFactory()),
        "release_taxa",
        "ReleaseTaxon",
    ),
    "taxon.release_taxa": (
        lambda: (TaxonFactory(), ReleaseTaxonFactory()),
        "release_taxa",
        "ReleaseTaxon",
    ),
    "release.files": (
        lambda: (DatasetReleaseFactory(), SourceFileFactory()),
        "files",
        "SourceFile",
    ),
    "release.import_runs": (
        lambda: (DatasetReleaseFactory(), ImportRunFactory()),
        "import_runs",
        "ImportRun.release",
    ),
    "file.import_runs": (
        lambda: (lambda run: (_file_of(run.release), run))(ImportRunFactory()),
        "import_runs",
        "ImportRun.file",
    ),
    "release.records": (
        lambda: (DatasetReleaseFactory(), SourceRecordFactory()),
        "records",
        "SourceRecord.release",
    ),
    "file.records": (
        lambda: (lambda record: (_file_of(record.release), record))(
            SourceRecordFactory()
        ),
        "records",
        "SourceRecord.file",
    ),
    "run.records": (
        lambda: (lambda record: (_run_of(record), record))(SourceRecordFactory()),
        "records",
        "SourceRecord.import_run",
    ),
    "record.history": (_history_row, "history", "DueDiligenceChange"),
    "release.licences": (
        lambda: (DatasetReleaseFactory(), ReleaseLicenceFactory()),
        "licences",
        "ReleaseLicence",
    ),
    "release.approvals": (_approval_elsewhere, "approvals", "UsageApproval.release"),
    "licence.approvals": (_licence_elsewhere, "approvals", "UsageApproval.licence"),
    "dataset.local_publications": (
        _local_publication,
        "local_publications",
        "Publication.local_dataset",
    ),
    "release.created_entities": (
        lambda: (
            DatasetReleaseFactory(),
            EntityFactory(created_by_release=DatasetReleaseFactory()),
        ),
        "created_entities",
        "Entity.created_by_release",
    ),
    "relationship.assertions": (
        _assertion_elsewhere,
        "assertions",
        "EvidenceAssertion.relationship",
    ),
    "assertion.participants": (
        _part_elsewhere(AssertionParticipantFactory),
        "participants",
        "AssertionParticipant.assertion",
    ),
    "assertion.methods": (
        _part_elsewhere(EvidenceMethodFactory),
        "methods",
        "EvidenceMethod.assertion",
    ),
    "assertion.publications": (
        _part_elsewhere(EvidencePublicationFactory),
        "publications",
        "EvidencePublication.assertion",
    ),
    "assertion.scores": (
        _part_elsewhere(EvidenceScoreFactory),
        "scores",
        "EvidenceScore.assertion",
    ),
    "assertion.contexts": (
        _part_elsewhere(EvidenceContextFactory),
        "contexts",
        "EvidenceContext.assertion",
    ),
}


@pytest.mark.parametrize("method", ["add", "set", "add_bulk_false"])
@pytest.mark.parametrize("accessor", sorted(REVERSE_ACCESSORS))
def test_a_reverse_foreign_key_manager_moves_no_row_past_its_rules(
    db, accessor, method
):
    """Django's reverse add() (and set(), which calls it) writes with one
    update() through the plain base manager. Here it saves each row, so its
    rules refuse, and the caller's transaction stays usable."""
    make, name, refusal = REVERSE_ACCESSORS[accessor]
    parent, row = make()
    before = stored(row)
    manager = getattr(parent, name)
    with pytest.raises(ImmutableRecordError, match=refusal):
        if method == "add":
            manager.add(row)
        elif method == "set":
            manager.set([row])
        else:
            manager.add(row, bulk=False)
    assert stored(row) == before
    assert not getattr(parent, name).filter(pk=row.pk).exists()


def test_every_reverse_many_to_one_accessor_of_a_guarded_model_saves_each_row():
    keys = set()
    for model in apps.get_app_config("graph").get_models():
        for rel in model._meta.get_fields():
            if not (rel.one_to_many and rel.auto_created):
                continue
            if rel.related_model not in GUARDED_MODELS:
                continue
            assert isinstance(rel.field, GuardedForeignKey), rel
            keys.add(f"{rel.related_model.__name__}.{rel.field.name}")
    # G1's own keys on Entity stay plain; created_by_release is G2's.
    assert isinstance(Entity._meta.get_field("created_by_release"), GuardedForeignKey)
    assert keys == {
        "SourceDataset.data_source",
        "Publication.local_dataset",
        "DatasetRelease.dataset",
        "DatasetRelease.supersedes",
        "ReleaseTaxon.release",
        "ReleaseTaxon.taxon",
        "SourceFile.release",
        "ImportRun.release",
        "ImportRun.file",
        "SourceRecord.release",
        "SourceRecord.file",
        "SourceRecord.import_run",
        "DueDiligenceChange.record",
        "ReleaseLicence.release",
        "UsageApproval.release",
        "UsageApproval.licence",
        # G3: every other key of its models has related_name="+".
        "EvidenceAssertion.relationship",
        "AssertionParticipant.assertion",
        "EvidenceMethod.assertion",
        "EvidencePublication.assertion",
        "EvidenceScore.assertion",
        "EvidenceContext.assertion",
    }
    # Each is refused above but data_source (a catalogue dataset may move),
    # and Entity.created_by_release is too.
    assert len(REVERSE_ACCESSORS) == len(keys)
    # The migrations see a plain ForeignKey.
    path = DatasetRelease._meta.get_field("dataset").deconstruct()[1]
    assert path == "django.db.models.ForeignKey"
    for name in ("entity_type", "taxon"):
        field = Entity._meta.get_field(name)
        assert isinstance(field, GuardedForeignKey), name
        assert field.deconstruct()[1] == "django.db.models.ForeignKey"


@pytest.mark.parametrize("model", GUARDED_MODELS, ids=lambda m: m.__name__)
def test_every_foreign_key_on_a_guarded_model_saves_each_row_or_has_no_manager(
    model,
):
    """A reverse manager's add() would move a row past its rules with one
    plain update(); a GuardedForeignKey's saves each row, and a key with
    related_name="+" has none."""
    for field in model._meta.get_fields():
        if field.many_to_one and field.concrete:
            assert (
                isinstance(field, GuardedForeignKey) or field.remote_field.is_hidden()
            ), f"{model.__name__}.{field.name}"


def test_a_reverse_add_still_writes_what_the_rules_allow(db):
    # A draft may name the release it corrects, through either side.
    target = DatasetReleaseFactory(status=PUBLISHED)
    draft = DatasetReleaseFactory(dataset=target.dataset)
    target.corrections.add(draft)
    assert stored(draft)["supersedes_id"] == target.pk
    # A catalogue dataset moves to another source.
    dataset = SourceDatasetFactory()
    source = DataSourceFactory()
    source.datasets.set([dataset])
    assert stored(dataset)["data_source_id"] == source.pk
    # An entity without one is given the release that introduced it.
    release, entity = DatasetReleaseFactory(), EntityFactory()
    release.created_entities.add(entity)
    assert stored(entity)["created_by_release_id"] == release.pk


def test_a_reverse_add_makes_no_release_public(switch_on):
    public = approvable_release(status=PUBLISHED)
    approve(public, ApprovalScope.INTERNAL, ApprovalScope.PUBLIC_VIEW)
    other = DatasetReleaseFactory(dataset=public.dataset, status=PUBLISHED)
    ReleaseLicenceFactory(release=other, redistribution=Permission.NOT_PERMITTED)
    with pytest.raises(ImmutableRecordError, match="UsageApproval.release"):
        other.approvals.add(*UsageApproval.objects.filter(release=public))
    assert visible_release_ids(None, Purpose.VIEW).ids == {public.pk}
    assert not other.approvals.exists()


def test_every_constraint_and_index_name_g2_and_g3_add_is_prefixed_and_short():
    # G1's graph_entity_merged_into_iff_merged is longer, and stays as
    # migrated.
    g1 = {"Taxon", "EntityType", "IdentifierNamespace", "RegistryState"}
    g1 |= {"Entity", "EntityIdentifier"}
    models = [
        model
        for model in apps.get_app_config("graph").get_models()
        if model.__name__ not in g1
    ]
    # G2's 14 models and G3's 10, by name.
    assert (len(G2_MODELS), len(G3_MODELS)) == (14, 10)
    assert {model.__name__ for model in models} == G2_MODELS | G3_MODELS
    for model in models:
        for item in [*model._meta.constraints, *model._meta.indexes]:
            assert item.name.startswith("graph_"), (model.__name__, item.name)
            assert len(item.name) <= 30, (model.__name__, item.name)


def test_a_refusal_names_the_model_the_field_and_the_rule(db):
    release = DatasetReleaseFactory(status=PUBLISHED)
    release.adapter = "changed"
    with pytest.raises(ImmutableRecordError) as refusal:
        release.save()
    assert str(refusal.value) == (
        "DatasetRelease.adapter: changes only while the release is a draft; "
        f"release {release.pk} is published."
    )


def test_save_reads_the_stored_row_not_the_copy_the_instance_was_loaded_with(db):
    release = DatasetReleaseFactory()
    stale = DatasetRelease.objects.get(pk=release.pk)
    move(release, STAGED)
    # The stale copy still says draft; the stored row decides.
    stale.adapter = "changed"
    with pytest.raises(ImmutableRecordError, match="DatasetRelease.adapter"):
        stale.save()
    assert stored(release)["adapter"] == ""
    assert stored(release)["status"] == STAGED


def test_a_partial_save_writes_and_checks_only_its_fields(db):
    release = DatasetReleaseFactory(status=STAGED)
    release.adapter = "not written"
    release.embargo_until = LATER
    release.save(update_fields=["embargo_until"])
    row = stored(release)
    assert (row["embargo_until"], row["adapter"]) == (LATER, "")


@pytest.mark.postgres
def test_save_locks_the_stored_row_and_the_release_it_belongs_to(db):
    record = SourceRecordFactory()
    with CaptureQueriesContext(connection) as queries:
        record.status = RecordStatus.REJECTED
        record.save()
    locking = [q["sql"] for q in queries.captured_queries if "FOR UPDATE" in q["sql"]]
    assert any('FROM "graph_datasetrelease"' in sql for sql in locking), locking
    assert any('FROM "graph_sourcerecord"' in sql for sql in locking), locking
    # The release is locked first, as the delete cascade from it would.
    assert 'FROM "graph_datasetrelease"' in locking[0]


@pytest.mark.postgres
@pytest.mark.django_db(transaction=True)
def test_a_record_change_waits_for_a_concurrent_publication_and_is_refused():
    """The guard locks the release before reading its status. A record
    change that starts while the release is being published waits, then
    reads the published status and is refused."""
    record = SourceRecordFactory()
    advance(record.release, STAGED)
    holding, go = threading.Event(), threading.Event()
    outcome, errors = {}, []

    def publish():
        try:
            with transaction.atomic():
                release = DatasetRelease.objects.select_for_update().get(
                    pk=record.release_id
                )
                move(release, PUBLISHED)
                holding.set()
                go.wait(30)
        except Exception as exc:  # reported by the main thread
            errors.append(exc)
        finally:
            connection.close()

    def change():
        try:
            holding.wait(30)
            fresh = SourceRecord.objects.get(pk=record.pk)
            fresh.status = RecordStatus.REJECTED
            fresh.save()
            outcome["saved"] = True
        except ImmutableRecordError as exc:
            outcome["refused"] = str(exc)
        except Exception as exc:
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=publish), threading.Thread(target=change)]
    for thread in threads:
        thread.start()
    try:
        assert holding.wait(30), "the publication never started"
        assert _a_backend_waits_on_a_lock(), "the record change never waited"
    finally:
        go.set()
        for thread in threads:
            thread.join(30)

    assert errors == []
    assert "saved" not in outcome
    assert "is published" in outcome["refused"]
    assert SourceRecord.objects.get(pk=record.pk).status == RecordStatus.MAPPED


def _a_backend_waits_on_a_lock(seconds=20) -> bool:
    deadline = time.monotonic() + seconds
    with connection.cursor() as cursor:
        while time.monotonic() < deadline:
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity"
                " WHERE datname = current_database() AND wait_event_type = 'Lock'"
            )
            if cursor.fetchone()[0]:
                return True
            time.sleep(0.05)
    return False


def test_deleting_an_account_keeps_the_rows_that_name_it(db):
    # The deletion collector's SET_NULL goes through the plain base manager.
    user = StaffUserFactory(username="departing")
    release = DatasetReleaseFactory(status=PUBLISHED, published_by=user)
    run = ImportRunFactory(triggered_by=user)
    assert (release.published_by_username, run.triggered_by_username) == (
        "departing",
        "departing",
    )
    user.delete()
    release_row, run_row = stored(release), stored(run)
    assert (release_row["published_by_id"], release_row["published_by_username"]) == (
        None,
        "departing",
    )
    assert (run_row["triggered_by_id"], run_row["triggered_by_username"]) == (
        None,
        "departing",
    )


# ── Closed sets ─────────────────────────────────────────────────────────────


def test_the_closed_sets_hold_exactly_the_values_the_design_names():
    from graph import models

    expected = {
        "DataSourceKind": {"lab", "consortium", "database", "this_portal", "other"},
        "DatasetStatus": {"candidate", "active", "withdrawn"},
        "PublicationRole": {"primary", "describes", "update", "erratum", "related"},
        "ReleaseKind": {"evidence", "reference"},
        "ReleaseStatus": {"draft", "staged", "published", "superseded", "withdrawn"},
        "FileRole": {
            "data",
            "entities",
            "manifest",
            "readme",
            "licence_text",
            "supplementary",
        },
        "ImportMode": {"dry_run", "commit"},
        "ImportState": {"running", "failed", "completed", "abandoned"},
        "RecordKeyBasis": {"source_id", "line_number", "content_hash"},
        "RecordStatus": {"mapped", "unresolved", "ambiguous", "rejected", "skipped"},
        "DueDiligenceStatus": {"open", "complete", "blocked"},
        "Permission": {
            "permitted",
            "permitted_with_conditions",
            "not_permitted",
            "unknown",
        },
        "ShareAlike": {"yes", "no", "unknown"},
        "ApprovalScope": {
            "internal",
            "public_view",
            "public_bulk",
            "external_services",
        },
        "Decision": {"approved", "denied"},
    }
    for name, values in expected.items():
        assert set(getattr(models, name).values) == values, name
    # Unknown and not_permitted are never permission.
    assert models.PERMITTED_VALUES == {"permitted", "permitted_with_conditions"}


CLOSED_SET_FIELDS = [
    (DataSourceFactory, "kind", DataSourceKind),
    (SourceDatasetFactory, "status", DatasetStatus),
    (DatasetReleaseFactory, "kind", ReleaseKind),
    (DatasetReleaseFactory, "status", ReleaseStatus),
    (SourceDatasetPublicationFactory, "role", PublicationRole),
    (ReleasePublicationFactory, "role", PublicationRole),
    (SourceFileFactory, "role", FileRole),
    (ImportRunFactory, "mode", ImportMode),
    (ImportRunFactory, "state", ImportState),
    (SourceRecordFactory, "record_key_basis", RecordKeyBasis),
    (SourceRecordFactory, "status", RecordStatus),
    (ReleaseLicenceFactory, "redistribution", Permission),
    (ReleaseLicenceFactory, "commercial_use", Permission),
    (ReleaseLicenceFactory, "modification", Permission),
    (ReleaseLicenceFactory, "share_alike", ShareAlike),
    (DueDiligenceRecordFactory, "status", DueDiligenceStatus),
    (UsageApprovalFactory, "scope", ApprovalScope),
    (UsageApprovalFactory, "decision", Decision),
]


@pytest.mark.parametrize(
    "factory_class,field,choices",
    CLOSED_SET_FIELDS,
    ids=lambda v: getattr(v, "__name__", v),
)
def test_each_closed_set_check_accepts_exactly_its_values(
    db, factory_class, field, choices
):
    for value in choices.values:
        factory_class(**{field: value})
    model = factory_class._meta.model
    count = model.objects.count()
    for value in ("not-a-value", "", choices.values[0].upper()):
        with pytest.raises(IntegrityError), transaction.atomic():
            factory_class(**{field: value})
    assert model.objects.count() == count


# ── Data sources and datasets ───────────────────────────────────────────────


def test_a_source_and_its_dataset_are_created_with_their_defaults(db):
    source = DataSourceFactory(slug="lab-a", kind=DataSourceKind.LAB)
    dataset = SourceDatasetFactory(slug="lab-a-regulation", data_source=source)
    dataset.refresh_from_db()
    assert dataset.status == DatasetStatus.CANDIDATE
    assert (str(source), str(dataset)) == ("lab-a", "lab-a-regulation")
    assert list(source.datasets.all()) == [dataset]
    with pytest.raises(IntegrityError), transaction.atomic():
        DataSourceFactory(slug="lab-a")
    with pytest.raises(IntegrityError), transaction.atomic():
        SourceDatasetFactory(slug="lab-a-regulation")


def test_catalogue_description_is_editable_on_every_path(db):
    dataset = SourceDatasetFactory()
    # The status is description only: it gates nothing, so it changes freely.
    write("save", dataset, status=DatasetStatus.ACTIVE, title="Renamed")
    write("update", dataset, short_title="Short")
    write("bulk_update", dataset, description="Plain text.")
    row = stored(dataset)
    assert (row["status"], row["title"], row["short_title"], row["description"]) == (
        DatasetStatus.ACTIVE,
        "Renamed",
        "Short",
        "Plain text.",
    )
    source = dataset.data_source
    write("update", source, name="Renamed lab")
    assert stored(source)["name"] == "Renamed lab"
    # Rows nothing uses may be deleted.
    delete("delete", DataSourceFactory())
    delete("queryset_delete", DataSourceFactory())
    assert list(DataSource.objects.all()) == [source]


def test_a_source_or_dataset_that_others_use_cannot_be_deleted(db):
    release = DatasetReleaseFactory()
    with pytest.raises(ProtectedError):
        release.dataset.data_source.delete()
    with pytest.raises(ProtectedError):
        SourceDataset.objects.filter(pk=release.dataset_id).delete()
    assert SourceDataset.objects.filter(pk=release.dataset_id).exists()


URL_FIELDS = [
    (DataSourceFactory, "url"),
    (SourceDatasetFactory, "homepage"),
    (PublicationFactory, "url"),
    (DatasetReleaseFactory, "source_url"),
    (SourceFileFactory, "retrieved_from"),
    (ReleaseLicenceFactory, "licence_url"),
    (ReleaseLicenceFactory, "read_at"),
]
NOT_HTTP = [
    "ftp://example.org/data.tsv",
    "ftps://example.org/data.tsv",
    "javascript:alert(1)",
    "file:///etc/passwd",
    "data:text/plain,hello",
    "mailto:curator@example.org",
    "//example.org/data.tsv",
    "example.org/data.tsv",
]


@pytest.mark.parametrize(
    "factory_class,field", URL_FIELDS, ids=lambda v: getattr(v, "__name__", v)
)
def test_links_are_http_or_https_on_every_save(db, factory_class, field):
    model = factory_class._meta.model
    first = factory_class(**{field: "https://example.org/a"})
    factory_class(**{field: "http://example.org/b"})
    count = model.objects.count()
    for value in NOT_HTTP:
        with pytest.raises(ImmutableRecordError, match=f"{model.__name__}.{field}"):
            factory_class(**{field: value})
        with pytest.raises(ImmutableRecordError, match=f"{model.__name__}.{field}"):
            model.objects.bulk_create([factory_class.build(**{field: value})])
    assert model.objects.count() == count
    # Saving a stored row checks the link again.
    setattr(first, field, "ftp://example.org/a")
    with pytest.raises(ImmutableRecordError, match=f"{model.__name__}.{field}"):
        first.save()
    assert stored(first)[field] == "https://example.org/a"


@pytest.mark.parametrize("path", ["update", "bulk_update"])
@pytest.mark.parametrize("factory_class,field", URL_FIELDS[:2])
def test_a_queryset_update_checks_links_too(db, factory_class, field, path):
    row = factory_class()
    model = type(row)
    write(path, row, **{field: "https://example.org/new"})
    assert stored(row)[field] == "https://example.org/new"
    # Every link of a bulk_update() is checked: one bad one writes nothing.
    text = {"url": "name", "homepage": "title"}[field]
    other = factory_class()
    for value in ("ftp://example.org/x", F("slug")):
        with pytest.raises(ImmutableRecordError, match=f"{model.__name__}.{field}"):
            if path == "update":
                model.objects.filter(pk=row.pk).update(**{field: value})
            else:
                setattr(row, field, "https://example.org/fine")
                setattr(row, text, "Not written")
                setattr(other, field, value)
                model.objects.bulk_update([row, other], [field, text])
    assert stored(row)[field] == "https://example.org/new"
    assert stored(row)[text] != "Not written"
    assert stored(other)[field] == ""


# ── Releases ────────────────────────────────────────────────────────────────


def test_a_release_starts_as_an_evidence_draft_that_keeps_raw_rows(db):
    release = DatasetReleaseFactory()
    row = stored(release)
    assert (row["status"], row["kind"], row["store_raw_rows"]) == (
        DRAFT,
        ReleaseKind.EVIDENCE,
        True,
    )
    assert (row["stats"], row["is_synthetic"], row["supersedes_id"]) == (
        {},
        False,
        None,
    )
    assert (row["published_at"], row["withdrawn_at"]) == (None, None)
    assert list(release.dataset.releases.all()) == [release]


def test_a_version_is_unique_within_its_dataset(db):
    release = DatasetReleaseFactory(version="v1")
    DatasetReleaseFactory(version="v1")
    with pytest.raises(IntegrityError), transaction.atomic():
        DatasetReleaseFactory(dataset=release.dataset, version="v1")
    assert DatasetRelease.objects.filter(version="v1").count() == 2


@pytest.mark.parametrize("version", ["v1", "2024-06", "v1+m2", "1.0", "R_2", "a" * 64])
def test_a_url_safe_version_is_accepted(db, version):
    release = DatasetReleaseFactory(version=version)
    assert stored(release)["version"] == version


@pytest.mark.parametrize(
    "version",
    ["", "-v1", ".v1", "+v1", "v 1", "v1/2", "v1?x", "v1\n", "vé", "a" * 65, None],
)
def test_a_version_that_is_not_url_safe_is_refused_on_every_save(db, version):
    with pytest.raises(ImmutableRecordError, match="DatasetRelease.version"):
        DatasetReleaseFactory(version=version)
    assert not DatasetRelease.objects.exists()
    release = DatasetReleaseFactory(version="v1")
    release.version = version
    with pytest.raises(ImmutableRecordError, match="DatasetRelease.version"):
        release.save()
    assert stored(release)["version"] == "v1"


# Each field and the statuses in which a save may change it (architecture
# §3.5; spec §4).
ONLY_IN_DRAFT = {
    "version",
    "supersedes",
    "adapter",
    "adapter_version",
    "mapping_version",
    "store_raw_rows",
    "release_date",
    "retrieved_at",
    "source_url",
    "kind",
}
UNTIL_PUBLISHED = {"stats", "embargo_until"}
AT_CREATION = {"dataset", "is_synthetic"}


def changed_value(field, release):
    if field == "supersedes":
        return DatasetReleaseFactory(dataset=release.dataset, status=PUBLISHED)
    if field == "dataset":
        return SourceDatasetFactory()
    return {
        "version": "v-changed",
        "adapter": "changed_adapter",
        "adapter_version": "9",
        "mapping_version": "9",
        "store_raw_rows": False,
        "release_date": datetime.date(2026, 1, 2),
        "retrieved_at": LATER,
        "source_url": "https://example.org/changed",
        "kind": ReleaseKind.REFERENCE,
        "stats": {"records": 1},
        "embargo_until": LATER,
        "is_synthetic": True,
    }[field]


@pytest.mark.parametrize("status", STATUSES)
@pytest.mark.parametrize("field", sorted(ONLY_IN_DRAFT | UNTIL_PUBLISHED | AT_CREATION))
def test_release_fields_freeze_by_status(db, field, status):
    release = DatasetReleaseFactory(status=status)
    value = changed_value(field, release)
    before = stored(release)
    setattr(release, field, value)
    allowed = (
        {DRAFT}
        if field in ONLY_IN_DRAFT
        else {DRAFT, STAGED} if field in UNTIL_PUBLISHED else set()
    )
    if status in allowed:
        release.save()
        attname = DatasetRelease._meta.get_field(field).attname
        expected = value.pk if hasattr(value, "pk") else value
        assert stored(release)[attname] == expected
    else:
        with pytest.raises(ImmutableRecordError, match=f"DatasetRelease.{field}"):
            release.save()
        assert stored(release) == before


@pytest.mark.parametrize("path", ["update", "bulk_update"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("adapter", "changed"),
        ("stats", {"records": 1}),
        ("status", STAGED),
        ("kind", ReleaseKind.REFERENCE),
    ],
)
def test_a_queryset_update_of_a_release_is_refused_even_in_draft(
    db, path, field, value
):
    release = DatasetReleaseFactory()
    before = stored(release)
    with pytest.raises(ImmutableRecordError, match="DatasetRelease"):
        write(path, release, **{field: value})
    assert stored(release) == before


def test_bulk_create_of_releases_is_refused(db):
    dataset = SourceDatasetFactory()
    with pytest.raises(ImmutableRecordError, match="DatasetRelease"):
        DatasetRelease.objects.bulk_create(
            [DatasetRelease(dataset=dataset, version="v1")]
        )
    assert not DatasetRelease.objects.exists()


ALLOWED_TRANSITIONS = {
    (DRAFT, STAGED),
    (STAGED, DRAFT),
    (STAGED, PUBLISHED),
    (PUBLISHED, SUPERSEDED),
    (STAGED, WITHDRAWN),
    (PUBLISHED, WITHDRAWN),
    (SUPERSEDED, WITHDRAWN),
}


@pytest.mark.parametrize(
    "old,new", [(old, new) for old in STATUSES for new in STATUSES if old != new]
)
def test_status_transitions(db, old, new):
    release = DatasetReleaseFactory(status=old)
    before = stored(release)
    if (old, new) in ALLOWED_TRANSITIONS:
        move(release, new)
        assert stored(release)["status"] == new
    else:
        with pytest.raises(ImmutableRecordError, match="DatasetRelease.status"):
            move(release, new)
        assert stored(release) == before


def test_publishing_sets_who_published_and_withdrawing_keeps_it(db):
    publisher, withdrawer = StaffUserFactory(), StaffUserFactory()
    release = DatasetReleaseFactory(status=STAGED)
    release.status = PUBLISHED
    release.published_at = timezone.now()
    release.published_by = publisher
    release._lifecycle = True
    release.save(update_fields=["status", "published_at", "published_by"])
    row = stored(release)
    assert (row["published_by_id"], row["published_by_username"]) == (
        publisher.pk,
        publisher.username,
    )
    release.status = WITHDRAWN
    release.withdrawn_at = timezone.now()
    release.withdrawn_reason = "Corrected upstream."
    release.withdrawn_by = withdrawer
    release.save()
    row = stored(release)
    assert (row["published_by_username"], row["withdrawn_by_username"]) == (
        publisher.username,
        withdrawer.username,
    )


@pytest.mark.parametrize("status", [PUBLISHED, SUPERSEDED, WITHDRAWN])
@pytest.mark.parametrize(
    "field", ["published_at", "published_by", "published_by_username"]
)
def test_who_published_is_set_once(db, status, field):
    release = DatasetReleaseFactory(
        status=status, published_at=timezone.now(), published_by_username="first"
    )
    value = {
        "published_at": LATER,
        "published_by": StaffUserFactory(),
        "published_by_username": "someone-else",
    }[field]
    before = stored(release)
    setattr(release, field, value)
    with pytest.raises(ImmutableRecordError, match=f"DatasetRelease.{field}"):
        release.save()
    assert stored(release) == before


@pytest.mark.parametrize(
    "field",
    ["withdrawn_at", "withdrawn_reason", "withdrawn_by", "withdrawn_by_username"],
)
def test_who_withdrew_and_why_is_set_once(db, field):
    release = DatasetReleaseFactory(status=WITHDRAWN)
    value = {
        "withdrawn_at": LATER,
        "withdrawn_reason": "Another reason.",
        "withdrawn_by": StaffUserFactory(),
        "withdrawn_by_username": "someone-else",
    }[field]
    before = stored(release)
    setattr(release, field, value)
    with pytest.raises(ImmutableRecordError, match=f"DatasetRelease.{field}"):
        release.save()
    assert stored(release) == before


def test_the_lifecycle_fields_are_set_only_by_their_transition(db):
    staged = DatasetReleaseFactory(status=STAGED)
    before = stored(staged)
    staged.published_by_username = "early"
    with pytest.raises(ImmutableRecordError, match="published_by_username"):
        staged.save()
    assert stored(staged) == before
    published = DatasetReleaseFactory(status=PUBLISHED)
    before = stored(published)
    published.withdrawn_reason = "Not withdrawn."
    with pytest.raises(ImmutableRecordError, match="withdrawn_reason"):
        published.save()
    assert stored(published) == before


@pytest.mark.parametrize("path", ["save", "save_update_fields"])
@pytest.mark.parametrize(
    "old,new", sorted(DatasetRelease.PUBLISHING_TRANSITIONS), ids=lambda s: s
)
def test_publishing_and_superseding_are_saved_only_by_publish_release(
    db, old, new, path
):
    """Their rules span releases (an effective internal approval; a
    correction never hides public data, nor stands published beside the
    release it corrects), so a bare save of either is refused."""
    release = DatasetReleaseFactory(status=old)
    before = stored(release)
    release.status = new
    fields = ["status"]
    if new == PUBLISHED:
        release.published_at = timezone.now()
        release.published_by_username = "nobody"
        fields += ["published_at", "published_by_username"]
    with pytest.raises(ImmutableRecordError, match="only through graph.releases"):
        if path == "save":
            release.save()
        else:
            release.save(update_fields=fields)
    assert stored(release) == before


def test_the_lifecycle_flag_lasts_one_save(db):
    release = DatasetReleaseFactory(status=STAGED)
    move(release, PUBLISHED)
    assert release._lifecycle is False
    release.status = SUPERSEDED
    with pytest.raises(ImmutableRecordError, match="only through graph.releases"):
        release.save()
    # Cleared after a refusal too.
    release._lifecycle = True
    release.adapter = "changed"
    with pytest.raises(ImmutableRecordError, match="DatasetRelease.adapter"):
        release.save()
    assert release._lifecycle is False
    assert stored(release)["status"] == PUBLISHED


@pytest.mark.parametrize("status", STATUSES)
@pytest.mark.parametrize("path", DELETE_PATHS)
def test_a_release_is_deleted_only_while_a_draft(db, status, path):
    release = DatasetReleaseFactory(status=status)
    if status == DRAFT:
        delete(path, release)
        assert not DatasetRelease.objects.exists()
    else:
        with pytest.raises(ImmutableRecordError, match="DatasetRelease.status"):
            delete(path, release)
        assert DatasetRelease.objects.filter(pk=release.pk).exists()


def test_a_queryset_delete_with_any_release_past_draft_deletes_none(db):
    dataset = SourceDatasetFactory()
    DatasetReleaseFactory(dataset=dataset)
    DatasetReleaseFactory(dataset=dataset, status=PUBLISHED)
    with pytest.raises(ImmutableRecordError, match="is published"):
        DatasetRelease.objects.filter(dataset=dataset).delete()
    assert DatasetRelease.objects.filter(dataset=dataset).count() == 2


def test_deleting_a_draft_release_removes_its_files_runs_records_and_taxa(db):
    record = SourceRecordFactory()
    release = record.release
    ReleaseTaxonFactory(release=release)
    ReleasePublicationFactory(release=release)
    release.delete()
    for model in (SourceRecord, ImportRun, SourceFile, ReleaseTaxon, DatasetRelease):
        assert not model.objects.exists(), model.__name__


# ── Supersession ────────────────────────────────────────────────────────────


def test_a_correction_belongs_to_the_dataset_of_the_release_it_corrects(db):
    target = DatasetReleaseFactory(status=PUBLISHED)
    with pytest.raises(ImmutableRecordError, match="DatasetRelease.supersedes"):
        DatasetReleaseFactory(supersedes=target)
    draft = DatasetReleaseFactory()
    draft.supersedes = target
    with pytest.raises(ImmutableRecordError, match="dataset of the release"):
        draft.save()
    assert stored(draft)["supersedes_id"] is None
    assert not target.corrections.exists()


def test_a_correction_keeps_the_synthetic_flag(db):
    target = DatasetReleaseFactory(status=PUBLISHED)
    with pytest.raises(ImmutableRecordError, match="synthetic flag"):
        DatasetReleaseFactory(
            dataset=target.dataset, supersedes=target, is_synthetic=True
        )
    assert not target.corrections.exists()


def test_a_release_never_supersedes_itself(db):
    release = DatasetReleaseFactory()
    release.supersedes = release
    with pytest.raises(ImmutableRecordError, match="never supersedes itself"):
        release.save()
    # The database refuses it too (graph_release_not_self); only the base
    # manager's plain update gets past the guard to show it.
    with pytest.raises(IntegrityError), transaction.atomic():
        DatasetRelease._base_manager.filter(pk=release.pk).update(supersedes=F("pk"))
    assert stored(release)["supersedes_id"] is None


def test_a_release_is_corrected_by_at_most_one_release_that_was_published(db):
    target = DatasetReleaseFactory(status=SUPERSEDED)
    first = DatasetReleaseFactory(
        dataset=target.dataset, status=PUBLISHED, supersedes=target
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        DatasetReleaseFactory(
            dataset=target.dataset, status=PUBLISHED, supersedes=target
        )
    # Withdrawn after its publication, it still holds the place.
    move(first, WITHDRAWN)
    with pytest.raises(IntegrityError), transaction.atomic():
        DatasetReleaseFactory(
            dataset=target.dataset, status=PUBLISHED, supersedes=target
        )
    assert list(target.corrections.all()) == [first]


def test_unpublished_corrections_never_hold_the_place(db):
    target = DatasetReleaseFactory(status=PUBLISHED)
    for status in (DRAFT, DRAFT, STAGED, STAGED, WITHDRAWN):
        DatasetReleaseFactory(dataset=target.dataset, status=status, supersedes=target)
    move(target, SUPERSEDED)
    # A correction withdrawn before its publication freed the place.
    DatasetReleaseFactory(dataset=target.dataset, status=PUBLISHED, supersedes=target)
    assert target.corrections.count() == 6


def test_a_release_inserted_as_published_corrects_only_one_no_longer_current(db):
    target = DatasetReleaseFactory(status=PUBLISHED)
    with pytest.raises(ImmutableRecordError, match="is published"):
        DatasetReleaseFactory(
            dataset=target.dataset, status=PUBLISHED, supersedes=target
        )
    assert not target.corrections.exists()
    withdrawn = DatasetReleaseFactory(dataset=target.dataset, status=WITHDRAWN)
    DatasetReleaseFactory(
        dataset=target.dataset, status=PUBLISHED, supersedes=withdrawn
    )


def test_a_superseded_release_cannot_be_deleted_while_a_correction_names_it(db):
    target = DatasetReleaseFactory()
    DatasetReleaseFactory(dataset=target.dataset, supersedes=target)
    with pytest.raises(ProtectedError):
        target.delete()


# ── Declared taxa ───────────────────────────────────────────────────────────


TAXON_PATHS = {
    "release.declared_taxa.add": lambda release, declared, other: (
        release.declared_taxa.add(other)
    ),
    "release.declared_taxa.remove": lambda release, declared, other: (
        release.declared_taxa.remove(declared)
    ),
    "release.declared_taxa.clear": lambda release, declared, other: (
        release.declared_taxa.clear()
    ),
    "release.declared_taxa.set": lambda release, declared, other: (
        release.declared_taxa.set([other])
    ),
    "taxon.releases.add": lambda release, declared, other: other.releases.add(release),
    "taxon.releases.remove": lambda release, declared, other: (
        declared.releases.remove(release)
    ),
    "taxon.releases.clear": lambda release, declared, other: declared.releases.clear(),
    "taxon.releases.set": lambda release, declared, other: other.releases.set(
        [release]
    ),
    "ReleaseTaxon.save": lambda release, declared, other: ReleaseTaxon(
        release=release, taxon=other
    ).save(),
    "ReleaseTaxon.bulk_create": lambda release, declared, other: (
        ReleaseTaxon.objects.bulk_create([ReleaseTaxon(release=release, taxon=other)])
    ),
    "ReleaseTaxon.delete": lambda release, declared, other: (
        ReleaseTaxon.objects.get(release=release, taxon=declared).delete()
    ),
    "ReleaseTaxon queryset delete": lambda release, declared, other: (
        ReleaseTaxon.objects.filter(release=release).delete()
    ),
}
# The release's declared taxa after each path, in draft: "declared", "other".
TAXA_AFTER = {
    "release.declared_taxa.add": {"declared", "other"},
    "release.declared_taxa.remove": set(),
    "release.declared_taxa.clear": set(),
    "release.declared_taxa.set": {"other"},
    "taxon.releases.add": {"declared", "other"},
    "taxon.releases.remove": set(),
    "taxon.releases.clear": set(),
    "taxon.releases.set": {"declared", "other"},
    "ReleaseTaxon.save": {"declared", "other"},
    "ReleaseTaxon.bulk_create": {"declared", "other"},
    "ReleaseTaxon.delete": set(),
    "ReleaseTaxon queryset delete": set(),
}


@pytest.mark.parametrize("status", STATUSES)
@pytest.mark.parametrize("path", sorted(TAXON_PATHS))
def test_declared_taxa_change_only_while_the_release_is_a_draft(db, path, status):
    release = DatasetReleaseFactory()
    declared, other = TaxonFactory(), TaxonFactory()
    release.declared_taxa.add(declared)
    advance(release, status)
    names = {declared.pk: "declared", other.pk: "other"}
    if status == DRAFT:
        TAXON_PATHS[path](release, declared, other)
        taxa = {names[t] for t in release.declared_taxa.values_list("pk", flat=True)}
        assert taxa == TAXA_AFTER[path]
    else:
        # The many-to-many managers call the guard inside a block of their
        # own without a savepoint, which the refusal marks for rollback; the
        # savepoint here keeps the test's transaction usable.
        with pytest.raises(
            ImmutableRecordError, match="ReleaseTaxon.release"
        ), transaction.atomic():
            TAXON_PATHS[path](release, declared, other)
        assert list(release.declared_taxa.all()) == [declared]


@pytest.mark.parametrize("path", WRITE_PATHS)
def test_a_declared_taxon_row_is_never_changed(db, path):
    row = ReleaseTaxonFactory()
    before = stored(row)
    with pytest.raises(ImmutableRecordError, match="ReleaseTaxon"):
        write(path, row, taxon=TaxonFactory())
    assert stored(row) == before


def test_a_declared_taxon_cannot_be_deleted_from_the_registry(db):
    row = ReleaseTaxonFactory()
    with pytest.raises(ProtectedError):
        row.taxon.delete()
    assert ReleaseTaxon.objects.filter(pk=row.pk).exists()


# ── Source files ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sha256",
    [
        "A" * 64,
        "a" * 63,
        "a" * 65,
        "g" * 64,
        " " + "a" * 63,
        "",
        "ab" * 31 + "A1",
    ],
)
def test_a_file_checksum_is_64_lower_case_hex_digits(db, sha256):
    release = DatasetReleaseFactory()
    with pytest.raises(ImmutableRecordError, match="SourceFile.sha256"):
        SourceFileFactory(release=release, sha256=sha256)
    with pytest.raises(ImmutableRecordError, match="SourceFile.sha256"):
        SourceFile.objects.bulk_create(
            [SourceFileFactory.build(release=release, sha256=sha256)]
        )
    assert not SourceFile.objects.exists()


def test_a_checksum_is_unique_per_release(db):
    file = SourceFileFactory()
    SourceFileFactory(sha256=file.sha256)
    with pytest.raises(IntegrityError), transaction.atomic():
        SourceFileFactory(release=file.release, sha256=file.sha256)
    assert SourceFile.objects.filter(sha256=file.sha256).count() == 2


@pytest.mark.parametrize("path", ["save", "bulk_create"])
@pytest.mark.parametrize("status", [STAGED, PUBLISHED, SUPERSEDED, WITHDRAWN])
def test_files_are_added_only_while_the_release_is_a_draft(db, path, status):
    release = DatasetReleaseFactory(status=status)
    with pytest.raises(ImmutableRecordError, match="SourceFile.release"):
        insert(path, SourceFileFactory.build(release=release))
    assert not SourceFile.objects.exists()


@pytest.mark.parametrize("path", ["save", "bulk_create"])
@pytest.mark.parametrize("decision", [Decision.APPROVED, Decision.DENIED, "revoked"])
def test_files_are_fixed_before_anyone_decides_on_the_release(db, path, decision):
    release = DatasetReleaseFactory()
    SourceFileFactory(release=release)
    approval = UsageApprovalFactory(
        release=release,
        decision=Decision.APPROVED if decision == "revoked" else decision,
    )
    if decision == "revoked":
        approval.revoked_at = timezone.now()
        approval.revoked_by = StaffUserFactory()
        approval.revoke_reason = "Test only."
        approval.save()
    with pytest.raises(ImmutableRecordError, match="approval decision"):
        insert(path, SourceFileFactory.build(release=release))
    assert release.files.count() == 1


@pytest.mark.parametrize("path", WRITE_PATHS)
def test_a_file_is_never_updated(db, path):
    file = SourceFileFactory()
    before = stored(file)
    with pytest.raises(ImmutableRecordError, match="SourceFile"):
        write(path, file, media_type="text/tab-separated-values")
    assert stored(file) == before
    with pytest.raises(ImmutableRecordError, match="never updated"):
        SourceFile.objects.get(pk=file.pk).save()


# ── Import runs ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "state,is_open",
    [
        (ImportState.RUNNING, True),
        (ImportState.FAILED, True),
        (ImportState.COMPLETED, False),
        (ImportState.ABANDONED, False),
    ],
)
def test_a_release_has_at_most_one_open_commit_run(db, state, is_open):
    release = DatasetReleaseFactory()
    ImportRunFactory(release=release, file=None, mode=ImportMode.COMMIT, state=state)
    if is_open:
        with pytest.raises(IntegrityError), transaction.atomic():
            ImportRunFactory(release=release, file=None, mode=ImportMode.COMMIT)
        assert release.import_runs.count() == 1
    else:
        ImportRunFactory(release=release, file=None, mode=ImportMode.COMMIT)
    # Dry runs never count.
    ImportRunFactory.create_batch(2, release=release, file=None)
    ImportRunFactory(release=DatasetReleaseFactory(), file=None, mode=ImportMode.COMMIT)


@pytest.mark.parametrize("path", ["save", "bulk_create"])
@pytest.mark.parametrize("status", STATUSES)
def test_a_commit_run_starts_only_while_the_release_is_a_draft(db, path, status):
    release = DatasetReleaseFactory(status=status)
    run = ImportRunFactory.build(release=release, file=None, mode=ImportMode.COMMIT)
    if status == DRAFT:
        insert(path, run)
        assert release.import_runs.count() == 1
    else:
        with pytest.raises(ImmutableRecordError, match="ImportRun.mode"):
            insert(path, run)
        assert not release.import_runs.exists()
    # A dry run may read any release.
    insert(path, ImportRunFactory.build(release=release, file=None))


@pytest.mark.parametrize("path", ["save", "bulk_create"])
def test_a_run_reads_a_file_of_its_own_release(db, path):
    file = SourceFileFactory()
    other = DatasetReleaseFactory()
    with pytest.raises(ImmutableRecordError, match="ImportRun.file"):
        insert(path, ImportRunFactory.build(release=other, file=file))
    assert not ImportRun.objects.exists()


def run_fixed_value(field, run):
    return {
        "release": lambda: DatasetReleaseFactory(),
        "file": lambda: SourceFileFactory(release=run.release),
        "mode": lambda: ImportMode.COMMIT,
        "adapter": lambda: "other_adapter",
        "adapter_version": lambda: "2",
        "mapping_version": lambda: "2",
        "triggered_by": lambda: StaffUserFactory(),
        "triggered_by_username": lambda: "someone-else",
        "started_at": lambda: LATER,
    }[field]()


RUN_FIXED = [
    "release",
    "file",
    "mode",
    "adapter",
    "adapter_version",
    "mapping_version",
    "triggered_by",
    "triggered_by_username",
    "started_at",
]
RUN_OUTCOME = {
    "state": ImportState.COMPLETED,
    "parameters": {"create_entities": False},
    "checkpoint": {"batch": 3},
    "report": {"rejected": 1},
    "error": "Line 7: bad UTF-8.",
    "finished_at": LATER,
}


@pytest.mark.parametrize("path", WRITE_PATHS)
@pytest.mark.parametrize("field", RUN_FIXED)
def test_what_a_run_ran_and_who_started_it_never_change(db, path, field):
    run = ImportRunFactory()
    value = run_fixed_value(field, run)
    before = stored(run)
    with pytest.raises(ImmutableRecordError, match="ImportRun"):
        write(path, run, **{field: value})
    assert stored(run) == before


@pytest.mark.parametrize("path", WRITE_PATHS)
@pytest.mark.parametrize("status", STATUSES)
def test_a_runs_outcome_changes_as_it_runs(db, path, status):
    run = ImportRunFactory(file=None)
    advance(run.release, status)
    write(path, run, **RUN_OUTCOME)
    row = stored(run)
    assert {name: row[name] for name in RUN_OUTCOME} == RUN_OUTCOME


# ── Source records ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", ["save", "bulk_create"])
@pytest.mark.parametrize("status", STATUSES)
def test_records_are_added_only_while_the_release_is_a_draft_or_staged(
    db, path, status
):
    release = DatasetReleaseFactory()
    file = SourceFileFactory(release=release)
    run = ImportRunFactory(release=release, file=file)
    advance(release, status)
    record = SourceRecordFactory.build(release=release, file=file, import_run=run)
    if status in (DRAFT, STAGED):
        insert(path, record)
        assert release.records.count() == 1
    else:
        with pytest.raises(ImmutableRecordError, match="SourceRecord.release"):
            insert(path, record)
        assert not release.records.exists()


@pytest.mark.parametrize("path", ["save", "bulk_create"])
@pytest.mark.parametrize("foreign", ["file", "import_run"])
def test_a_record_comes_from_a_file_and_run_of_its_own_release(db, path, foreign):
    record = SourceRecordFactory()
    other = DatasetReleaseFactory()
    own = {
        "file": SourceFileFactory(release=other),
        "import_run": ImportRunFactory(release=other, file=None),
    }
    own[foreign] = getattr(record, foreign)
    with pytest.raises(ImmutableRecordError, match=f"SourceRecord.{foreign}"):
        insert(path, SourceRecordFactory.build(release=other, **own))
    assert not other.records.exists()


def record_content_value(field, record):
    return {
        "release": lambda: DatasetReleaseFactory(),
        "file": lambda: SourceFileFactory(release=record.release),
        "import_run": lambda: ImportRunFactory(release=record.release, file=None),
        "record_key": lambda: "another-key",
        "record_key_basis": lambda: RecordKeyBasis.LINE_NUMBER,
        "line_number": lambda: 99,
        "raw": lambda: {"id": "changed"},
        "raw_sha256": lambda: "0" * 64,
    }[field]()


RECORD_CONTENT = [
    "release",
    "file",
    "import_run",
    "record_key",
    "record_key_basis",
    "line_number",
    "raw",
    "raw_sha256",
]


@pytest.mark.parametrize("path", WRITE_PATHS)
@pytest.mark.parametrize("field", RECORD_CONTENT)
def test_a_records_content_never_changes(db, path, field):
    record = SourceRecordFactory()
    value = record_content_value(field, record)
    before = stored(record)
    with pytest.raises(ImmutableRecordError, match=f"SourceRecord.{field}"):
        write(path, record, **{field: value})
    assert stored(record) == before


@pytest.mark.parametrize("path", WRITE_PATHS)
@pytest.mark.parametrize("status", STATUSES)
def test_a_records_status_and_issues_change_only_while_draft_or_staged(
    db, path, status
):
    record = SourceRecordFactory()
    advance(record.release, status)
    before = stored(record)
    changes = {"status": RecordStatus.REJECTED, "issues": ["unknown namespace"]}
    if status in (DRAFT, STAGED):
        write(path, record, **changes)
        row = stored(record)
        assert (row["status"], row["issues"]) == (
            RecordStatus.REJECTED,
            ["unknown namespace"],
        )
    else:
        with pytest.raises(ImmutableRecordError, match="SourceRecord"):
            write(path, record, **changes)
        assert stored(record) == before


def test_a_record_of_a_published_release_is_not_even_saved_unchanged(db):
    record = SourceRecordFactory()
    advance(record.release, PUBLISHED)
    before = stored(record)
    with pytest.raises(ImmutableRecordError, match="is published"):
        SourceRecord.objects.get(pk=record.pk).save()
    assert stored(record) == before


def test_a_release_that_keeps_no_raw_rows_stores_none(db):
    release = DatasetReleaseFactory(store_raw_rows=False)
    with pytest.raises(ImmutableRecordError, match="SourceRecord.raw"):
        SourceRecordFactory(release=release)
    record = SourceRecordFactory(release=release, raw=None)
    assert stored(record)["raw"] is None


@pytest.mark.parametrize("digest", ["", "A" * 64, "a" * 63, "not a hash"])
def test_a_records_hash_is_64_lower_case_hex_digits(db, digest):
    with pytest.raises(ImmutableRecordError, match="SourceRecord.raw_sha256"):
        SourceRecordFactory(raw_sha256=digest)
    assert not SourceRecord.objects.exists()


def test_records_are_inserted_in_bulk_but_never_upserted(db):
    record = SourceRecordFactory()
    rows = [
        SourceRecordFactory.build(
            release=record.release, file=record.file, import_run=record.import_run
        )
        for _ in range(3)
    ]
    SourceRecord.objects.bulk_create(rows)
    assert record.release.records.count() == 4
    # An upsert would rewrite stored rows past their rules.
    again = SourceRecordFactory.build(
        release=record.release,
        file=record.file,
        import_run=record.import_run,
        record_key=record.record_key,
        raw={"id": "rewritten"},
    )
    with pytest.raises(ImmutableRecordError, match="update_conflicts"):
        SourceRecord.objects.bulk_create(
            [again],
            update_conflicts=True,
            unique_fields=["release", "record_key"],
            update_fields=["raw"],
        )
    assert stored(record)["raw"] == record.raw


def test_a_record_key_is_unique_per_release_and_its_line_positive(db):
    record = SourceRecordFactory(record_key="row-1")
    SourceRecordFactory(record_key="row-1")
    with pytest.raises(IntegrityError), transaction.atomic():
        SourceRecordFactory(
            release=record.release,
            file=record.file,
            import_run=record.import_run,
            record_key="row-1",
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        SourceRecordFactory(line_number=0)
    assert SourceRecord.objects.filter(record_key="row-1").count() == 2


# ── Deleting what belongs to a release ──────────────────────────────────────


def release_part(model, release):
    if model is SourceFile:
        return SourceFileFactory(release=release)
    if model is ImportRun:
        return ImportRunFactory(release=release, file=None)
    if model is SourceRecord:
        return SourceRecordFactory(release=release)
    return ReleaseTaxonFactory(release=release)


@pytest.mark.parametrize("status", STATUSES)
@pytest.mark.parametrize("path", DELETE_PATHS)
@pytest.mark.parametrize(
    "model",
    [SourceFile, ImportRun, SourceRecord, ReleaseTaxon],
    ids=lambda m: m.__name__,
)
def test_a_releases_rows_are_deleted_only_while_it_is_a_draft(db, model, path, status):
    release = DatasetReleaseFactory()
    row = release_part(model, release)
    advance(release, status)
    if status == DRAFT:
        delete(path, row)
        assert not model.objects.filter(pk=row.pk).exists()
    else:
        with pytest.raises(ImmutableRecordError, match=f"{model.__name__}.release"):
            delete(path, row)
        assert model.objects.filter(pk=row.pk).exists()


def test_a_file_or_run_that_records_point_at_is_never_deleted_alone(db):
    record = SourceRecordFactory()
    with pytest.raises(RestrictedError):
        record.file.delete()
    with pytest.raises(RestrictedError):
        ImportRun.objects.filter(pk=record.import_run_id).delete()
    # A run that read a file keeps the file too.
    run = ImportRunFactory()
    with pytest.raises(RestrictedError):
        run.file.delete()
    assert SourceFile.objects.filter(pk=record.file_id).exists()
    assert ImportRun.objects.filter(pk=record.import_run_id).exists()
    # The whole draft release goes together.
    record.release.delete()
    assert not SourceRecord.objects.filter(pk=record.pk).exists()


# ── Deletes decide from the stored row ──────────────────────────────────────

# A stored row of each model whose release decides its deletion, made while
# its release is a draft.
RELEASE_ROWS = {
    ReleaseTaxon: lambda release: ReleaseTaxonFactory(release=release),
    SourceFile: lambda release: SourceFileFactory(release=release),
    ImportRun: lambda release: ImportRunFactory(release=release, file=None),
    SourceRecord: lambda release: SourceRecordFactory(release=release),
    EvidenceAssertion: lambda release: EvidenceAssertionFactory(release=release),
    AssertionParticipant: lambda release: AssertionParticipantFactory(
        assertion__release=release
    ),
    EvidenceMethod: lambda release: EvidenceMethodFactory(assertion__release=release),
    EvidencePublication: lambda release: EvidencePublicationFactory(
        assertion__release=release
    ),
    EvidenceScore: lambda release: EvidenceScoreFactory(assertion__release=release),
    EvidenceContext: lambda release: EvidenceContextFactory(assertion__release=release),
}


def _misleading(model, row, variant):
    """An instance of the stored row that says less, or something else, than
    the row: its pk alone, or a draft release (or assertion) given in
    memory."""
    if variant == "pk only":
        return model(pk=row.pk)
    instance = model.objects.get(pk=row.pk)
    draft = DatasetReleaseFactory()
    instance.release_id = draft.pk
    if variant == "assertion in memory":
        instance.assertion_id = EvidenceAssertionFactory(release=draft).pk
    return instance


@pytest.mark.parametrize("variant", ["pk only", "release in memory"])
@pytest.mark.parametrize("status", [STAGED, PUBLISHED, SUPERSEDED, WITHDRAWN])
@pytest.mark.parametrize("model", list(RELEASE_ROWS), ids=lambda m: m.__name__)
def test_a_delete_is_decided_by_the_stored_row_not_the_instance(
    db, model, status, variant
):
    """A probe showed SourceRecord(pk=x).delete() deleting a published
    release's record: the guard asked the instance's release, which a
    pk-only instance does not have."""
    release = DatasetReleaseFactory()
    row = RELEASE_ROWS[model](release)
    advance(release, status)
    with pytest.raises(ImmutableRecordError, match=f"{model.__name__}.release"):
        _misleading(model, row, variant).delete()
    assert model._base_manager.filter(pk=row.pk).exists()


@pytest.mark.parametrize("status", [STAGED, PUBLISHED, SUPERSEDED, WITHDRAWN])
@pytest.mark.parametrize(
    "model",
    [
        AssertionParticipant,
        EvidenceMethod,
        EvidencePublication,
        EvidenceScore,
        EvidenceContext,
    ],
    ids=lambda m: m.__name__,
)
def test_a_parts_delete_ignores_an_assertion_given_in_memory(db, model, status):
    release = DatasetReleaseFactory()
    row = RELEASE_ROWS[model](release)
    advance(release, status)
    with pytest.raises(ImmutableRecordError, match=f"{model.__name__}.release"):
        _misleading(model, row, "assertion in memory").delete()
    assert model._base_manager.filter(pk=row.pk).exists()


def test_an_update_is_decided_by_the_stored_rows_release_not_the_instance(db):
    """The release a save locks and asks is the stored row's: a draft given
    in memory let a partial save change a published release's record."""
    record = SourceRecordFactory()
    advance(record.release, PUBLISHED)
    fresh = SourceRecord.objects.get(pk=record.pk)
    fresh.release_id = DatasetReleaseFactory().pk
    fresh.status = RecordStatus.REJECTED
    with pytest.raises(ImmutableRecordError, match="is published"):
        fresh.save(update_fields=["status"])
    assert stored(record)["status"] == RecordStatus.MAPPED


def test_a_due_diligence_actor_is_judged_by_the_stored_account(db):
    actor = StaffUserFactory()
    # Demoted since it was loaded: the object in memory still says staff.
    type(actor).objects.filter(pk=actor.pk).update(is_staff=False)
    record = DueDiligenceRecordFactory.build(
        dataset=SourceDatasetFactory(), changed_by=actor
    )
    with pytest.raises(ImmutableRecordError, match="names its actor"):
        record.save()
    assert not DueDiligenceRecord.objects.exists()


def test_a_draft_releases_row_is_deleted_by_its_pk_alone(db):
    record = SourceRecordFactory()
    assert SourceRecord(pk=record.pk).delete()[0] == 1
    assert not SourceRecord.objects.filter(pk=record.pk).exists()


# The models whose rows are never deleted, whatever their release says.
NEVER_DELETED = {
    DueDiligenceRecord: lambda: DueDiligenceRecordFactory(),
    DueDiligenceChange: lambda: DueDiligenceRecordFactory().history.get(),
    ReleaseLicence: lambda: ReleaseLicenceFactory(),
    UsageApproval: lambda: UsageApprovalFactory(),
    Term: lambda: TermFactory(),
    Relationship: lambda: RelationshipFactory(),
}


@pytest.mark.parametrize("model", list(NEVER_DELETED), ids=lambda m: m.__name__)
def test_a_row_that_is_never_deleted_is_not_deleted_by_its_pk(db, model):
    row = NEVER_DELETED[model]()
    with pytest.raises(ImmutableRecordError, match=f"{model.__name__}"):
        model(pk=row.pk).delete()
    assert model._base_manager.filter(pk=row.pk).exists()


@pytest.mark.parametrize("status", [STAGED, PUBLISHED, SUPERSEDED, WITHDRAWN])
def test_a_release_is_not_deleted_by_its_pk_past_draft(db, status):
    release = DatasetReleaseFactory(status=status)
    with pytest.raises(ImmutableRecordError, match="DatasetRelease.status"):
        DatasetRelease(pk=release.pk).delete()
    assert DatasetRelease.objects.filter(pk=release.pk).exists()


@pytest.mark.parametrize("model", GUARDED_MODELS, ids=lambda m: m.__name__)
def test_a_pk_that_names_no_row_deletes_nothing(db, model):
    missing = "MI:9999" if model is Term else 10**12
    assert model(pk=missing).delete()[0] == 0


# ── JSON is compared exactly ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "stored_raw,retyped",
    [
        ({"n": 1}, {"n": True}),
        ({"n": True}, {"n": 1}),
        ({"n": 1}, {"n": 1.0}),
        ({"n": [1, 2]}, {"n": [1.0, 2]}),
        ({"n": None}, {"n": False}),
    ],
)
def test_a_records_raw_row_is_never_retyped(db, stored_raw, retyped):
    """Python finds True == 1 and 1.0 == 1; JSON stores them differently, so
    the comparison that decides "never changes" reads canonical JSON."""
    record = SourceRecordFactory(raw=stored_raw)
    advance(record.release, STAGED)
    before = stored(record)
    record.raw = retyped
    with pytest.raises(ImmutableRecordError, match="SourceRecord.raw"):
        record.save()
    assert stored(record) == before


def test_json_that_differs_only_in_key_order_is_unchanged(db):
    record = SourceRecordFactory(raw={"a": 1, "b": [1, 2]})
    advance(record.release, STAGED)
    record.raw = {"b": [1, 2], "a": 1}
    record.status = RecordStatus.REJECTED
    record.save()
    assert stored(record)["status"] == RecordStatus.REJECTED


def test_a_published_releases_stats_are_never_retyped(db):
    release = DatasetReleaseFactory(status=PUBLISHED, stats={"records": 1})
    release.stats = {"records": 1.0}
    with pytest.raises(ImmutableRecordError, match="DatasetRelease.stats"):
        release.save()
    assert stored(release)["stats"] == {"records": 1}


@pytest.mark.parametrize(
    "value", [{"n": -0.0}, {"n": 1e16}], ids=["negative zero", "1e16"]
)
def test_a_value_the_database_keeps_in_its_own_form_is_unchanged(db, value):
    """PostgreSQL's jsonb gives -0.0 back as 0.0 and 1e16 as the integer
    10000000000000000: an instance still holding the value it was saved with
    is saved again unchanged, not refused as a change."""
    record = SourceRecordFactory(raw=value)
    record.status = RecordStatus.REJECTED
    record.save()
    assert stored(record)["status"] == RecordStatus.REJECTED
    release = advance(DatasetReleaseFactory(stats=value), PUBLISHED)
    move(release, WITHDRAWN)
    assert stored(release)["status"] == WITHDRAWN


@pytest.mark.parametrize(
    "stored_raw,retyped",
    [
        ({"n": 1e16}, {"n": 10**16}),
        ({"n": 10**16}, {"n": 1e16}),
        ({"n": -0.0}, {"n": 0.0}),
        ({"n": 0.0}, {"n": -0.0}),
    ],
    ids=["1e16 to an integer", "an integer to 1e16", "-0.0 to 0.0", "0.0 to -0.0"],
)
def test_a_stored_number_keeps_its_json_form_on_every_backend(db, stored_raw, retyped):
    """PostgreSQL's jsonb keeps one form of each of these numbers, so there
    the save writes what is stored already and is allowed; SQLite keeps the
    text json.dumps wrote, so there the save would re-type the stored value,
    and is refused."""
    record = SourceRecordFactory(raw=stored_raw)
    advance(record.release, STAGED)
    before = stored(record)
    record.raw = retyped
    record.status = RecordStatus.REJECTED
    if connection.vendor == "postgresql":
        record.save()
        assert stored(record)["status"] == RecordStatus.REJECTED
    else:
        with pytest.raises(ImmutableRecordError, match="SourceRecord.raw"):
            record.save()
        assert stored(record) == before
    # == finds 1e16 equal to 10**16 and -0.0 to 0.0; their JSON text differs.
    assert json.dumps(stored(record)["raw"]) == json.dumps(before["raw"])


@pytest.mark.parametrize(
    "stored_raw,retyped",
    [
        ({"true": "x"}, {True: "y", "true": "x"}),
        ({"null": "x"}, {None: "y", "null": "x"}),
        ({"1": "x"}, {1: "y", "1": "x"}),
    ],
    ids=["true", "null", "1"],
)
def test_keys_json_writes_alike_are_a_change(db, stored_raw, retyped):
    """{True: .., "true": ..} would be written as one JSON object with a
    repeated key; a stored value is never re-written that way."""
    record = SourceRecordFactory(raw=stored_raw)
    advance(record.release, STAGED)
    before = stored(record)
    record.raw = retyped
    record.status = RecordStatus.REJECTED
    with pytest.raises(ImmutableRecordError, match="SourceRecord.raw"):
        record.save()
    assert stored(record) == before


def test_a_published_releases_stats_keys_never_collapse(db):
    release = advance(DatasetReleaseFactory(stats={"true": 1}), PUBLISHED)
    before = stored(release)
    release.stats = {True: 2, "true": 1}
    with pytest.raises(ImmutableRecordError):
        release.save()
    assert stored(release) == before


# ── A record with evidence stays mapped ─────────────────────────────────────


def _record_with_evidence():
    assertion = EvidenceAssertionFactory()
    return SourceRecord.objects.get(pk=assertion.source_record_id)


@pytest.mark.parametrize("path", WRITE_PATHS)
@pytest.mark.parametrize("status", [RecordStatus.REJECTED, RecordStatus.UNRESOLVED])
def test_a_record_with_evidence_keeps_its_mapped_status(db, path, status):
    record = _record_with_evidence()
    before = stored(record)
    with pytest.raises(ImmutableRecordError, match="SourceRecord.status: stays"):
        write(path, record, status=status)
    assert stored(record) == before


@pytest.mark.parametrize("path", ["update", "bulk_update"])
def test_a_queryset_naming_the_status_of_a_record_with_evidence_is_refused(db, path):
    """The hook sees names, not values: even mapped to mapped."""
    record = _record_with_evidence()
    other = mapped_record(record.release)
    with pytest.raises(ImmutableRecordError, match=f"record {record.pk} has"):
        if path == "update":
            SourceRecord.objects.filter(pk__in=[record.pk, other.pk]).update(
                status=RecordStatus.MAPPED
            )
        else:
            SourceRecord.objects.bulk_update([other, record], ["status"])
    assert stored(record)["status"] == RecordStatus.MAPPED


def test_a_record_with_evidence_still_takes_issues_and_its_mapped_status(db):
    record = _record_with_evidence()
    record.issues = ["re-read by run 2"]
    record.save()
    record.status = RecordStatus.MAPPED
    record.save(update_fields=["status"])
    # And a record without evidence changes status as before.
    other = mapped_record(record.release)
    SourceRecord.objects.filter(pk=other.pk).update(status=RecordStatus.REJECTED)
    assert stored(record)["issues"] == ["re-read by run 2"]
    assert stored(other)["status"] == RecordStatus.REJECTED


# ── A publication's synthetic flag is fixed when it is created ──────────────


@pytest.mark.parametrize("path", ["save", "save_update_fields"])
def test_a_publications_synthetic_flag_is_fixed_at_creation(db, path):
    # False counted as blank among the set-once identifiers, so a save could
    # make a paper synthetic.
    paper = Publication(local_dataset=SourceDatasetFactory(), local_key="p1")
    paper.save()
    with pytest.raises(
        ImmutableRecordError, match="Publication.is_synthetic: is fixed"
    ):
        write(path, paper, is_synthetic=True)
    assert stored(paper)["is_synthetic"] is False
    synthetic = Publication(
        local_dataset=SourceDatasetFactory(), local_key="s1", is_synthetic=True
    )
    synthetic.save()
    with pytest.raises(ImmutableRecordError, match="Publication.is_synthetic"):
        write(path, synthetic, is_synthetic=False)
    assert stored(synthetic)["is_synthetic"] is True


# ── Entities and the releases that introduce them ───────────────────────────


def test_entities_created_without_a_release_name_none(registry):
    entity = create_entity("protein", "TP53", 9606, "uniprotkb", "P04637")
    entity.refresh_from_db()
    assert entity.created_by_release is None


def test_a_release_that_created_entities_cannot_be_deleted(db):
    release = DatasetReleaseFactory()
    entity = EntityFactory(created_by_release=release)
    with pytest.raises(ProtectedError):
        release.delete()
    assert list(release.created_entities.all()) == [entity]


def _rename_introducer(entity, other, path):
    if path == "clear":
        entity.created_by_release.created_entities.clear()
    elif path == "remove":
        entity.created_by_release.created_entities.remove(entity)
    elif path == "set":
        other.created_entities.set([entity])
    elif path == "update":
        Entity.objects.filter(pk=entity.pk).update(created_by_release=other)
    elif path == "update_to_null":
        Entity.objects.filter(pk=entity.pk).update(created_by_release_id=None)
    else:
        entity.created_by_release = other
        if path == "bulk_update":
            Entity.objects.bulk_update([entity], ["created_by_release"])
        elif path == "save":
            entity.save()
        else:
            entity.save(update_fields=["created_by_release"])


@pytest.mark.parametrize(
    "path",
    [
        "clear",
        "remove",
        "set",
        "update",
        "update_to_null",
        "bulk_update",
        "save",
        "save_update_fields",
    ],
)
def test_the_release_that_introduced_an_entity_is_set_once(db, path):
    release, other = DatasetReleaseFactory(), DatasetReleaseFactory()
    entity = EntityFactory(created_by_release=release)
    before = stored(entity)
    with pytest.raises(ImmutableRecordError, match="Entity.created_by_release"):
        _rename_introducer(entity, other, path)
    assert stored(entity) == before
    # So the release keeps its entity: it is not deleted, and its kind holds.
    with pytest.raises(ProtectedError):
        DatasetRelease.objects.get(pk=release.pk).delete()
    release.kind = ReleaseKind.REFERENCE
    with pytest.raises(ImmutableRecordError, match="DatasetRelease.kind"):
        release.save()


def test_an_entity_without_an_introducing_release_may_be_given_one(db):
    release = DatasetReleaseFactory()
    by_update, by_save = EntityFactory(), EntityFactory()
    Entity.objects.filter(pk=by_update.pk).update(created_by_release=release)
    by_save.created_by_release = release
    by_save.save()
    # Saving an entity with the release it has changes nothing.
    by_save.label = "Renamed"
    by_save.save()
    assert set(release.created_entities.all()) == {by_update, by_save}


def test_a_release_kind_is_fixed_once_an_entity_names_the_release(db):
    release = DatasetReleaseFactory()
    EntityFactory(created_by_release=release)
    release.kind = ReleaseKind.REFERENCE
    with pytest.raises(ImmutableRecordError, match="DatasetRelease.kind"):
        release.save()
    assert stored(release)["kind"] == ReleaseKind.EVIDENCE


def test_two_datasets_releases_keep_separate_provenance_for_one_entity(db):
    first, second = DatasetReleaseFactory(), DatasetReleaseFactory()
    assert first.dataset_id != second.dataset_id
    entity = EntityFactory(created_by_release=first)
    rows = {}
    for release in (first, second):
        file = SourceFileFactory(release=release, sha256="b" * 64)
        rows[release.pk] = SourceRecordFactory(
            release=release, file=file, record_key="row-1", raw={"regulator": "SYN-A"}
        )
    # The same file, key and row in both, each release's own.
    assert rows[first.pk].pk != rows[second.pk].pk
    assert list(first.created_entities.all()) == [entity]
    assert not second.created_entities.exists()
    second.delete()
    assert list(first.records.all()) == [rows[first.pk]]
    entity.refresh_from_db()
    assert entity.created_by_release_id == first.pk


# ── create_entity(release=...): the release that introduces an entity ──────


def introducing(kind=ReleaseKind.EVIDENCE, status=DRAFT, **fields):
    """A release with an effective internal approval, which may introduce
    entities while it is a draft or staged."""
    release = approvable_release(kind=kind, status=status, **fields)
    approve(release, ApprovalScope.INTERNAL)
    return release


def tp53(release):
    return create_entity(
        "protein", "TP53", 9606, "uniprotkb", "P04637", release=release
    )


def synthetic_protein(release, value="SYN-P1", taxon=32630):
    return create_entity(
        "protein", value, taxon, "synthetic_protein", value, release=release
    )


def test_no_release_introduces_an_entity_while_the_switch_is_off(registry, settings):
    releases = [introducing(), DatasetReleaseFactory(is_synthetic=True)]
    settings.TYPED_GRAPH_ENABLED = False
    for release in releases:
        with pytest.raises(ReleaseProvenanceError, match="TYPED_GRAPH_ENABLED"):
            tp53(release)
        with pytest.raises(ReleaseProvenanceError, match="TYPED_GRAPH_ENABLED"):
            synthetic_protein(release)
    assert issubclass(ReleaseProvenanceError, EntityError)
    assert not Entity.objects.exists()
    assert not EntityIdentifier.objects.exists()


@pytest.mark.parametrize("status", [DRAFT, STAGED])
def test_an_entity_a_release_introduces_names_it_and_is_provisional(registry, status):
    release = introducing(status=status)
    entity = tp53(release)
    row = stored(entity)
    assert (row["created_by_release_id"], row["status"]) == (
        release.pk,
        EntityStatus.PROVISIONAL,
    )
    assert list(release.created_entities.all()) == [entity]
    primary = EntityIdentifier.objects.get(entity=entity)
    assert (primary.role, primary.lookup) == ("primary", "P04637")
    # The release's id does as well as the row.
    other = create_entity(
        "protein", "MDM2", 9606, "uniprotkb", "Q00987", release=release.pk
    )
    assert stored(other)["created_by_release_id"] == release.pk


def test_a_reference_release_introduces_active_entities(registry):
    release = introducing(kind=ReleaseKind.REFERENCE)
    entity = tp53(release)
    assert (stored(entity)["status"], entity.created_by_release_id) == (
        EntityStatus.ACTIVE,
        release.pk,
    )


def test_an_existing_entity_is_returned_unchanged(registry):
    first = introducing()
    entity = tp53(first)
    before = stored(entity)
    # A later release, even a reference one of another dataset, finds it as
    # the first writer left it.
    later = introducing(kind=ReleaseKind.REFERENCE)
    assert later.dataset_id != first.dataset_id
    again = create_entity(
        "protein",
        "Cellular tumor antigen p53",
        9606,
        "uniprotkb",
        "p04637",
        release=later,
    )
    assert again.pk == entity.pk
    assert stored(entity) == before
    assert not later.created_entities.exists()
    # The same for an entity created without a release.
    plain = create_entity("protein", "MDM2", 9606, "uniprotkb", "Q00987")
    plain_row = stored(plain)
    assert (
        create_entity("protein", "MDM2", 9606, "uniprotkb", "Q00987", release=later).pk
        == plain.pk
    )
    assert stored(plain) == plain_row
    assert plain_row["created_by_release_id"] is None


@pytest.mark.parametrize("status", [PUBLISHED, SUPERSEDED, WITHDRAWN])
def test_a_release_past_staged_introduces_no_entity(registry, status):
    release = introducing(status=status)
    with pytest.raises(ReleaseProvenanceError, match=f"is {status}: only a draft"):
        tp53(release)
    assert not Entity.objects.exists()
    # Nor does it get an entity that exists already.
    existing = create_entity("protein", "TP53", 9606, "uniprotkb", "P04637")
    with pytest.raises(ReleaseProvenanceError, match=f"is {status}"):
        tp53(release)
    assert stored(existing)["created_by_release_id"] is None


def _no_decision():
    return approvable_release()


def _internal_denied():
    release = approvable_release()
    UsageApprovalFactory(release=release, decision=Decision.DENIED, licence=None)
    return release


def _public_view_only():
    release = approvable_release()
    approve(release, ApprovalScope.PUBLIC_VIEW)
    return release


def _internal_revoked():
    release = introducing()
    actor = StaffUserFactory()
    revoke_decision(release.approvals.get(), reason="Ended.", revoked_by=actor)
    return release


def _diligence_lapsed(**change):
    def build():
        release = introducing()
        record = DueDiligenceRecord.objects.get(dataset=release.dataset)
        for name, value in change.items():
            setattr(record, name, value)
        record._changed_by = StaffUserFactory()
        record.save()
        return release

    return build


def _no_due_diligence():
    release = DatasetReleaseFactory()
    ReleaseLicenceFactory(release=release, open_terms=True)
    approve(release, ApprovalScope.INTERNAL)
    return release


def _embargoed_but_internal():
    # Not a refusal: an embargo leaves internal effective.
    return introducing(embargo_until=timezone.now() + datetime.timedelta(days=1))


NOT_INTRODUCING = {
    "no decision": _no_decision,
    "internal denied": _internal_denied,
    "public view without internal": _public_view_only,
    "internal revoked": _internal_revoked,
    "due diligence reopened": _diligence_lapsed(status=DueDiligenceStatus.OPEN),
    "due diligence blocked": _diligence_lapsed(status=DueDiligenceStatus.BLOCKED),
    "terms time-limited": _diligence_lapsed(terms_time_limited=True),
    "no due-diligence record": _no_due_diligence,
}


@pytest.mark.parametrize("case", sorted(NOT_INTRODUCING))
def test_a_release_without_an_effective_internal_approval_introduces_none(
    registry, case
):
    release = NOT_INTRODUCING[case]()
    with pytest.raises(ReleaseProvenanceError, match="no effective internal"):
        tp53(release)
    assert not Entity.objects.exists()
    assert not EntityIdentifier.objects.exists()


def test_an_embargo_does_not_stop_a_release_introducing_entities(registry):
    release = _embargoed_but_internal()
    assert tp53(release).created_by_release_id == release.pk


def test_a_synthetic_release_introduces_only_synthetic_constructs(registry):
    # It needs no approval: synthetic releases have no due diligence to do.
    release = DatasetReleaseFactory(is_synthetic=True)
    entity = synthetic_protein(release)
    assert (entity.created_by_release_id, entity.taxon_id) == (release.pk, 32630)
    gene = create_entity(
        "gene", "SYN-G1", 32630, "synthetic_gene", "SYN-G1", release=release
    )
    assert gene.created_by_release_id == release.pk
    with pytest.raises(ReleaseProvenanceError, match="synthetic constructs"):
        tp53(release)
    assert set(release.created_entities.all()) == {entity, gene}
    assert Entity.objects.count() == 2


def test_an_unknown_release_introduces_nothing(registry):
    for release in (10**12, "1", DatasetReleaseFactory.build()):
        with pytest.raises(ReleaseProvenanceError, match="Unknown release"):
            tp53(release)
    assert not Entity.objects.exists()


def test_without_a_release_create_entity_behaves_as_in_g1(registry, settings):
    # Whatever the switch: G1's write path never read it.
    settings.TYPED_GRAPH_ENABLED = False
    entity = create_entity("protein", "TP53", 9606, "uniprotkb", "P04637")
    row = stored(entity)
    assert (row["status"], row["created_by_release_id"]) == (
        EntityStatus.PROVISIONAL,
        None,
    )
    assert create_entity("protein", "p53", 9606, "uniprotkb", "P04637").pk == entity.pk


def test_two_datasets_releases_naming_one_entity_keep_their_own_provenance(registry):
    first, second = approvable_release(), approvable_release()
    assert first.dataset_id != second.dataset_id
    # A release's files and records come before anyone decides on it.
    records = {
        release.pk: SourceRecordFactory(
            release=release, record_key="row-1", raw={"protein": "P04637"}
        )
        for release in (first, second)
    }
    for release in (first, second):
        approve(release, ApprovalScope.INTERNAL)
    entity = tp53(first)
    assert tp53(second).pk == entity.pk
    assert stored(entity)["created_by_release_id"] == first.pk
    assert list(first.created_entities.all()) == [entity]
    assert not second.created_entities.exists()
    # The same row of the same file, each release's own record.
    assert records[first.pk].pk != records[second.pk].pk
    assert list(first.records.all()) == [records[first.pk]]
    assert list(second.records.all()) == [records[second.pk]]


@pytest.mark.postgres
def test_introducing_an_entity_locks_the_release_before_writing(registry):
    release = introducing()
    with CaptureQueriesContext(connection) as queries:
        tp53(release)
    sql = [query["sql"] for query in queries.captured_queries]
    locking = [
        n
        for n, text in enumerate(sql)
        if "FOR UPDATE" in text and 'FROM "graph_datasetrelease"' in text
    ]
    inserting = [
        n for n, text in enumerate(sql) if 'INSERT INTO "graph_entity"' in text
    ]
    assert locking and inserting and locking[0] < inserting[0]


@pytest.mark.postgres
@pytest.mark.django_db(transaction=True)
def test_an_entity_and_a_change_of_its_releases_kind_never_disagree(registry):
    """Both lock the release. An entity introduced after a kind change waits
    for it and takes the new kind; a kind change after an entity waits for
    it and is refused."""
    for first, value in (("kind change", "SYN-PA"), ("entity", "SYN-PB")):
        release = DatasetReleaseFactory(is_synthetic=True)
        outcome = {}

        def change_kind():
            fresh = DatasetRelease.objects.get(pk=release.pk)
            fresh.kind = ReleaseKind.REFERENCE
            try:
                fresh.save()
            except ImmutableRecordError as exc:
                outcome["refused"] = str(exc)

        def introduce():
            outcome["entity"] = synthetic_protein(release.pk, value)

        order = (
            (change_kind, introduce)
            if first == "kind change"
            else (introduce, change_kind)
        )
        assert _race(*order) == [], first
        entity = Entity.objects.get(pk=outcome["entity"].pk)
        kind = DatasetRelease.objects.get(pk=release.pk).kind
        if first == "kind change":
            assert (kind, entity.status) == (ReleaseKind.REFERENCE, EntityStatus.ACTIVE)
            assert "refused" not in outcome
        else:
            assert (kind, entity.status) == (
                ReleaseKind.EVIDENCE,
                EntityStatus.PROVISIONAL,
            )
            assert "DatasetRelease.kind" in outcome["refused"]


def _race(first, second):
    """Run `first` in a transaction held open until `second`, started
    meanwhile on another connection, waits on a lock; then let both finish.
    Returns what either raised."""
    holding, go = threading.Event(), threading.Event()
    errors = []

    def run_first():
        try:
            with transaction.atomic():
                first()
                holding.set()
                go.wait(30)
        except Exception as exc:  # reported by the caller
            errors.append(exc)
        finally:
            connection.close()

    def run_second():
        try:
            holding.wait(30)
            second()
        except Exception as exc:
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=run_first), threading.Thread(target=run_second)]
    for thread in threads:
        thread.start()
    try:
        assert holding.wait(30), "the first transaction never started"
        assert _a_backend_waits_on_a_lock(), "the second never waited"
    finally:
        go.set()
        for thread in threads:
            thread.join(30)
    return errors

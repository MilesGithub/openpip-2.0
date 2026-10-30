"""Licensing records: due-diligence records with their history, and licence
snapshots.

The first part tests the records' own rules, which hold on every write path
(graph/models.py). The services in graph/licensing.py are tested after it.
"""

import datetime
import threading
import time

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError
from django.forms import modelform_factory
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from graph.approvals import (
    ApprovalError,
    record_decision,
    replace_decision,
    revoke,
    stale_approvals,
)
from graph.licensing import (
    LICENCE_FIELDS,
    LicensingError,
    confirm_due_diligence,
    latest_licences,
    record_licence,
)
from graph.models import (
    DUE_DILIGENCE_COMPLETE_RULE,
    ApprovalScope,
    Decision,
    DueDiligenceChange,
    DueDiligenceRecord,
    DueDiligenceStatus,
    ImmutableRecordError,
    Permission,
    ReleaseLicence,
    ShareAlike,
    SourceDataset,
)
from graph.tests.factories import (
    DatasetReleaseFactory,
    DueDiligenceChangeFactory,
    DueDiligenceRecordFactory,
    ReleaseLicenceFactory,
    SourceDatasetFactory,
    StaffUserFactory,
    approvable_release,
    confirm_diligence,
)

COMPLETE = DueDiligenceStatus.COMPLETE
OPEN = DueDiligenceStatus.OPEN
BLOCKED = DueDiligenceStatus.BLOCKED
# The fields whose every change the history keeps.
AUDITED = {
    "status": BLOCKED,
    "terms_time_limited": True,
    "reviewer": "Another reviewer",
    "reviewed_on": datetime.date(2026, 10, 2),
    "notes_link": "docs/project/generalization/DATASET_DUE_DILIGENCE.md#trrust",
}


def stored(obj) -> dict:
    return type(obj)._base_manager.filter(pk=obj.pk).values().get()


def save_as(record, actor, note="", **changes):
    for name, value in changes.items():
        setattr(record, name, value)
    record._changed_by = actor
    record._change_note = note
    record.save()


# ── Record rules: due diligence ─────────────────────────────────────────────


def test_a_dataset_has_at_most_one_due_diligence_record(db):
    record = DueDiligenceRecordFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        DueDiligenceRecordFactory(dataset=record.dataset)
    assert DueDiligenceRecord.objects.count() == 1
    dataset = SourceDataset.objects.get(pk=record.dataset_id)
    assert dataset.due_diligence == record


def test_creating_a_record_writes_its_first_history_row(db):
    actor = StaffUserFactory(username="reviewer-one")
    record = DueDiligenceRecordFactory(changed_by=actor, change_note="First look.")
    [change] = record.history.all()
    assert (change.status, change.terms_time_limited, change.completed_at) == (
        OPEN,
        False,
        None,
    )
    assert (change.reviewer, change.notes_link) == (record.reviewer, record.notes_link)
    assert (change.changed_by, change.changed_by_username, change.note) == (
        actor,
        "reviewer-one",
        "First look.",
    )


@pytest.mark.parametrize("field", sorted(AUDITED))
def test_every_audited_change_writes_one_history_row(db, field):
    record = DueDiligenceRecordFactory()
    actor = StaffUserFactory()
    save_as(record, actor, note="Changed.", **{field: AUDITED[field]})
    assert record.history.count() == 2
    latest = record.history.last()
    assert getattr(latest, field) == AUDITED[field]
    assert (latest.changed_by, latest.note) == (actor, "Changed.")
    # The row states every audited value after the change.
    row = stored(record)
    for name in DueDiligenceRecord.AUDITED_FIELDS:
        assert getattr(latest, name) == row[name], name


def test_an_edit_of_the_notes_alone_writes_no_history_and_needs_no_actor(db):
    record = DueDiligenceRecordFactory()
    record.notes = "Plain-text working notes."
    record.save()
    assert stored(record)["notes"] == "Plain-text working notes."
    assert record.history.count() == 1


def actor_of(kind):
    if kind == "none":
        return None
    if kind == "unsaved":
        return StaffUserFactory.build()
    if kind == "not staff":
        return StaffUserFactory(is_staff=False)
    return StaffUserFactory(is_active=False)


@pytest.mark.parametrize("actor", ["none", "unsaved", "not staff", "inactive staff"])
def test_an_audited_change_without_an_active_staff_actor_is_refused(db, actor):
    dataset = SourceDatasetFactory()
    with pytest.raises(ImmutableRecordError, match="names its actor"):
        DueDiligenceRecordFactory(dataset=dataset, changed_by=actor_of(actor))
    assert not DueDiligenceRecord.objects.exists()
    assert not DueDiligenceChange.objects.exists()

    record = DueDiligenceRecordFactory(dataset=dataset)
    before = stored(record)
    for field, value in AUDITED.items():
        fresh = DueDiligenceRecord.objects.get(pk=record.pk)
        with pytest.raises(ImmutableRecordError, match="names its actor"):
            save_as(fresh, actor_of(actor), **{field: value})
    assert stored(record) == before
    assert record.history.count() == 1


def test_the_actor_and_note_are_cleared_after_each_save(db):
    record = DueDiligenceRecordFactory()
    save_as(record, StaffUserFactory(), status=BLOCKED)
    assert (record._changed_by, record._change_note) == (None, "")
    record.status = OPEN
    with pytest.raises(ImmutableRecordError, match="names its actor"):
        record.save()
    assert stored(record)["status"] == BLOCKED


def test_complete_needs_a_reviewer_and_a_notes_link(db):
    for missing in ("reviewer", "notes_link"):
        with pytest.raises(IntegrityError), transaction.atomic():
            DueDiligenceRecordFactory(status=COMPLETE, **{missing: ""})
    assert not DueDiligenceRecord.objects.exists()
    record = DueDiligenceRecordFactory(reviewer="")
    record.status = COMPLETE
    with pytest.raises(ValidationError) as invalid:
        record.validate_constraints()
    assert (
        "Due diligence is complete only with a reviewer and a link to the "
        "reviewed notes." in invalid.value.messages
    )


def test_completed_at_is_set_cleared_and_set_again(db):
    actor = StaffUserFactory()
    record = DueDiligenceRecordFactory()
    assert stored(record)["completed_at"] is None

    save_as(record, actor, status=COMPLETE)
    first = stored(record)["completed_at"]
    assert first is not None

    # A save that leaves the status complete keeps it, whatever is typed.
    record.completed_at = datetime.datetime(2000, 1, 1, tzinfo=datetime.timezone.utc)
    record.notes = "Typed a date."
    record.save()
    assert stored(record)["completed_at"] == first

    # A re-confirmation sets it again (licensing.confirm_due_diligence).
    record._reconfirm = True
    save_as(record, actor, note="Covers the new terms.")
    confirmed = stored(record)["completed_at"]
    assert confirmed > first
    assert record._reconfirm is False
    assert record.history.last().completed_at == confirmed

    # Leaving complete clears it; completing again sets a new time.
    save_as(record, actor, status=OPEN)
    assert stored(record)["completed_at"] is None
    save_as(record, actor, status=COMPLETE)
    assert stored(record)["completed_at"] > confirmed
    assert record.history.count() == 5


def test_a_re_confirmation_needs_a_complete_record(db):
    record = DueDiligenceRecordFactory()
    record._reconfirm = True
    with pytest.raises(ImmutableRecordError, match="complete record"):
        save_as(record, StaffUserFactory())
    assert stored(record)["completed_at"] is None
    assert record.history.count() == 1


def test_a_form_completes_a_record_without_typing_completed_at(db):
    # completed_at is not editable, so a model form (the admin's) leaves it
    # to save(), and skips the check that needs it; the reviewer and notes
    # link check still shows its message.
    Form = modelform_factory(
        DueDiligenceRecord,
        fields=["status", "reviewer", "reviewed_on", "notes_link", "notes"],
    )
    record = DueDiligenceRecordFactory()
    assert "completed_at" not in Form().fields
    form = Form({"status": COMPLETE, "reviewer": "", "notes_link": ""}, instance=record)
    assert not form.is_valid()
    assert (
        "Due diligence is complete only with a reviewer and a link to the "
        "reviewed notes." in form.non_field_errors()
    )
    form = Form(
        {"status": COMPLETE, "reviewer": "Reviewer", "notes_link": "README.md"},
        instance=DueDiligenceRecord.objects.get(pk=record.pk),
    )
    assert form.is_valid(), form.errors
    completed = form.save(commit=False)
    completed._changed_by = StaffUserFactory()
    completed.save()
    assert stored(record)["completed_at"] is not None


def test_a_partial_save_of_the_status_sets_completed_at_with_it(db):
    record = DueDiligenceRecordFactory()
    record.status = COMPLETE
    record._changed_by = StaffUserFactory()
    record.save(update_fields=["status"])
    row = stored(record)
    assert (row["status"], row["completed_at"] is not None) == (COMPLETE, True)


@pytest.mark.parametrize(
    "link",
    [
        "https://example.org/notes",
        "http://example.org/notes#section",
        "docs/project/generalization/DATASET_DUE_DILIGENCE.md#trrust",
        ".github/workflows/ci.yml",
        "README.md",
    ],
)
def test_a_notes_link_is_an_http_url_or_a_repository_path(db, link):
    record = DueDiligenceRecordFactory(notes_link=link)
    assert stored(record)["notes_link"] == link
    record.full_clean()


@pytest.mark.parametrize(
    "link",
    [
        "javascript:alert(1)",
        "JavaScript:alert(1)",
        "file:///etc/passwd",
        "data:text/html,<b>x</b>",
        "ftp://example.org/notes",
        "mailto:curator@example.org",
        "/etc/passwd",
        "//example.org/notes",
        "../secrets.md",
        "docs/../../secrets.md",
        "docs/notes file.md",
        "C:\\notes.md",
        "docs/notes.md#",
        " docs/notes.md",
    ],
)
def test_any_other_notes_link_is_refused(db, link):
    with pytest.raises(ImmutableRecordError, match="DueDiligenceRecord.notes_link"):
        DueDiligenceRecordFactory(notes_link=link)
    record = DueDiligenceRecordFactory()
    with pytest.raises(ImmutableRecordError, match="DueDiligenceRecord.notes_link"):
        save_as(record, StaffUserFactory(), notes_link=link)
    assert stored(record)["notes_link"] == (
        "docs/project/generalization/DATASET_DUE_DILIGENCE.md"
    )
    # A form sees the same rule through the field's validator.
    record.notes_link = link
    with pytest.raises(ValidationError):
        record.full_clean()


def test_a_record_stays_with_its_dataset_which_it_keeps(db):
    record = DueDiligenceRecordFactory()
    before = stored(record)
    with pytest.raises(ImmutableRecordError, match="DueDiligenceRecord.dataset"):
        save_as(record, StaffUserFactory(), dataset=SourceDatasetFactory())
    assert stored(record) == before
    assert record.history.count() == 1
    with pytest.raises(ProtectedError):
        DueDiligenceRecord.objects.get(pk=record.pk).dataset.delete()


@pytest.mark.parametrize(
    "path", ["delete", "queryset_delete", "update", "bulk_update", "bulk_create"]
)
def test_a_due_diligence_record_is_never_deleted_or_written_in_bulk(db, path):
    record = DueDiligenceRecordFactory()
    before = stored(record)
    with pytest.raises(ImmutableRecordError, match="DueDiligenceRecord"):
        if path == "delete":
            record.delete()
        elif path == "queryset_delete":
            DueDiligenceRecord.objects.filter(pk=record.pk).delete()
        elif path == "update":
            DueDiligenceRecord.objects.filter(pk=record.pk).update(status=BLOCKED)
        elif path == "bulk_update":
            record.notes = "In bulk."
            DueDiligenceRecord.objects.bulk_update([record], ["notes"])
        else:
            DueDiligenceRecord.objects.bulk_create(
                [DueDiligenceRecord(dataset=SourceDatasetFactory())]
            )
    assert stored(record) == before
    assert DueDiligenceRecord.objects.count() == 1


@pytest.mark.parametrize(
    "path",
    [
        "save",
        "save_unchanged",
        "update",
        "bulk_update",
        "delete",
        "queryset_delete",
        "bulk_create",
    ],
)
def test_the_due_diligence_history_is_append_only(db, path):
    record = DueDiligenceRecordFactory()
    change = record.history.get()
    before = stored(change)
    with pytest.raises(ImmutableRecordError, match="DueDiligenceChange"):
        if path == "save":
            change.note = "Rewritten."
            change.save()
        elif path == "save_unchanged":
            change.save()
        elif path == "update":
            DueDiligenceChange.objects.filter(pk=change.pk).update(note="Rewritten.")
        elif path == "bulk_update":
            change.note = "Rewritten."
            DueDiligenceChange.objects.bulk_update([change], ["note"])
        elif path == "delete":
            change.delete()
        elif path == "queryset_delete":
            record.history.all().delete()
        else:
            DueDiligenceChange.objects.bulk_create([DueDiligenceChangeFactory.build()])
    assert stored(change) == before
    assert DueDiligenceChange.objects.count() == 1


def test_a_history_row_states_its_records_values(db):
    record = DueDiligenceRecordFactory()
    # Written directly, a row must say what the record says now.
    DueDiligenceChangeFactory(record=record)
    with pytest.raises(ImmutableRecordError, match="DueDiligenceChange.status"):
        DueDiligenceChangeFactory(record=record, status=COMPLETE)
    with pytest.raises(IntegrityError), transaction.atomic():
        DueDiligenceChangeFactory(record=record, changed_by=None)
    # The status check holds in the database too; only the plain base
    # manager gets past the guard to show it.
    with pytest.raises(IntegrityError), transaction.atomic():
        DueDiligenceChange._base_manager.bulk_create(
            [
                DueDiligenceChangeFactory.build(
                    record=record,
                    status="finished",
                    changed_by=None,
                    changed_by_username="graph-staff",
                )
            ]
        )
    assert record.history.count() == 2
    with pytest.raises(ProtectedError):
        DueDiligenceRecord._base_manager.filter(pk=record.pk).delete()


def test_the_history_is_ordered_by_time_then_id(db):
    record = DueDiligenceRecordFactory()
    actor = StaffUserFactory()
    for status in (BLOCKED, OPEN, COMPLETE):
        save_as(record, actor, status=status)
    assert [change.status for change in record.history.all()] == [
        OPEN,
        BLOCKED,
        OPEN,
        COMPLETE,
    ]


def test_deleting_the_actors_account_keeps_the_history_and_the_name(db):
    actor = StaffUserFactory(username="departing")
    record = DueDiligenceRecordFactory(changed_by=actor)
    actor.delete()
    [change] = DueDiligenceChange.objects.filter(record=record)
    assert (change.changed_by_id, change.changed_by_username) == (None, "departing")


# ── Record rules: licence snapshots ─────────────────────────────────────────


def test_a_new_snapshot_knows_nothing_until_a_person_records_it(db):
    release = DatasetReleaseFactory()
    licence = ReleaseLicence(release=release, recorded_by=StaffUserFactory())
    licence.save()
    row = stored(licence)
    for name in ("redistribution", "commercial_use", "modification"):
        assert row[name] == Permission.UNKNOWN, name
    assert row["share_alike"] == ShareAlike.UNKNOWN
    for name in (
        "licence_name",
        "spdx_id",
        "licence_url",
        "terms_quoted",
        "read_at",
        "attribution",
        "obligations",
        "ambiguity_notes",
        "reviewer",
    ):
        assert row[name] == "", name
    assert row["read_on"] is None
    assert licence.records_terms is False


def test_a_snapshot_names_who_recorded_it(db):
    actor = StaffUserFactory(username="licence-reader")
    licence = ReleaseLicenceFactory(recorded_by=actor)
    assert stored(licence)["recorded_by_username"] == "licence-reader"
    with pytest.raises(IntegrityError), transaction.atomic():
        ReleaseLicenceFactory(recorded_by=None)
    actor.delete()
    row = stored(licence)
    assert (row["recorded_by_id"], row["recorded_by_username"]) == (
        None,
        "licence-reader",
    )


@pytest.mark.parametrize(
    "path",
    [
        "save",
        "save_unchanged",
        "save_update_fields",
        "update",
        "bulk_update",
        "delete",
        "queryset_delete",
    ],
)
def test_a_snapshot_is_immutable_from_insertion(db, path):
    licence = ReleaseLicenceFactory()
    before = stored(licence)
    with pytest.raises(ImmutableRecordError, match="ReleaseLicence"):
        if path == "save":
            licence.redistribution = Permission.PERMITTED
            licence.save()
        elif path == "save_unchanged":
            licence.save()
        elif path == "save_update_fields":
            licence.licence_name = "Corrected"
            licence.save(update_fields=["licence_name"])
        elif path == "update":
            ReleaseLicence.objects.filter(pk=licence.pk).update(
                redistribution=Permission.PERMITTED
            )
        elif path == "bulk_update":
            licence.share_alike = ShareAlike.NO
            ReleaseLicence.objects.bulk_update([licence], ["share_alike"])
        elif path == "delete":
            licence.delete()
        else:
            ReleaseLicence.objects.filter(pk=licence.pk).delete()
    assert stored(licence) == before


def test_snapshots_are_not_inserted_in_bulk(db):
    release = DatasetReleaseFactory()
    with pytest.raises(ImmutableRecordError, match="ReleaseLicence"):
        ReleaseLicence.objects.bulk_create(
            [ReleaseLicence(release=release, recorded_by_username="graph-staff")]
        )
    assert not ReleaseLicence.objects.exists()


def test_a_correction_is_a_new_snapshot_and_the_latest_has_the_highest_id(db):
    first = ReleaseLicenceFactory(recorded=True)
    # Recorded with an earlier time, it is still the latest: ids decide.
    correction = ReleaseLicenceFactory(
        release=first.release,
        recorded=True,
        redistribution=Permission.NOT_PERMITTED,
        recorded_at=first.recorded_at - datetime.timedelta(days=1),
    )
    assert correction.pk > first.pk
    assert first.release.licences.order_by("-pk").first() == correction
    assert stored(first)["redistribution"] == Permission.UNKNOWN


@pytest.mark.parametrize(
    "fields,records",
    [
        ({}, False),
        ({"recorded": True}, True),
        ({"recorded": True, "licence_name": "", "terms_quoted": "Free to use."}, True),
        ({"recorded": True, "licence_name": "", "terms_quoted": ""}, False),
        ({"recorded": True, "licence_name": "   "}, False),
        ({"recorded": True, "read_at": ""}, False),
        ({"recorded": True, "read_on": None}, False),
    ],
)
def test_a_snapshot_records_terms_only_with_where_and_when_they_were_read(
    db, fields, records
):
    assert ReleaseLicenceFactory(**fields).records_terms is records


def test_a_release_with_licence_snapshots_cannot_be_deleted(db):
    licence = ReleaseLicenceFactory()
    with pytest.raises(ProtectedError):
        licence.release.delete()
    assert ReleaseLicence.objects.filter(pk=licence.pk).exists()


def test_recorded_at_defaults_to_now(db):
    before = timezone.now()
    licence = ReleaseLicenceFactory()
    assert before <= stored(licence)["recorded_at"] <= timezone.now()


# ── Services: graph.licensing ───────────────────────────────────────────────

# Every field a person states, each with a value other than its default.
STATED = {
    "licence_name": "Test licence 2.0",
    "spdx_id": "LicenseRef-test-2.0",
    "licence_url": "https://example.org/licence/2.0",
    "terms_quoted": "You may share and adapt the data, with attribution.",
    "read_at": "https://example.org/terms",
    "read_on": datetime.date(2026, 10, 3),
    "attribution": "Cite the test dataset.",
    "redistribution": Permission.PERMITTED,
    "commercial_use": Permission.NOT_PERMITTED,
    "modification": Permission.PERMITTED_WITH_CONDITIONS,
    "share_alike": ShareAlike.YES,
    "obligations": "Keep this notice.",
    "ambiguity_notes": "None found.",
    "reviewer": "Test reviewer",
}
INTERNAL = ApprovalScope.INTERNAL


def test_a_caller_states_every_snapshot_field_but_the_services_own():
    assert set(STATED) == set(LICENCE_FIELDS)
    own = {"id", "release", "recorded_at", "recorded_by", "recorded_by_username"}
    own.add("created_at")
    fields = {field.name for field in ReleaseLicence._meta.concrete_fields}
    assert set(LICENCE_FIELDS) == fields - own


def test_record_licence_records_what_is_stated_and_who_recorded_it(switch_on):
    actor = StaffUserFactory(username="licence-reader")
    release = DatasetReleaseFactory()
    before = timezone.now()
    licence = record_licence(release, recorded_by=actor, **STATED)
    row = stored(licence)
    for name, value in STATED.items():
        assert row[name] == value, name
    assert (row["release_id"], row["recorded_by_id"], row["recorded_by_username"]) == (
        release.pk,
        actor.pk,
        "licence-reader",
    )
    assert before <= row["recorded_at"] <= timezone.now()
    assert licence.records_terms
    assert latest_licences([release.pk]) == {release.pk: licence}
    # A release's id does as well as the row, and the newest is the latest.
    newer = record_licence(release.pk, recorded_by=actor)
    assert latest_licences([release.pk]) == {release.pk: newer}


def test_a_snapshot_recorded_with_nothing_stated_knows_nothing(switch_on, staff_user):
    licence = record_licence(DatasetReleaseFactory(), recorded_by=staff_user)
    row = stored(licence)
    for name in ("redistribution", "commercial_use", "modification", "share_alike"):
        assert row[name] == "unknown", name
    for name in set(LICENCE_FIELDS) - {
        "redistribution",
        "commercial_use",
        "modification",
        "share_alike",
        "read_on",
    }:
        assert row[name] == "", name
    assert row["read_on"] is None
    assert not licence.records_terms


def test_record_licence_refuses_while_the_switch_is_off(db, settings, staff_user):
    settings.TYPED_GRAPH_ENABLED = False
    with pytest.raises(LicensingError, match="TYPED_GRAPH_ENABLED"):
        record_licence(DatasetReleaseFactory(), recorded_by=staff_user, **STATED)
    assert not ReleaseLicence.objects.exists()


@pytest.mark.parametrize("actor", ["none", "unsaved", "not staff", "inactive staff"])
def test_record_licence_needs_an_active_staff_actor(switch_on, actor):
    with pytest.raises(LicensingError, match="recorded_by must be an active staff"):
        record_licence(DatasetReleaseFactory(), recorded_by=actor_of(actor), **STATED)
    assert not ReleaseLicence.objects.exists()


@pytest.mark.parametrize(
    "name", ["recorded_at", "recorded_by_username", "id", "created_at", "licence"]
)
def test_record_licence_takes_no_field_the_service_records_or_none_holds(
    switch_on, staff_user, name
):
    with pytest.raises(LicensingError, match=f"no field {name}"):
        record_licence(
            DatasetReleaseFactory(), recorded_by=staff_user, **{name: "2026-10-01"}
        )
    assert not ReleaseLicence.objects.exists()


@pytest.mark.parametrize(
    "fields",
    [
        {"redistribution": "maybe"},
        {"commercial_use": "PERMITTED"},
        {"modification": None},
        {"share_alike": "unknowable"},
        {"licence_url": "ftp://example.org/licence"},
        {"read_at": "javascript:alert(1)"},
        {"read_at": "example.org/terms"},
        {"licence_url": "https://example.org/" + "x" * 1000},
        {"read_on": "2026-10-01"},
        {"read_on": datetime.datetime(2026, 10, 1, tzinfo=datetime.timezone.utc)},
        {"licence_name": "x" * 256},
        {"spdx_id": "x" * 65},
        {"reviewer": "x" * 256},
        {"licence_name": None},
        {"terms_quoted": 5},
    ],
    ids=lambda fields: next(iter(fields)),
)
def test_record_licence_checks_each_value(switch_on, staff_user, fields):
    with pytest.raises(LicensingError, match=next(iter(fields))) as refusal:
        record_licence(DatasetReleaseFactory(), recorded_by=staff_user, **fields)
    assert "Nothing was written." in str(refusal.value)
    assert not ReleaseLicence.objects.exists()


def test_record_licence_never_changes_due_diligence(switch_on, staff_user):
    release = approvable_release()
    record = DueDiligenceRecord.objects.get(dataset=release.dataset)
    before, history = stored(record), record.history.count()
    record_licence(
        release,
        recorded_by=staff_user,
        licence_name="Corrected licence",
        redistribution=Permission.NOT_PERMITTED,
    )
    assert stored(record) == before
    assert record.history.count() == history


def test_record_licence_needs_a_release_that_exists(switch_on, staff_user):
    for release in (10**12, 2**80, "1", None, DatasetReleaseFactory.build()):
        with pytest.raises(LicensingError, match="Unknown release"):
            record_licence(release, recorded_by=staff_user)
    assert not ReleaseLicence.objects.exists()


@pytest.mark.postgres
def test_record_licence_locks_the_release_before_it_writes(switch_on, staff_user):
    release = DatasetReleaseFactory()
    with CaptureQueriesContext(connection) as queries:
        record_licence(release, recorded_by=staff_user)
    sql = [query["sql"] for query in queries.captured_queries]
    locking = [n for n, text in enumerate(sql) if "FOR UPDATE" in text]
    inserting = [n for n, text in enumerate(sql) if text.startswith("INSERT")]
    assert 'FROM "graph_datasetrelease"' in sql[locking[0]]
    assert locking[0] < inserting[0]


NEW_TERMS = {
    "licence_name": "New terms 2.0",
    "read_at": "https://example.org/terms-2",
    "read_on": datetime.date(2026, 10, 2),
}


def _held_while(first, second) -> bool:
    """Run first() in a transaction held open, and second() meanwhile in
    another; True when second() waited on a lock until first() committed."""
    holding, go = threading.Event(), threading.Event()
    errors = []

    def hold():
        try:
            with transaction.atomic():
                first()
                holding.set()
                go.wait(30)
        except Exception as exc:  # reported by the main thread
            errors.append(exc)
        finally:
            connection.close()

    def follow():
        try:
            holding.wait(30)
            second()
        except Exception as exc:
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=hold), threading.Thread(target=follow)]
    for thread in threads:
        thread.start()
    try:
        assert holding.wait(30), "the first transaction never started"
        waited = _a_backend_waits_on_a_lock()
    finally:
        go.set()
        for thread in threads:
            thread.join(30)
    assert errors == []
    return waited


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


@pytest.mark.postgres
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("confirm", ["confirm_due_diligence", "create complete"])
def test_a_confirmation_waits_for_a_snapshot_being_recorded(settings, confirm):
    """A confirmation stamped while a snapshot is still uncommitted could
    not see it, so must not count as covering it: it waits for the commit,
    and is then newer than the snapshot."""
    settings.TYPED_GRAPH_ENABLED = True
    staff = StaffUserFactory()
    if confirm == "confirm_due_diligence":
        release = approvable_release()
        record = DueDiligenceRecord.objects.get(dataset=release.dataset)

        def confirming():
            confirm_due_diligence(record, by=staff, note="Covers the new terms.")

    else:
        release = DatasetReleaseFactory()
        ReleaseLicenceFactory(release=release, open_terms=True)

        def confirming():
            DueDiligenceRecordFactory(dataset=release.dataset, status=COMPLETE)

    def recording():
        record_licence(release, recorded_by=staff, **NEW_TERMS)

    assert _held_while(recording, confirming), "the confirmation never waited"
    latest = ReleaseLicence.objects.filter(release=release).latest("pk")
    record = DueDiligenceRecord.objects.get(dataset=release.dataset)
    assert latest.licence_name == "New terms 2.0"
    assert record.completed_at > latest.recorded_at
    decision = record_decision(
        release,
        ApprovalScope.INTERNAL,
        Decision.APPROVED,
        rationale="Covered.",
        decided_by=staff,
    )
    assert decision.licence == latest


@pytest.mark.postgres
@pytest.mark.django_db(transaction=True)
def test_a_snapshot_waits_for_a_confirmation_being_made(settings):
    """The other order: a snapshot recorded while a confirmation is still
    uncommitted waits, is newer, and needs confirming again."""
    settings.TYPED_GRAPH_ENABLED = True
    staff = StaffUserFactory()
    release = approvable_release()
    record = DueDiligenceRecord.objects.get(dataset=release.dataset)

    def confirming():
        confirm_due_diligence(record, by=staff, note="Confirmed.")

    def recording():
        record_licence(release, recorded_by=staff, **NEW_TERMS)

    assert _held_while(confirming, recording), "the snapshot never waited"
    latest = ReleaseLicence.objects.filter(release=release).latest("pk")
    record.refresh_from_db()
    assert record.completed_at < latest.recorded_at
    with pytest.raises(ApprovalError, match="Confirm that it covers"):
        record_decision(
            release,
            ApprovalScope.INTERNAL,
            Decision.APPROVED,
            rationale="Not covered yet.",
            decided_by=staff,
        )
    assert not release.approvals.exists()


def test_confirming_completes_a_record_and_names_who_confirmed(switch_on):
    actor = StaffUserFactory(username="confirmer")
    record = DueDiligenceRecordFactory()
    before = timezone.now()
    confirmed = confirm_due_diligence(record, by=actor, note="  Covers the terms.  ")
    row = stored(record)
    assert row["status"] == COMPLETE
    assert before <= row["completed_at"] <= timezone.now()
    change = record.history.last()
    assert (change.status, change.completed_at, change.note) == (
        COMPLETE,
        row["completed_at"],
        "Covers the terms.",
    )
    assert (change.changed_by, change.changed_by_username) == (actor, "confirmer")
    assert record.history.count() == 2
    assert (confirmed.pk, confirmed.status, confirmed.completed_at) == (
        record.pk,
        COMPLETE,
        row["completed_at"],
    )


@pytest.mark.parametrize("status", [COMPLETE, BLOCKED])
def test_confirming_again_sets_completed_at_again(switch_on, staff_user, status):
    record = DueDiligenceRecordFactory(status=status)
    first = stored(record)["completed_at"]
    confirm_due_diligence(record.pk, by=staff_user)
    row = stored(record)
    assert row["status"] == COMPLETE
    assert row["completed_at"] is not None
    assert first is None or row["completed_at"] > first
    assert record.history.count() == 2


@pytest.mark.parametrize(
    "missing", [{"reviewer": ""}, {"reviewer": "   "}, {"notes_link": ""}]
)
def test_confirming_needs_a_reviewer_and_a_notes_link(switch_on, staff_user, missing):
    record = DueDiligenceRecordFactory(**missing)
    before = stored(record)
    with pytest.raises(LicensingError) as refusal:
        confirm_due_diligence(record, by=staff_user)
    assert str(refusal.value) == DUE_DILIGENCE_COMPLETE_RULE
    assert stored(record) == before
    assert record.history.count() == 1


def test_confirming_reads_the_record_as_stored(switch_on, staff_user):
    record = DueDiligenceRecordFactory(reviewer="")
    stale = DueDiligenceRecord.objects.get(pk=record.pk)
    stale.reviewer = "Typed, never saved"
    with pytest.raises(LicensingError, match="reviewer"):
        confirm_due_diligence(stale, by=staff_user)
    save_as(record, staff_user, reviewer="Saved reviewer")
    stale.reviewer = ""
    confirm_due_diligence(stale, by=staff_user)
    row = stored(record)
    assert (row["status"], row["reviewer"]) == (COMPLETE, "Saved reviewer")


def test_confirming_refuses_while_the_switch_is_off(db, settings, staff_user):
    record = DueDiligenceRecordFactory()
    settings.TYPED_GRAPH_ENABLED = False
    with pytest.raises(LicensingError, match="TYPED_GRAPH_ENABLED"):
        confirm_due_diligence(record, by=staff_user)
    assert stored(record)["status"] == OPEN
    assert record.history.count() == 1


@pytest.mark.parametrize("actor", ["none", "unsaved", "not staff", "inactive staff"])
def test_confirming_needs_an_active_staff_actor(switch_on, actor):
    record = DueDiligenceRecordFactory()
    with pytest.raises(LicensingError, match="by must be an active staff"):
        confirm_due_diligence(record, by=actor_of(actor))
    assert stored(record)["status"] == OPEN
    assert record.history.count() == 1


def test_confirming_needs_a_record_that_exists(switch_on, staff_user):
    for record in (10**12, "1", None, DueDiligenceRecord()):
        with pytest.raises(LicensingError, match="Unknown due-diligence record"):
            confirm_due_diligence(record, by=staff_user)
    with pytest.raises(LicensingError, match="A note is text"):
        confirm_due_diligence(DueDiligenceRecordFactory(), by=staff_user, note=3)


def test_latest_licences_gives_each_releases_highest_id_in_one_query(
    switch_on, django_assert_num_queries
):
    first = ReleaseLicenceFactory(recorded=True)
    # Recorded with an earlier time, it is still the latest: ids decide.
    correction = ReleaseLicenceFactory(
        release=first.release,
        recorded_at=first.recorded_at - datetime.timedelta(days=1),
    )
    other = ReleaseLicenceFactory()
    bare = DatasetReleaseFactory()
    with django_assert_num_queries(1):
        latest = latest_licences([first.release_id, other.release_id, bare.pk, 2**80])
    assert latest == {first.release_id: correction, other.release_id: other}


def test_latest_licences_of_nothing_or_while_off_is_empty(db, settings):
    licence = ReleaseLicenceFactory()
    settings.TYPED_GRAPH_ENABLED = True
    assert latest_licences([]) == {}
    settings.TYPED_GRAPH_ENABLED = False
    assert latest_licences([licence.release_id]) == {}


def test_latest_licences_takes_integer_ids(switch_on):
    for ids in (["1"], [1.0], [True], "12", None, 5):
        with pytest.raises(TypeError):
            latest_licences(ids)


def test_stale_approvals_lists_active_approvals_whose_snapshot_was_superseded(
    switch_on, staff_user
):
    release = approvable_release()
    internal = record_decision(
        release, INTERNAL, Decision.APPROVED, rationale="Why.", decided_by=staff_user
    )
    record_decision(
        release,
        ApprovalScope.EXTERNAL_SERVICES,
        Decision.DENIED,
        rationale="Not yet.",
        decided_by=staff_user,
    )
    assert list(stale_approvals()) == []
    newer = record_licence(release, recorded_by=staff_user, **STATED)
    # The denial, which relied on the older snapshot too, is no approval.
    assert list(stale_approvals()) == [internal]

    other = approvable_release()
    revoked = record_decision(
        other, INTERNAL, Decision.APPROVED, rationale="Why.", decided_by=staff_user
    )
    ReleaseLicenceFactory(release=other, recorded=True)
    assert list(stale_approvals()) == [internal, revoked]
    revoke(revoked, reason="Ended.", revoked_by=staff_user)
    assert list(stale_approvals()) == [internal]

    # Refreshed against the latest snapshot, it is no longer stale.
    confirm_due_diligence(
        DueDiligenceRecord.objects.get(dataset=release.dataset), by=staff_user
    )
    fresh = replace_decision(internal, rationale="Reviewed.", decided_by=staff_user)
    assert fresh.licence == newer
    assert list(stale_approvals()) == []


def test_stale_approvals_is_empty_while_the_switch_is_off(db, settings, staff_user):
    settings.TYPED_GRAPH_ENABLED = True
    release = approvable_release()
    record_decision(
        release, INTERNAL, Decision.APPROVED, rationale="Why.", decided_by=staff_user
    )
    confirm_diligence(release.dataset)
    ReleaseLicenceFactory(release=release, recorded=True)
    assert stale_approvals().count() == 1
    settings.TYPED_GRAPH_ENABLED = False
    assert not stale_approvals().exists()

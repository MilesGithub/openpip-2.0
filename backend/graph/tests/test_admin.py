"""The typed graph in the Django admin (graph/admin.py): absent while the
switch is off, and, while it is on, the catalogue, licensing and registry
records under Django's permissions, narrowed by each record's rules."""

import datetime
from pathlib import Path

import pytest
from django.apps import apps
from django.contrib import admin
from django.contrib.admin.models import LogEntry
from django.contrib.admin.utils import flatten_fieldsets, quote
from django.contrib.auth.models import Permission as AuthPermission
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from graph import licensing
from graph.admin import NOTE_WITHOUT_CHANGE, STALE
from graph.models import (
    DUE_DILIGENCE_COMPLETE_RULE,
    ApprovalScope,
    DataSource,
    DatasetRelease,
    DatasetStatus,
    DueDiligenceChange,
    DueDiligenceRecord,
    DueDiligenceStatus,
    Entity,
    EntityIdentifier,
    EntityType,
    IdentifierNamespace,
    ImportRun,
    Permission,
    Publication,
    PublicationRole,
    RegistryState,
    ReleaseLicence,
    ReleaseStatus,
    ReleaseTaxon,
    ShareAlike,
    SourceDataset,
    SourceDatasetPublication,
    SourceFile,
    SourceRecord,
    Taxon,
    UsageApproval,
)
from graph.tests.factories import (
    DataSourceFactory,
    DatasetReleaseFactory,
    DueDiligenceRecordFactory,
    EntityTypeFactory,
    IdentifierNamespaceFactory,
    PublicationFactory,
    ReleaseTaxonFactory,
    SourceDatasetFactory,
    StaffUserFactory,
    TaxonFactory,
    approvable_release,
    approve,
)

COMPLETE = DueDiligenceStatus.COMPLETE
OPEN = DueDiligenceStatus.OPEN
BLOCKED = DueDiligenceStatus.BLOCKED
REGISTERED = (
    Taxon,
    EntityType,
    IdentifierNamespace,
    RegistryState,
    DataSource,
    SourceDataset,
    Publication,
    DatasetRelease,
    DueDiligenceRecord,
    DueDiligenceChange,
    ReleaseLicence,
    UsageApproval,
)
# Third-party data, which only the visibility service may show (AD1).
UNREGISTERED = (Entity, EntityIdentifier, SourceFile, ImportRun, SourceRecord)
VIEW_ONLY = (
    Taxon,
    EntityType,
    IdentifierNamespace,
    RegistryState,
    DatasetRelease,
    UsageApproval,
    DueDiligenceChange,
)
GRAPH_DIR = Path(apps.get_app_config("graph").path)


def name_of(model) -> str:
    return model.__name__


def url(model, view, *args) -> str:
    return reverse(
        f"admin:graph_{model._meta.model_name}_{view}",
        args=[quote(arg) for arg in args],
    )


def stored(obj) -> dict:
    return type(obj)._base_manager.filter(pk=obj.pk).values().get()


def one_of_each() -> dict:
    """A row of every registered model: a published release with its
    dataset, data source, licence snapshot, due diligence and an approval."""
    release = approvable_release(status=ReleaseStatus.PUBLISHED)
    [approval] = approve(release, ApprovalScope.INTERNAL)
    record = DueDiligenceRecord.objects.get(dataset=release.dataset)
    return {
        Taxon: TaxonFactory(),
        EntityType: EntityTypeFactory(),
        IdentifierNamespace: IdentifierNamespaceFactory(),
        RegistryState: RegistryState.objects.create(
            version="test", synced_at=timezone.now()
        ),
        DataSource: release.dataset.data_source,
        SourceDataset: release.dataset,
        Publication: PublicationFactory(),
        DatasetRelease: release,
        DueDiligenceRecord: record,
        DueDiligenceChange: record.history.first(),
        ReleaseLicence: approval.licence,
        UsageApproval: approval,
    }


def posted(value):
    if value is True:
        return "on"
    if value is None:
        return ""
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    return value


def submitted(page, **changes) -> dict:
    """What a browser posts for the admin form on `page`, inlines included,
    with `changes` typed over it: True ticks a checkbox, False clears it."""
    forms = [page.context["adminform"].form]
    data = {}
    for inline in page.context["inline_admin_formsets"]:
        management = inline.formset.management_form
        for name in management.fields:
            data[management.add_prefix(name)] = management[name].value()
        forms.extend(inline.formset.forms)
    for form in forms:
        for name in form.fields:
            data[form.add_prefix(name)] = form[name].value()
    data.update(changes)
    return {key: posted(value) for key, value in data.items() if value is not False}


def errors(response) -> dict:
    """The admin form's errors, for an assertion's message."""
    context = response.context
    if context is None or "adminform" not in context:
        return {"status": response.status_code}
    found = dict(context["adminform"].form.errors)
    for inline in context["inline_admin_formsets"]:
        formset = inline.formset
        found[formset.prefix] = [*formset.errors, *formset.non_form_errors()]
    return found


def request_by(user):
    request = RequestFactory().get("/")
    request.user = user
    return request


@pytest.fixture
def superuser(db):
    return StaffUserFactory(username="graph-superuser", is_superuser=True)


@pytest.fixture
def browser(client, superuser):
    """The admin, signed in as a superuser."""
    client.force_login(superuser)
    return client


# ── The switch ──────────────────────────────────────────────────────────────


def test_the_graph_app_is_absent_from_the_admin_while_the_switch_is_off(
    browser, settings
):
    settings.TYPED_GRAPH_ENABLED = False
    one_of_each()
    index = browser.get(reverse("admin:index"))
    assert index.status_code == 200
    labels = {app["app_label"] for app in index.context["app_list"]}
    assert "core" in labels and "graph" not in labels
    assert b"Typed relationship graph" not in index.content
    app_index = reverse("admin:app_list", kwargs={"app_label": "graph"})
    assert browser.get(app_index).status_code == 404


@pytest.mark.parametrize("model", REGISTERED, ids=name_of)
def test_every_page_is_refused_while_the_switch_is_off(browser, settings, model):
    settings.TYPED_GRAPH_ENABLED = False
    row = one_of_each()[model]
    before = stored(row)
    pages = [
        url(model, "changelist"),
        url(model, "add"),
        url(model, "change", row.pk),
        url(model, "delete", row.pk),
        url(model, "history", row.pk),
        # An unknown id is answered as a known one is.
        url(model, "history", 999_999),
        url(model, "change", 999_999),
    ]
    for page in pages:
        assert browser.get(page).status_code == 403, page
    assert browser.post(url(model, "change", row.pk), {}).status_code == 403
    deleted = browser.post(url(model, "delete", row.pk), {"post": "yes"})
    assert deleted.status_code == 403
    assert stored(row) == before


def test_the_graph_app_is_listed_while_the_switch_is_on(browser, switch_on):
    rows = one_of_each()
    index = browser.get(reverse("admin:index"))
    [graph] = [app for app in index.context["app_list"] if app["app_label"] == "graph"]
    assert {entry["object_name"] for entry in graph["models"]} == {
        model.__name__ for model in REGISTERED
    }
    app_index = reverse("admin:app_list", kwargs={"app_label": "graph"})
    assert browser.get(app_index).status_code == 200
    for model, row in rows.items():
        assert browser.get(url(model, "changelist")).status_code == 200, model
        assert browser.get(url(model, "change", row.pk)).status_code == 200, model
        assert browser.get(url(model, "history", row.pk)).status_code == 200, model


def test_only_catalogue_licensing_and_registry_models_are_registered():
    registered = {
        model for model in admin.site._registry if model._meta.app_label == "graph"
    }
    assert registered == set(REGISTERED)
    for model in (*UNREGISTERED, ReleaseTaxon):
        assert not admin.site.is_registered(model), model


def test_the_graph_admins_pass_django_s_admin_checks():
    # Inlines are checked with the admin that holds them.
    found = [
        error for model in REGISTERED for error in admin.site._registry[model].check()
    ]
    assert found == []


def test_staff_need_their_django_permissions_while_the_switch_is_on(client, switch_on):
    staff = StaffUserFactory()
    client.force_login(staff)
    source = DataSourceFactory()
    index = client.get(reverse("admin:index"))
    assert "graph" not in {app["app_label"] for app in index.context["app_list"]}
    assert client.get(url(DataSource, "changelist")).status_code == 403

    staff.user_permissions.add(
        AuthPermission.objects.get(
            content_type__app_label="graph", codename="view_datasource"
        )
    )
    assert client.get(url(DataSource, "changelist")).status_code == 200
    page = client.get(url(DataSource, "change", source.pk))
    assert page.status_code == 200
    assert page.context["has_change_permission"] is False
    assert client.get(url(DataSource, "add")).status_code == 403
    assert client.get(url(DataSource, "delete", source.pk)).status_code == 403


# ── View-only records ───────────────────────────────────────────────────────


@pytest.mark.parametrize("model", VIEW_ONLY, ids=name_of)
def test_view_only_records_are_never_added_changed_or_deleted_by_a_superuser(
    browser, switch_on, superuser, model
):
    row = one_of_each()[model]
    before = stored(row)
    model_admin = admin.site._registry[model]
    request = request_by(superuser)
    assert model_admin.has_view_permission(request, row)
    assert not model_admin.has_add_permission(request)
    assert not model_admin.has_change_permission(request, row)
    assert not model_admin.has_delete_permission(request, row)

    page = browser.get(url(model, "change", row.pk))
    assert page.status_code == 200
    assert page.context["has_change_permission"] is False
    assert not page.context["adminform"].form.fields
    listing = browser.get(url(model, "changelist"))
    assert listing.context["has_add_permission"] is False
    assert listing.context["action_form"] is None

    assert browser.get(url(model, "add")).status_code == 403
    assert browser.post(url(model, "add"), {}).status_code == 403
    assert browser.post(url(model, "change", row.pk), {}).status_code == 403
    assert browser.get(url(model, "delete", row.pk)).status_code == 403
    deleted = browser.post(url(model, "delete", row.pk), {"post": "yes"})
    assert deleted.status_code == 403
    browser.post(
        url(model, "changelist"),
        {"action": "delete_selected", "_selected_action": [row.pk], "post": "yes"},
    )
    assert stored(row) == before


def test_a_release_is_shown_without_its_stats_and_with_its_declared_taxa(
    browser, switch_on
):
    release = DatasetReleaseFactory(stats={"distinctive_stat_name": 7})
    taxon = TaxonFactory(scientific_name="Testus declaratus")
    ReleaseTaxonFactory(release=release, taxon=taxon)
    page = browser.get(url(DatasetRelease, "change", release.pk))
    shown = flatten_fieldsets(page.context["adminform"].fieldsets)
    assert "stats" not in shown and "declared_taxa" in shown
    assert b"distinctive_stat_name" not in page.content
    assert f"Testus declaratus ({taxon.taxid})".encode() in page.content


@pytest.mark.parametrize("model", (ReleaseLicence, UsageApproval), ids=name_of)
def test_licences_and_approvals_cannot_be_changed_or_deleted(browser, switch_on, model):
    row = one_of_each()[model]
    before = stored(row)
    page = browser.get(url(model, "change", row.pk))
    assert page.status_code == 200
    assert not page.context["adminform"].form.fields
    assert browser.post(url(model, "change", row.pk), {}).status_code == 403
    assert browser.get(url(model, "delete", row.pk)).status_code == 403
    assert (
        browser.post(url(model, "delete", row.pk), {"post": "yes"}).status_code == 403
    )
    assert browser.get(url(model, "changelist")).context["action_form"] is None
    assert stored(row) == before


# ── Catalogue ───────────────────────────────────────────────────────────────


def test_publication_identifiers_are_read_only_and_none_is_added(browser, switch_on):
    publication = PublicationFactory(title="A title")
    assert browser.get(url(Publication, "add")).status_code == 403
    assert browser.post(url(Publication, "add"), {"pmid": "1"}).status_code == 403
    page = browser.get(url(Publication, "change", publication.pk))
    assert set(page.context["adminform"].form.fields) == {
        "title",
        "authors",
        "journal",
        "year",
        "url",
    }
    response = browser.post(
        url(Publication, "change", publication.pk),
        submitted(
            page,
            title="The corrected title",
            year=2016,
            pmid="1",
            doi="10.1000/typed",
            is_synthetic=True,
        ),
    )
    assert response.status_code == 302, errors(response)
    row = stored(publication)
    assert (row["title"], row["year"]) == ("The corrected title", 2016)
    assert (row["pmid"], row["doi"], row["doi_lookup"], row["is_synthetic"]) == (
        publication.pmid,
        "",
        "",
        False,
    )


def test_a_publication_link_is_added_on_the_publication_page(browser, switch_on):
    publication = PublicationFactory()
    dataset = SourceDatasetFactory()
    page = browser.get(url(Publication, "change", publication.pk))
    response = browser.post(
        url(Publication, "change", publication.pk),
        submitted(
            page,
            **{
                "dataset_links-TOTAL_FORMS": 1,
                "dataset_links-0-dataset": dataset.pk,
                "dataset_links-0-role": PublicationRole.ERRATUM,
            },
        ),
    )
    assert response.status_code == 302, errors(response)
    [link] = SourceDatasetPublication.objects.all()
    assert (link.dataset, link.publication, link.role) == (
        dataset,
        publication,
        PublicationRole.ERRATUM,
    )


def test_a_publication_link_offers_releases_by_dataset_and_version(browser, switch_on):
    release = DatasetReleaseFactory(version="v7")
    page = browser.get(url(Publication, "change", PublicationFactory().pk))
    [inline] = [
        inline
        for inline in page.context["inline_admin_formsets"]
        if inline.formset.prefix == "release_links"
    ]
    field = inline.formset.empty_form.fields["release"]
    assert field.label_from_instance(release) == f"{release.dataset.slug}/v7 (draft)"


def test_a_dataset_status_is_read_only(browser, switch_on):
    dataset = SourceDatasetFactory(title="A title")
    assert "status" not in (
        browser.get(url(SourceDataset, "add")).context["adminform"].form.fields
    )
    page = browser.get(url(SourceDataset, "change", dataset.pk))
    assert "status" not in page.context["adminform"].form.fields
    response = browser.post(
        url(SourceDataset, "change", dataset.pk),
        submitted(page, title="A new title", status=DatasetStatus.WITHDRAWN),
    )
    assert response.status_code == 302, errors(response)
    row = stored(dataset)
    assert (row["title"], row["status"]) == ("A new title", DatasetStatus.CANDIDATE)


def test_a_data_source_is_added_and_changed(browser, switch_on):
    page = browser.get(url(DataSource, "add"))
    response = browser.post(
        url(DataSource, "add"),
        submitted(page, slug="a-lab", name="A lab", kind="lab", url="a-lab.example"),
    )
    assert response.status_code == 302, errors(response)
    source = DataSource.objects.get(slug="a-lab")
    # A link typed without a scheme is taken as https.
    assert source.url == "https://a-lab.example"
    page = browser.get(url(DataSource, "change", source.pk))
    response = browser.post(
        url(DataSource, "change", source.pk),
        submitted(page, url="ftp://a-lab.example/data"),
    )
    assert response.status_code == 200
    assert "url" in errors(response)
    assert stored(source)["url"] == "https://a-lab.example"


# ── Due diligence ───────────────────────────────────────────────────────────


def change_page(record) -> str:
    return url(DueDiligenceRecord, "change", record.pk)


def test_adding_due_diligence_writes_history_naming_the_user(
    browser, switch_on, superuser
):
    dataset = SourceDatasetFactory()
    page = browser.get(url(DueDiligenceRecord, "add"))
    assert "completed_at" not in page.context["adminform"].form.fields
    response = browser.post(
        url(DueDiligenceRecord, "add"),
        submitted(
            page,
            dataset=dataset.pk,
            status=OPEN,
            reviewer="A reviewer",
            notes_link="docs/project/generalization/DATASET_DUE_DILIGENCE.md#trrust",
            change_note="Opened for review.",
        ),
    )
    assert response.status_code == 302, errors(response)
    record = DueDiligenceRecord.objects.get(dataset=dataset)
    [change] = record.history.all()
    assert (change.changed_by, change.changed_by_username, change.note) == (
        superuser,
        "graph-superuser",
        "Opened for review.",
    )
    assert (change.status, change.reviewer) == (OPEN, "A reviewer")


def test_changing_due_diligence_writes_history_naming_the_user(
    browser, switch_on, superuser
):
    record = DueDiligenceRecordFactory()
    page = browser.get(change_page(record))
    form = page.context["adminform"].form
    assert "dataset" not in form.fields and "completed_at" not in form.fields
    response = browser.post(
        change_page(record),
        submitted(page, status=COMPLETE, change_note="The notes were reviewed."),
    )
    assert response.status_code == 302, errors(response)
    row = stored(record)
    assert row["status"] == COMPLETE and row["completed_at"] is not None
    assert record.history.count() == 2
    latest = record.history.last()
    assert (latest.changed_by, latest.note) == (superuser, "The notes were reviewed.")
    assert (latest.status, latest.completed_at) == (COMPLETE, row["completed_at"])

    page = browser.get(change_page(record))
    response = browser.post(change_page(record), submitted(page, status=BLOCKED))
    assert response.status_code == 302, errors(response)
    row = stored(record)
    assert (row["status"], row["completed_at"]) == (BLOCKED, None)
    assert record.history.count() == 3


def test_an_edit_of_the_notes_alone_writes_no_history(browser, switch_on):
    record = DueDiligenceRecordFactory()
    page = browser.get(change_page(record))
    response = browser.post(
        change_page(record), submitted(page, notes="Working notes.")
    )
    assert response.status_code == 302, errors(response)
    assert stored(record)["notes"] == "Working notes."
    assert record.history.count() == 1


def test_a_note_without_an_audited_change_is_refused(browser, switch_on):
    record = DueDiligenceRecordFactory()
    before = stored(record)
    page = browser.get(change_page(record))
    response = browser.post(
        change_page(record),
        submitted(page, notes="Working notes.", change_note="Why I edited."),
    )
    assert response.status_code == 200
    assert errors(response)["change_note"] == [NOTE_WITHOUT_CHANGE]
    assert stored(record) == before


def test_completing_without_a_reviewer_shows_the_rule(browser, switch_on):
    record = DueDiligenceRecordFactory(reviewer="")
    before = stored(record)
    page = browser.get(change_page(record))
    response = browser.post(change_page(record), submitted(page, status=COMPLETE))
    assert response.status_code == 200
    assert DUE_DILIGENCE_COMPLETE_RULE in errors(response)["__all__"]
    assert stored(record) == before


def blocked_elsewhere(record):
    record.status = BLOCKED
    record._changed_by = StaffUserFactory()
    record.save()


def flagged_elsewhere(record):
    record.terms_time_limited = True
    record._changed_by = StaffUserFactory()
    record.save()


def confirmed_elsewhere(record):
    licensing.confirm_due_diligence(record, by=StaffUserFactory())


def noted_elsewhere(record):
    record.notes = "Written by someone else."
    record.save()


@pytest.mark.parametrize(
    "change",
    [blocked_elsewhere, flagged_elsewhere, confirmed_elsewhere, noted_elsewhere],
)
def test_a_form_opened_before_the_record_changed_is_refused(browser, switch_on, change):
    record = DueDiligenceRecordFactory(status=COMPLETE)
    page = browser.get(change_page(record))
    change(DueDiligenceRecord.objects.get(pk=record.pk))
    before, history = stored(record), record.history.count()
    stale = submitted(page, reviewer="Another reviewer", change_note="Edit.")
    response = browser.post(change_page(record), stale)
    assert response.status_code == 200
    assert errors(response)["__all__"] == [STALE]
    assert stored(record) == before
    assert record.history.count() == history
    # The same form again is refused again; one opened now is not.
    assert browser.post(change_page(record), stale).status_code == 200
    page = browser.get(change_page(record))
    response = browser.post(
        change_page(record), submitted(page, reviewer="Another reviewer")
    )
    assert response.status_code == 302, errors(response)
    assert stored(record)["reviewer"] == "Another reviewer"


def test_due_diligence_is_never_deleted_in_the_admin(browser, switch_on):
    record = DueDiligenceRecordFactory()
    assert browser.get(url(DueDiligenceRecord, "delete", record.pk)).status_code == 403
    deleted = browser.post(
        url(DueDiligenceRecord, "delete", record.pk), {"post": "yes"}
    )
    assert deleted.status_code == 403
    listing = browser.get(url(DueDiligenceRecord, "changelist"))
    choices = dict(listing.context["action_form"].fields["action"].choices)
    assert "delete_selected" not in choices and "confirm_current_terms" in choices
    assert DueDiligenceRecord.objects.filter(pk=record.pk).exists()


def test_the_history_is_shown_read_only_with_the_record(browser, switch_on):
    record = DueDiligenceRecordFactory(change_note="First look.")
    page = browser.get(change_page(record))
    [inline] = page.context["inline_admin_formsets"]
    assert inline.formset.model is DueDiligenceChange
    assert not (
        inline.has_add_permission
        or inline.has_change_permission
        or inline.has_delete_permission
    )
    assert b"First look." in page.content


def confirm(browser, *records):
    return browser.post(
        url(DueDiligenceRecord, "changelist"),
        {
            "action": "confirm_current_terms",
            "_selected_action": [record.pk for record in records],
        },
        follow=True,
    )


def test_the_confirm_action_sets_completed_at_again(browser, switch_on, superuser):
    record = DueDiligenceRecordFactory(status=COMPLETE)
    first = stored(record)["completed_at"]
    response = confirm(browser, record)
    assert response.status_code == 200
    row = stored(record)
    assert (row["status"], row["completed_at"] > first) == (COMPLETE, True)
    latest = record.history.last()
    assert (latest.changed_by, latest.completed_at) == (superuser, row["completed_at"])
    assert latest.note.startswith("Confirmed that due diligence covers")
    shown = [str(message) for message in response.context["messages"]]
    assert shown == [f"Due diligence confirmed for {record.dataset}."]


def test_the_confirm_action_reports_what_it_refused(browser, switch_on):
    ready = DueDiligenceRecordFactory(status=OPEN)
    unreviewed = DueDiligenceRecordFactory(status=OPEN, reviewer="")
    response = confirm(browser, ready, unreviewed)
    assert stored(ready)["status"] == COMPLETE
    row = stored(unreviewed)
    assert (row["status"], row["completed_at"]) == (OPEN, None)
    assert unreviewed.history.count() == 1
    shown = [str(message) for message in response.context["messages"]]
    assert f"{unreviewed.dataset}: {DUE_DILIGENCE_COMPLETE_RULE}" in shown
    assert f"Due diligence confirmed for {ready.dataset}." in shown


# ── Licence snapshots ───────────────────────────────────────────────────────

TERMS = {
    "licence_name": "CC BY 4.0",
    "spdx_id": "CC-BY-4.0",
    "licence_url": "https://creativecommons.org/licenses/by/4.0/",
    "terms_quoted": "Licensed under CC BY 4.0.",
    "read_at": "https://example.org/terms",
    "read_on": "2026-10-01",
    "attribution": "Cite the paper.",
    "redistribution": Permission.PERMITTED,
    "commercial_use": Permission.PERMITTED,
    "modification": Permission.PERMITTED_WITH_CONDITIONS,
    "share_alike": ShareAlike.NO,
    "obligations": "Note the changes.",
    "ambiguity_notes": "",
    "reviewer": "A reviewer",
}


@pytest.fixture
def recorded_by(monkeypatch):
    """Who each record_licence call named, in order."""
    calls = []
    record_licence = licensing.record_licence

    def spy(release, **fields):
        calls.append(fields["recorded_by"])
        return record_licence(release, **fields)

    monkeypatch.setattr(licensing, "record_licence", spy)
    return calls


def test_a_licence_snapshot_is_added_through_record_licence(
    browser, switch_on, superuser, recorded_by
):
    release = DatasetReleaseFactory()
    page = browser.get(url(ReleaseLicence, "add"))
    form = page.context["adminform"].form
    assert set(form.fields) == {"release", *licensing.LICENCE_FIELDS}
    response = browser.post(
        url(ReleaseLicence, "add"), submitted(page, release=release.pk, **TERMS)
    )
    assert response.status_code == 302, errors(response)
    assert recorded_by == [superuser]
    [licence] = ReleaseLicence.objects.all()
    assert (licence.release, licence.recorded_by) == (release, superuser)
    assert licence.recorded_by_username == "graph-superuser"
    for name, value in TERMS.items():
        assert str(getattr(licence, name)) == value, name
    # The admin's log names the row record_licence wrote.
    entry = LogEntry.objects.get()
    assert (entry.object_id, entry.is_addition()) == (str(licence.pk), True)


def test_a_licence_snapshot_that_breaks_a_rule_is_not_added(
    browser, switch_on, recorded_by
):
    release = DatasetReleaseFactory()
    page = browser.get(url(ReleaseLicence, "add"))
    for field, value in (
        ("read_at", "ftp://example.org/terms"),
        ("redistribution", "probably"),
        ("read_on", "1 October"),
    ):
        response = browser.post(
            url(ReleaseLicence, "add"),
            submitted(page, release=release.pk, **{**TERMS, field: value}),
        )
        assert response.status_code == 200
        assert field in errors(response), field
    assert recorded_by == []
    assert not ReleaseLicence.objects.exists()


def test_a_licence_snapshot_offers_releases_by_dataset_and_version(browser, switch_on):
    release = DatasetReleaseFactory(version="v3", status=ReleaseStatus.STAGED)
    page = browser.get(url(ReleaseLicence, "add"))
    field = page.context["adminform"].form.fields["release"]
    assert field.label_from_instance(release) == f"{release.dataset.slug}/v3 (staged)"


# ── Plain text ──────────────────────────────────────────────────────────────


def test_nothing_in_the_graph_app_marks_text_as_html():
    for path in sorted(GRAPH_DIR.rglob("*.py")):
        if "tests" in path.relative_to(GRAPH_DIR).parts:
            continue
        source = path.read_text(encoding="utf-8")
        for name in ("format_html", "mark_safe", "SafeString", "html_safe"):
            assert name not in source, f"{path.name} uses {name}"

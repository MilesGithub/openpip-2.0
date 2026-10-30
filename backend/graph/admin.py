"""The typed graph's catalogue, licensing and registry records in the Django
admin, for staff, while the switch is on.

Source files, import runs, source records and entities are not registered:
they hold third-party data, which only the visibility service may show
(graph.visibility), so they wait for AD1's admin, which reads through it.
So do relationships, their evidence assertions and the assertions'
participants, methods, publications, scores and contexts.

The registries are shown: taxa, entity types, identifier namespaces and the
registry version, relationship types and score types, all loaded from their
JSON, and the vocabulary terms. A term holds a CURIE and the label a commit
stated for it, recorded only by the commit of a synthetic release or of one
with an effective internal approval (graph.terms), which is what staff may
see.

Registering does not depend on the switch, so the admin's URLs never do;
each page asks it. While TYPED_GRAPH_ENABLED is off the app is absent from
the admin and every one of its pages is refused, to superusers too. While it
is on, Django's staff permissions apply, narrowed where a record's rules
allow less:

- the registries come only from their JSON (graph_sync_registry), terms
  only from a commit (graph.terms), releases change only through
  graph.releases and G5's commands, and decisions only through
  graph.approvals, so they are shown and never edited here, as is the
  due-diligence history;
- a publication's identifiers are graph.publications' to set, normalized and
  merged across co-referent identifiers, so only its citation text is edited
  here, and none is added;
- a dataset's status describes it and gates nothing; its releases'
  registration sets it;
- a licence snapshot is added through graph.licensing.record_licence, which
  records who added it, and is never changed;
- a due-diligence record is never deleted, each change names the user who
  made it, and a form opened before the record last changed is refused, so a
  stale page never undoes a reopen, a block or a confirmation.

Nothing here is rendered as HTML.
"""

import hashlib
import json

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction

from . import licensing
from .models import (
    DataSource,
    DatasetRelease,
    DueDiligenceChange,
    DueDiligenceRecord,
    EntityType,
    IdentifierNamespace,
    Publication,
    RegistryState,
    RelationshipType,
    ReleaseLicence,
    ReleasePublication,
    ScoreType,
    SourceDataset,
    SourceDatasetPublication,
    Taxon,
    Term,
    UsageApproval,
)
from .preconditions import typed_graph_enabled

# ── The switch, and what may change here ────────────────────────────────────


class SwitchedPermissions:
    """Nothing while the switch is off, whoever asks; Django's permissions
    decide while it is on. Every model admin and inline here has it."""

    def has_module_permission(self, request):
        return typed_graph_enabled() and super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        return typed_graph_enabled() and super().has_view_permission(request, obj)

    def has_add_permission(self, request, *obj):
        # A model admin asks has_add_permission(request), an inline
        # has_add_permission(request, obj).
        return typed_graph_enabled() and super().has_add_permission(request, *obj)

    def has_change_permission(self, request, obj=None):
        return typed_graph_enabled() and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return typed_graph_enabled() and super().has_delete_permission(request, obj)


class GraphAdmin(SwitchedPermissions, admin.ModelAdmin):
    # Links are http(s) only (graph.models.HTTP_URL); one typed without a
    # scheme is taken as https.
    formfield_overrides = {models.URLField: {"assume_scheme": "https"}}

    def history_view(self, request, object_id, extra_context=None):
        # Django looks the row up before it asks for permission, which would
        # answer an unknown id differently from a known one.
        if not typed_graph_enabled():
            raise PermissionDenied
        return super().history_view(request, object_id, extra_context)


class ViewOnlyAdmin(GraphAdmin):
    """Shown, and never added, changed or deleted here, by anyone."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ViewOnlyInline(SwitchedPermissions, admin.TabularInline):
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


def changed_fields(form) -> list:
    """The stored fields the form changed."""
    stored = {field.name for field in form.instance._meta.concrete_fields}
    return [name for name in form.changed_data if name in stored]


class SavesChangedFields:
    """A change writes only the fields the form changed. A field the form
    does not show (a dataset's status, a publication's identifiers) is then
    never written back from a copy read before someone else changed it."""

    def save_model(self, request, obj, form, change):
        if change:
            obj.save(update_fields=[*changed_fields(form), "updated_at"])
        else:
            obj.save()


def release_label(release) -> str:
    return f"{release.dataset.slug}/{release.version}"


class ReleaseChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, release):
        return f"{release_label(release)} ({release.status})"


class ReleaseChoices:
    """Releases are offered by dataset and version, with their status, rather
    than by row id."""

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.related_model is DatasetRelease:
            kwargs.setdefault("form_class", ReleaseChoiceField)
            kwargs.setdefault(
                "queryset",
                DatasetRelease.objects.select_related("dataset").order_by(
                    "dataset__slug", "version"
                ),
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class ReleaseColumn:
    @admin.display(description="release", ordering="release__dataset__slug")
    def release_name(self, row):
        return release_label(row.release)


# ── Registries: loaded from their JSON ──────────────────────────────────────


@admin.register(Taxon)
class TaxonAdmin(ViewOnlyAdmin):
    list_display = ("taxid", "scientific_name", "common_name", "rank")
    search_fields = ("scientific_name", "common_name")


@admin.register(EntityType)
class EntityTypeAdmin(ViewOnlyAdmin):
    list_display = ("slug", "label", "taxon_policy", "identity_scheme")


@admin.register(IdentifierNamespace)
class IdentifierNamespaceAdmin(ViewOnlyAdmin):
    list_display = ("slug", "label", "scope", "fixed_taxon", "defines_identity")
    list_filter = ("scope", "defines_identity")
    list_select_related = ("fixed_taxon",)


@admin.register(RegistryState)
class RegistryStateAdmin(ViewOnlyAdmin):
    list_display = ("version", "synced_at")


@admin.register(RelationshipType)
class RelationshipTypeAdmin(ViewOnlyAdmin):
    list_display = (
        "slug",
        "label",
        "family",
        "orientation",
        "polarity",
        "self_pairs_allowed",
        "is_reference",
    )
    list_filter = ("family", "orientation", "polarity", "is_reference")


@admin.register(ScoreType)
class ScoreTypeAdmin(ViewOnlyAdmin):
    list_display = (
        "slug",
        "label",
        "value_kind",
        "direction",
        "range_min",
        "range_max",
        "comparable_across_datasets",
    )
    list_filter = ("value_kind", "direction", "comparable_across_datasets")
    search_fields = ("slug", "label")


# ── Vocabulary terms: recorded by commits ───────────────────────────────────


@admin.register(Term)
class TermAdmin(ViewOnlyAdmin):
    # The release by dataset and version, as elsewhere here, rather than by
    # row id.
    fields = (
        "curie",
        "ontology",
        "label",
        "label_from",
        "definition",
        "obsolete",
        "ontology_release",
        "created_at",
    )
    readonly_fields = fields
    list_display = ("curie", "label", "ontology", "label_from", "obsolete")
    list_filter = ("ontology", "obsolete")
    list_select_related = ("label_release__dataset",)
    search_fields = ("curie", "label")

    @admin.display(
        description="label stated by release", ordering="label_release__dataset__slug"
    )
    def label_from(self, term):
        # None, shown as the admin's empty value, once the draft release that
        # stated the label is deleted.
        release = term.label_release
        return release_label(release) if release is not None else None


# ── Catalogue ───────────────────────────────────────────────────────────────


@admin.register(DataSource)
class DataSourceAdmin(SavesChangedFields, GraphAdmin):
    list_display = ("slug", "name", "kind")
    list_filter = ("kind",)
    search_fields = ("slug", "name")


@admin.register(SourceDataset)
class SourceDatasetAdmin(SavesChangedFields, GraphAdmin):
    list_display = ("slug", "title", "data_source", "status")
    list_filter = ("status",)
    list_select_related = ("data_source",)
    search_fields = ("slug", "title", "short_title")
    readonly_fields = ("status",)


class DatasetLinkInline(SwitchedPermissions, admin.TabularInline):
    model = SourceDatasetPublication
    extra = 0
    fields = ("dataset", "role")


class ReleaseLinkInline(ReleaseChoices, SwitchedPermissions, admin.TabularInline):
    model = ReleasePublication
    extra = 0
    fields = ("release", "role")


@admin.register(Publication)
class PublicationAdmin(SavesChangedFields, GraphAdmin):
    IDENTIFIERS = (
        "pmid",
        "doi",
        "doi_lookup",
        "pmcid",
        "imex_id",
        "local_dataset",
        "local_key",
        "is_synthetic",
    )

    fields = (*IDENTIFIERS, "title", "authors", "journal", "year", "url")
    readonly_fields = IDENTIFIERS
    inlines = (DatasetLinkInline, ReleaseLinkInline)
    list_display = ("__str__", "journal", "year", "is_synthetic")
    list_filter = ("is_synthetic",)
    search_fields = ("pmid", "doi", "pmcid", "imex_id", "local_key", "title")

    def has_add_permission(self, request):
        return False


@admin.register(DatasetRelease)
class DatasetReleaseAdmin(ViewOnlyAdmin):
    # Without stats: a release's figures describe its data, which only the
    # visibility service shows.
    fields = (
        "dataset",
        "version",
        "kind",
        "status",
        "is_synthetic",
        "supersedes",
        "release_date",
        "retrieved_at",
        "source_url",
        "embargo_until",
        "declared_taxa",
        "adapter",
        "adapter_version",
        "mapping_version",
        "store_raw_rows",
        "published_at",
        "published_by_username",
        "withdrawn_at",
        "withdrawn_by_username",
        "withdrawn_reason",
        "created_at",
        "updated_at",
    )
    readonly_fields = fields
    list_display = (
        "dataset",
        "version",
        "kind",
        "status",
        "is_synthetic",
        "published_at",
    )
    list_filter = ("status", "kind", "is_synthetic")
    list_select_related = ("dataset",)
    search_fields = ("dataset__slug", "version")


# ── Licensing ───────────────────────────────────────────────────────────────

CONFIRM = "Confirm due diligence covers the current terms"
CONFIRMED_NOTE = (
    "Confirmed that due diligence covers the current terms (Django admin action)."
)
# What the form shows of a record, with when it was confirmed: a form opened
# on other values is refused.
VERSIONED_FIELDS = (*DueDiligenceRecord.AUDITED_FIELDS, "notes")
AUDITED_IN_FORM = frozenset(DueDiligenceRecord.AUDITED_FIELDS) - {"completed_at"}
STALE = (
    "The record changed after this page was opened, so nothing was saved. "
    "Reload the page to see it as it is now, then make the change again."
)
NOTE_WITHOUT_CHANGE = (
    "A note is kept with the history row that a change to the status, the "
    "time-limited flag, the reviewer, the review date or the notes link writes. "
    "Change one of them, or leave the note blank."
)


def record_version(record) -> str:
    """A digest of the record's versioned fields as stored. A digest, not
    the values, so that no newline in the notes is changed on its way back
    from the browser."""
    values = [str(getattr(record, name)) for name in VERSIONED_FIELDS]
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


class DueDiligenceForm(forms.ModelForm):
    """A due-diligence record's form: the record's fields, a note for the
    history row the change writes, and the version of the record the form
    was opened on."""

    change_note = forms.CharField(
        label="Note for the history",
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text="Kept with the history row that this change writes.",
    )
    opened_version = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk is not None:
            self.fields["opened_version"].initial = record_version(self.instance)

    def clean(self):
        cleaned = super().clean()
        if self.instance.pk is None:
            return cleaned
        # The lock lasts until the admin's transaction ends, so the record
        # cannot change between this check and the save.
        with transaction.atomic():
            stored = (
                DueDiligenceRecord.objects.select_for_update()
                .filter(pk=self.instance.pk)
                .first()
            )
        if stored is None or record_version(stored) != cleaned.get("opened_version"):
            raise ValidationError(STALE, code="stale")
        if cleaned.get("change_note") and not AUDITED_IN_FORM & set(self.changed_data):
            self.add_error("change_note", NOTE_WITHOUT_CHANGE)
        return cleaned


class DueDiligenceHistoryInline(ViewOnlyInline):
    model = DueDiligenceChange
    fields = (
        "changed_at",
        "changed_by_username",
        "status",
        "terms_time_limited",
        "reviewer",
        "reviewed_on",
        "notes_link",
        "completed_at",
        "note",
    )
    readonly_fields = fields


@admin.register(DueDiligenceRecord)
class DueDiligenceRecordAdmin(GraphAdmin):
    FIELDS = (
        "dataset",
        "status",
        "terms_time_limited",
        "reviewer",
        "reviewed_on",
        "notes_link",
        "notes",
    )

    form = DueDiligenceForm
    inlines = (DueDiligenceHistoryInline,)
    actions = ("confirm_current_terms",)
    list_display = (
        "dataset",
        "status",
        "terms_time_limited",
        "reviewer",
        "reviewed_on",
        "completed_at",
    )
    list_filter = ("status", "terms_time_limited")
    list_select_related = ("dataset",)
    search_fields = ("dataset__slug", "reviewer")

    def get_fields(self, request, obj=None):
        if obj is None:
            return (*self.FIELDS, "change_note")
        if not self.has_change_permission(request, obj):
            return (*self.FIELDS, "completed_at")
        return (*self.FIELDS, "completed_at", "change_note", "opened_version")

    def get_readonly_fields(self, request, obj=None):
        # A record stays with its dataset, and completed_at is the model's.
        return ("dataset", "completed_at") if obj is not None else ()

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        obj._changed_by = request.user
        obj._change_note = form.cleaned_data.get("change_note", "")
        if change:
            obj.save(update_fields=[*changed_fields(form), "updated_at"])
        else:
            obj.save()

    @admin.action(description=CONFIRM, permissions=["change"])
    def confirm_current_terms(self, request, queryset):
        confirmed = []
        for record in queryset.select_related("dataset").order_by("pk"):
            try:
                licensing.confirm_due_diligence(
                    record, by=request.user, note=CONFIRMED_NOTE
                )
            except licensing.LicensingError as error:
                self.message_user(request, f"{record.dataset}: {error}", messages.ERROR)
            else:
                confirmed.append(str(record.dataset))
        if confirmed:
            self.message_user(
                request,
                f"Due diligence confirmed for {', '.join(confirmed)}.",
                messages.SUCCESS,
            )


@admin.register(DueDiligenceChange)
class DueDiligenceChangeAdmin(ViewOnlyAdmin):
    fields = ("record", *DueDiligenceHistoryInline.fields)
    readonly_fields = fields
    list_display = ("record", "status", "changed_at", "changed_by_username")
    list_filter = ("status",)
    list_select_related = ("record",)


@admin.register(ReleaseLicence)
class ReleaseLicenceAdmin(ReleaseChoices, ReleaseColumn, GraphAdmin):
    list_display = (
        "id",
        "release_name",
        "licence_name",
        "redistribution",
        "modification",
        "share_alike",
        "recorded_at",
        "recorded_by_username",
    )
    list_filter = ("redistribution", "modification", "share_alike")
    list_select_related = ("release__dataset",)
    search_fields = (
        "release__dataset__slug",
        "release__version",
        "licence_name",
        "spdx_id",
    )

    def get_fields(self, request, obj=None):
        if obj is None:
            return ("release", *licensing.LICENCE_FIELDS)
        return (
            "release",
            *licensing.LICENCE_FIELDS,
            "recorded_at",
            "recorded_by_username",
        )

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        recorded = licensing.record_licence(
            form.cleaned_data["release"],
            recorded_by=request.user,
            **{name: form.cleaned_data[name] for name in licensing.LICENCE_FIELDS},
        )
        # The admin goes on to log and show the row record_licence wrote.
        for field in obj._meta.concrete_fields:
            setattr(obj, field.attname, getattr(recorded, field.attname))
        obj._state.adding = False
        obj._state.db = recorded._state.db


@admin.register(UsageApproval)
class UsageApprovalAdmin(ReleaseColumn, ViewOnlyAdmin):
    fields = (
        "release",
        "scope",
        "decision",
        "licence",
        "rationale",
        "evidence",
        "decided_by_username",
        "decided_at",
        "revoked_at",
        "revoked_by_username",
        "revoke_reason",
    )
    readonly_fields = fields
    list_display = (
        "id",
        "release_name",
        "scope",
        "decision",
        "decided_by_username",
        "decided_at",
        "revoked_at",
    )
    list_filter = ("scope", "decision")
    list_select_related = ("release__dataset",)
    search_fields = (
        "release__dataset__slug",
        "release__version",
        "decided_by_username",
    )

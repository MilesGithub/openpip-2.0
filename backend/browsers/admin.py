"""Browser instances in the Django admin, for staff, while the switch is on.

As in graph's admin (graph.admin), the module is absent and every page is
refused while TYPED_GRAPH_ENABLED is off, to superusers too, and Django's
permissions decide while it is on. An instance's header citation and its
selected releases are edited in two inlines on its page. Whoever may add or
change an instance may edit both, whatever their permissions on the two row
models, so the selection's rules always run.

Where each rule runs, every error keyed to a field of the form or shown as
the selection's own:
- a rule on one field: the model's field validators (browsers.rules,
  browsers.presentation), which the form runs;
- the facets (the primary taxon among the taxa, assays PSI-MI terms): the
  form's clean(), from the submitted values;
- the selection (rules.validate_selection): the selection formset's
  clean(), which Django runs after the form has set the submitted values on
  the instance, so a change of kind and the rows it leaves are judged
  together;
- the database constraints: validate_constraints().

The pickers refuse a value that could name no row (a control character in
a slug or CURIE, anything but a whole number for a numeric key) before the
database is read, and never render one, whether it was posted or came from
the add page's query string, so a crafted form submission is a form error
rather than a database error. The search behind an autocomplete picker is
Django's own view, which runs it in the admin of the model picked from, so
this module guards only a search for an instance (a NUL finds nothing), not
those of graph's admins for taxa, terms and papers. Tags are checkboxes, and
presentation is JSON of at most MAX_PRESENTATION_CHARS characters.

The slug is the instance's address, which links and saved views keep, so
it is read-only once the instance is saved. An instance may be deleted; the
operator guide says to retire one instead. Nothing here is rendered as HTML.
"""

import json
import re

from django import forms
from django.contrib import admin, messages
from django.contrib.admin import helpers
from django.contrib.admin.options import IncorrectLookupParameters
from django.contrib.admin.views.main import ChangeList
from django.contrib.auth import get_permission_codename
from django.core.exceptions import ValidationError
from django.db import (
    DataError,
    NotSupportedError,
    OperationalError,
    ProgrammingError,
    connection,
    models,
)
from django.db.models.constants import LOOKUP_SEP
from django.forms.fields import InvalidJSONInput
from django.forms.models import BaseInlineFormSet

from graph.admin import GraphAdmin, ReleaseChoices, SwitchedPermissions
from graph.preconditions import typed_graph_enabled

from .models import BrowserInstance, BrowserInstancePublication, BrowserInstanceRelease
from .presentation import MAX_PRESENTATION_CHARS, NOT_JSON, TOO_LONG
from .rules import (
    CONTROL_CHARACTERS,
    MAX_TAGS,
    TAGS,
    validate_facets,
    validate_selection,
)

# Every editable field, in the form's order. updated_by is not one: a select
# of it would list every account, and save_model sets it.
EDITABLE_FIELDS = (
    "slug",
    "title",
    "short_title",
    "summary",
    "description",
    "methods_text",
    "kind",
    "status",
    "visibility",
    "external_url",
    "is_synthetic",
    "taxa",
    "primary_taxon",
    "relationship_types",
    "entity_types",
    "assays",
    "tags",
    "presentation",
    "allow_external_services",
    "display_order",
)
READ_ONLY_FIELDS = ("created_at", "updated_at", "updated_by_username")

# ── Pickers that never send the database a value it would refuse ───────────

# Primary keys are signed 64-bit integers: a larger one names no row, and
# SQLite cannot even compare one with a column (graph.preconditions).
_LARGEST_KEY = 2**63 - 1
_DIGITS = re.compile(r"[0-9]{1,19}")


def nameable(field, value) -> bool:
    """Whether `value` can name a row of the field's model without a
    database error: a whole number of at most 19 digits for an integer key,
    a string with no control character for a string key (a slug or a CURIE).
    Django 5.0 reads every submitted value of a many-to-many field in one
    filter outside its own error handling, and the autocomplete widget
    filters by the values it renders, so a NUL would reach PostgreSQL."""
    if isinstance(value, models.Model):
        return True
    model = field.queryset.model
    key = model._meta.get_field(field.to_field_name or model._meta.pk.name)
    if isinstance(key, models.IntegerField):
        if isinstance(value, int) and not isinstance(value, bool):
            return 0 <= value <= _LARGEST_KEY
        return (
            isinstance(value, str)
            and _DIGITS.fullmatch(value) is not None
            and int(value) <= _LARGEST_KEY
        )
    return isinstance(value, str) and CONTROL_CHARACTERS.search(value) is None


class SafeModelChoiceField(forms.ModelChoiceField):
    """A ModelChoiceField that refuses a value no row could have before the
    database is read, and never renders it again (nameable)."""

    def clean(self, value):
        if value not in self.empty_values and not nameable(self, value):
            raise ValidationError(
                self.error_messages["invalid_choice"],
                code="invalid_choice",
                params={"value": value},
            )
        return super().clean(value)

    def bound_data(self, data, initial):
        data = super().bound_data(data, initial)
        return data if data in self.empty_values or nameable(self, data) else None

    def prepare_value(self, value):
        # The add page prefills its form from the query string, which no
        # clean() or bound_data() sees; every value rendered comes through here.
        if value in self.empty_values or nameable(self, value):
            return super().prepare_value(value)
        return None


class SafeModelMultipleChoiceField(forms.ModelMultipleChoiceField):
    """SafeModelChoiceField's rule for a many-to-many field."""

    def clean(self, value):
        if isinstance(value, (list, tuple)):
            for item in value:
                if not nameable(self, item):
                    raise ValidationError(
                        self.error_messages["invalid_pk_value"],
                        code="invalid_pk_value",
                        params={"pk": item},
                    )
        return super().clean(value)

    def bound_data(self, data, initial):
        data = super().bound_data(data, initial)
        if isinstance(data, (list, tuple)):
            return [item for item in data if nameable(self, item)]
        return data if data in self.empty_values or nameable(self, data) else []

    def prepare_value(self, value):
        if isinstance(value, (list, tuple)):
            value = [item for item in value if nameable(self, item)]
        return super().prepare_value(value)


class PresentationField(forms.JSONField):
    """presentation's JSON, at most MAX_PRESENTATION_CHARS characters. Text
    that json.loads cannot read (a number too long, nesting too deep), or
    reads into a string no page can carry (an escape that leaves half a
    surrogate pair), is refused as not JSON, and is shown again as typed,
    never parsed again: Django's own field catches only JSONDecodeError, when
    it cleans and when it renders."""

    default_error_messages = {"invalid": NOT_JSON}

    def to_python(self, value):
        if isinstance(value, str) and len(value) > MAX_PRESENTATION_CHARS:
            raise ValidationError(TOO_LONG, code="too_long")
        try:
            converted = super().to_python(value)
            self._encode(converted)
        except (RecursionError, ValueError):
            raise ValidationError(
                self.error_messages["invalid"], code="invalid"
            ) from None
        return converted

    def bound_data(self, data, initial):
        if self.disabled or data is None:
            return super().bound_data(data, initial)
        if len(data) > MAX_PRESENTATION_CHARS:
            return InvalidJSONInput(data)
        try:
            converted = json.loads(data, cls=self.decoder)
            self._encode(converted)
        except (RecursionError, ValueError):
            return InvalidJSONInput(data)
        return converted

    def _encode(self, converted):
        # Raises UnicodeEncodeError, a ValueError, for a value the page that
        # repeats it could not send: the form's error names it, and the
        # textarea shows it again.
        json.dumps(converted, ensure_ascii=False, cls=self.encoder).encode("utf-8")


class HttpsURLField(forms.URLField):
    """A URL typed without a scheme is taken as https. GraphAdmin's
    formfield_overrides say so for the fields the admin builds; this says so
    for the form's own class, which the admin also builds without them."""

    def __init__(self, **kwargs):
        kwargs.setdefault("assume_scheme", "https")
        super().__init__(**kwargs)


# ── The instance ────────────────────────────────────────────────────────────


class BrowserInstanceForm(forms.ModelForm):
    """The admin's form for an instance. The pickers get their safe classes
    through Meta.field_classes, which the admin's formfield callback
    receives, so each keeps the admin's widget; a field declared here would
    replace it. Only tags and presentation are declared, with the model's
    defaults as their initial values."""

    tags = forms.MultipleChoiceField(
        choices=[(tag, tag) for tag in TAGS],
        required=False,
        initial=list,
        widget=forms.CheckboxSelectMultiple,
        label="Tags",
        help_text=f"Biology areas, for the gallery's tag filter: at most {MAX_TAGS}.",
    )
    presentation = PresentationField(
        required=False,
        initial=dict,
        label="Presentation",
        help_text=(
            "Display settings, as a JSON object of presentation schema v1 (see "
            "the operator guide's page on browser instances). Empty, or {}, "
            "means every default."
        ),
    )

    class Meta:
        model = BrowserInstance
        fields = EDITABLE_FIELDS
        field_classes = {
            "relationship_types": SafeModelMultipleChoiceField,
            "entity_types": SafeModelMultipleChoiceField,
            "assays": SafeModelMultipleChoiceField,
            "taxa": SafeModelMultipleChoiceField,
            "primary_taxon": SafeModelChoiceField,
            "external_url": HttpsURLField,
        }
        labels = {
            "methods_text": "Methods",
            "external_url": "External URL",
            "is_synthetic": "Synthetic data",
        }
        help_texts = {
            "slug": (
                "The instance's address under /browsers/: lower-case letters and "
                "digits in groups joined by single hyphens, such as "
                "synthetic-regulation. Fixed once the instance is saved, since "
                "links keep it."
            ),
            "title": "Plain text, shown as typed.",
            "short_title": "For narrow places. Plain text; optional.",
            "summary": "One or two sentences for the gallery card. Plain text.",
            "description": "Plain text, shown as typed; line breaks are kept.",
            "methods_text": (
                "How the data were made. Plain text, shown as typed; line breaks "
                "are kept."
            ),
            "kind": (
                "Typed data shows the releases selected below. This portal's "
                "search links to /search. External portal links to the URL below. "
                "Citation card shows its header citation, with no data."
            ),
            "status": (
                "A planned or retired instance shows as such, whatever its "
                "releases. An available typed instance is live while every "
                "selected release is published or superseded and visible to the "
                "reader, and data unavailable otherwise."
            ),
            "visibility": (
                "Who sees the instance, never its data. Unlisted keeps it out of "
                "the gallery, but anyone who knows its slug can open it. To hide "
                "data, withdraw the release or revoke its approval."
            ),
            "external_url": (
                "An external portal's https address, which it needs; no other "
                "kind has one. Shown as stored, and never fetched."
            ),
            "is_synthetic": (
                "Invented demonstration data. An instance also reads as "
                "synthetic while a selected release the reader may see is."
            ),
            "taxa": (
                "The organisms covered. Left empty, the taxa its selected "
                "releases declare."
            ),
            "primary_taxon": "The organism shown first: one of the taxa, if any.",
            "relationship_types": (
                "The relationship types covered. Left empty, those its selected "
                "releases hold, reference links aside."
            ),
            "entity_types": (
                "The entity types covered, as stated here: never read from the "
                "releases."
            ),
            "assays": (
                "Detection methods, as PSI-MI terms such as MI:0018. Only terms "
                "an import has recorded can be picked."
            ),
            "allow_external_services": (
                "Will let a typed instance's pages send identifiers to outside "
                "services, while it is live and every selected release is approved "
                "for external services."
            ),
            "display_order": "The gallery's order: lower first, then by slug.",
        }

    def clean(self):
        cleaned = super().clean()
        validate_facets(
            taxa=cleaned.get("taxa"),
            primary_taxon=cleaned.get("primary_taxon"),
            assays=cleaned.get("assays"),
        )
        return cleaned


# ── Its rows ────────────────────────────────────────────────────────────────


class InstancePermissions:
    """The permissions of an inline whose rows are part of the instance: the
    instance's, behind the switch, as Django answers for an auto-created
    through model. On a change page (with the instance), viewing asks the
    instance's view or change permission, and adding, changing and deleting
    rows its change permission. With no instance (the add page), each asks
    the add permission; viewing then also accepts view or change, since
    Django asks it with no instance on every page to filter the rows an
    inline shows."""

    def _allows(self, request, *actions) -> bool:
        opts = self.parent_model._meta
        return typed_graph_enabled() and any(
            request.user.has_perm(
                f"{opts.app_label}.{get_permission_codename(action, opts)}"
            )
            for action in actions
        )

    def has_view_permission(self, request, obj=None):
        if obj is None:
            return self._allows(request, "add", "view", "change")
        return self._allows(request, "view", "change")

    def has_add_permission(self, request, obj):
        return self._allows(request, "add" if obj is None else "change")

    def has_change_permission(self, request, obj=None):
        return self._allows(request, "add" if obj is None else "change")

    def has_delete_permission(self, request, obj=None):
        return self._allows(request, "add" if obj is None else "change")


class BrowserInstancePublicationForm(forms.ModelForm):
    class Meta:
        model = BrowserInstancePublication
        fields = ("publication", "role", "order")
        field_classes = {"publication": SafeModelChoiceField}


class BrowserInstancePublicationInline(
    InstancePermissions, SwitchedPermissions, admin.TabularInline
):
    """The header citation, in order: papers a commit or the registry holds,
    since none is added here."""

    model = BrowserInstancePublication
    form = BrowserInstancePublicationForm
    fields = ("publication", "role", "order")
    autocomplete_fields = ("publication",)
    extra = 0
    verbose_name = "cited paper"
    verbose_name_plural = "header citation"


class SelectionFormSet(BaseInlineFormSet):
    """An instance's selected releases. Django cleans this formset after the
    instance's form, which has by then set the submitted values on
    self.instance."""

    def clean(self):
        super().clean()
        named = {form.instance.pk for form in self.forms}
        validate_selection(
            self.instance,
            [
                *(
                    form.cleaned_data["release"]
                    for form in self.forms
                    if form.cleaned_data.get("release") is not None
                    and not self._should_delete_form(form)
                ),
                # A stored row no form names stays selected: one added after
                # this page was opened, or left out of a crafted POST.
                *(row for row in self.get_queryset() if row.pk not in named),
            ],
        )


class BrowserInstanceReleaseInline(
    InstancePermissions, ReleaseChoices, SwitchedPermissions, admin.TabularInline
):
    """The selected releases, of either role, offered by dataset and version
    with their status."""

    model = BrowserInstanceRelease
    formset = SelectionFormSet
    fields = ("release", "role", "order")
    extra = 0
    verbose_name = "selected release"
    verbose_name_plural = "selected releases"


# Django's own sentence for an action posted with nothing selected.
NOTHING_SELECTED = (
    "Items must be selected in order to perform actions on them. "
    "No items have been changed."
)


# More separators than any lookup here needs. A longer path can overflow the
# stack when Django compiles it, in a posted action as well as in the list.
MAX_LOOKUP_SEPARATORS = 32


class CheckedChangeList(ChangeList):
    """A list whose query string cannot be run answers as any other wrong
    lookup does: Django's redirect to ?e=1. Django guards only the building
    of the filter; the statement is compiled and sent later, where a lookup
    it accepted can still fail (a __range value that is not two items, a
    year no date can hold, a large integer as a JSON key) or be refused (a
    NUL, an invalid regular expression, which only the database can judge).
    A lookup path past MAX_LOOKUP_SEPARATORS is refused before the filter is
    built, since a posted action compiles the statement again deeper in the
    stack. On PostgreSQL a database that fails stays an error; SQLite raises
    one class for that and for a lookup it refuses, so on that fallback both
    redirect."""

    def get_filters_params(self, params=None):
        lookup_params = super().get_filters_params(params)
        if any(key.count(LOOKUP_SEP) > MAX_LOOKUP_SEPARATORS for key in lookup_params):
            raise IncorrectLookupParameters("A lookup path this deep names nothing.")
        return lookup_params

    def get_results(self, request):
        try:
            super().get_results(request)
            # Django sends the list's own statement after this returns. SQLite
            # runs a regular expression row by row, in an order each statement's
            # plan decides, so the list can reach one the count did not.
            len(self.result_list)
        except (
            DataError,
            IndexError,
            NotImplementedError,
            NotSupportedError,
            OverflowError,
            ProgrammingError,
            RecursionError,
            TypeError,
            ValueError,
        ) as error:
            # psycopg2 raises ValueError for a NUL, before PostgreSQL sees it.
            # NotImplementedError and NotSupportedError are SQLite's: it has no
            # JSON contains lookup, and none of PostgreSQL's range lookups.
            raise IncorrectLookupParameters(error) from error
        except OperationalError as error:
            # SQLite's class for a regular expression it cannot compile. On
            # PostgreSQL it means the database failed, which stays a 500.
            if connection.vendor != "sqlite":
                raise
            raise IncorrectLookupParameters(error) from error


@admin.register(BrowserInstance)
class BrowserInstanceAdmin(GraphAdmin):
    form = BrowserInstanceForm
    fields = (*EDITABLE_FIELDS, *READ_ONLY_FIELDS)
    readonly_fields = READ_ONLY_FIELDS
    list_display = ("slug", "title", "kind", "status", "visibility", "display_order")
    list_filter = ("kind", "status", "visibility")
    search_fields = ("slug", "title", "short_title", "summary")
    ordering = ("display_order", "slug")
    # Each count beside a filter drops that filter and runs while the page
    # renders, outside CheckedChangeList's guard.
    show_facets = admin.ShowFacets.NEVER
    # Their admins search these registries; the two small ones have no
    # search, so they are picked from a list.
    autocomplete_fields = ("taxa", "primary_taxon", "assays")
    filter_horizontal = ("relationship_types", "entity_types")
    inlines = (BrowserInstancePublicationInline, BrowserInstanceReleaseInline)

    def get_changelist(self, request, **kwargs):
        return CheckedChangeList

    def get_search_results(self, request, queryset, search_term):
        # Django's autocomplete view passes its term here unchecked, and
        # psycopg2 refuses a NUL. PostgreSQL text cannot hold one, so no row
        # matches.
        if "\x00" in search_term:
            return queryset.none(), False
        return super().get_search_results(request, queryset, search_term)

    def response_action(self, request, queryset):
        # Django filters by the posted ids outside any error handling, so one
        # that is not a whole number would be a 500. No row can have it.
        selected = request.POST.getlist(helpers.ACTION_CHECKBOX_NAME)
        if not all(
            _DIGITS.fullmatch(value) and int(value) <= _LARGEST_KEY
            for value in selected
        ):
            self.message_user(request, NOTHING_SELECTED, messages.WARNING)
            return None
        return super().response_action(request, queryset)

    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)
        # A field validator is skipped for a read-only field, so a saved
        # slug that has since become reserved still saves.
        return (*fields, "slug") if obj is not None else fields

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        obj.updated_by_username = request.user.get_username()
        super().save_model(request, obj, form, change)

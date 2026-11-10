"""Browser instances in the Django admin (browsers/admin.py): absent while
the switch is off; while it is on, under the instance's permissions, with
its header citation and its selected releases in two inlines whose rules
always run, and pickers that turn a crafted value into a form error."""

import pytest
from django.contrib import admin
from django.contrib.admin.utils import flatten_fieldsets
from django.contrib.admin.views.main import ChangeList
from django.contrib.admin.widgets import (
    AutocompleteSelect,
    AutocompleteSelectMultiple,
    FilteredSelectMultiple,
    RelatedFieldWidgetWrapper,
)
from django import forms
from django.contrib.auth.models import Permission as AuthPermission
from django.db import OperationalError, connection, transaction
from django.forms import CheckboxSelectMultiple
from django.urls import reverse
from django.utils.html import escape

from browsers import rules
from browsers.admin import (
    NOTHING_SELECTED,
    BrowserInstanceForm,
    PresentationField,
    SafeModelChoiceField,
    SafeModelMultipleChoiceField,
)
from browsers.models import (
    BrowserInstance,
    BrowserInstancePublication,
    BrowserInstanceRelease,
    Kind,
    Role,
    Status,
)
from browsers.presentation import NOT_OBJECT, TOO_LONG
from graph.tests.factories import (
    DatasetReleaseFactory,
    PublicationFactory,
    StaffUserFactory,
    TermFactory,
    taxon_of,
)
from graph.tests.test_admin import errors
from graph.tests.test_admin import submitted as graph_submitted

INVALID_CHOICE = (
    "Select a valid choice. That choice is not one of the available choices."
)
MULTIPLE = (forms.MultipleChoiceField, forms.ModelMultipleChoiceField)


def submitted(page, **changes) -> dict:
    """What a browser posts for the admin form on `page`, inlines included,
    with `changes` typed over it (graph's helper), where a multiple select
    left with nothing chosen sends nothing: an add page's many-to-many
    fields start with no value at all."""
    data = graph_submitted(page, **changes)
    for name, field in page.context["adminform"].form.fields.items():
        if isinstance(field, MULTIPLE) and data.get(name) == "":
            del data[name]
    return data


def url(view, *args) -> str:
    return reverse(f"admin:browsers_browserinstance_{view}", args=args)


def make(**fields) -> BrowserInstance:
    return BrowserInstance.objects.create(
        **{"slug": "an-instance", "title": "An instance", "kind": Kind.TYPED, **fields}
    )


def stored(instance) -> dict:
    return BrowserInstance.objects.filter(pk=instance.pk).values().get()


def selected(instance) -> list:
    """(release id, role) of each selected release, in order."""
    return list(instance.selection.values_list("release_id", "role"))


def permission(name) -> AuthPermission:
    app_label, codename = name.split(".")
    return AuthPermission.objects.get(
        content_type__app_label=app_label, codename=codename
    )


def account(*names):
    """An active staff account holding these permissions and no others."""
    user = StaffUserFactory()
    user.user_permissions.add(*(permission(name) for name in names))
    return user


def inlines(page) -> dict:
    return {
        inline.formset.prefix: inline
        for inline in page.context["inline_admin_formsets"]
    }


def selection_errors(response) -> list:
    """The selection formset's own errors."""
    return list(inlines(response)["selection"].formset.non_form_errors())


def row(index, release, role=Role.PRIMARY, order=0) -> dict:
    """A new selection row for the add or change form."""
    return {
        f"selection-{index}-release": release.pk,
        f"selection-{index}-role": role,
        f"selection-{index}-order": order,
    }


@pytest.fixture
def superuser(db):
    return StaffUserFactory(username="browsers-superuser", is_superuser=True)


@pytest.fixture
def signed_in(client, superuser):
    """The admin, signed in as a superuser."""
    client.force_login(superuser)
    return client


# ── The switch ──────────────────────────────────────────────────────────────


def test_the_app_is_absent_from_the_admin_while_the_switch_is_off(signed_in, settings):
    settings.TYPED_GRAPH_ENABLED = False
    make()
    index = signed_in.get(reverse("admin:index"))
    assert index.status_code == 200
    labels = {app["app_label"] for app in index.context["app_list"]}
    assert "core" in labels and "browsers" not in labels
    assert b"Browser instances" not in index.content
    app_index = reverse("admin:app_list", kwargs={"app_label": "browsers"})
    assert signed_in.get(app_index).status_code == 404


def test_every_page_is_refused_while_the_switch_is_off(signed_in, settings):
    settings.TYPED_GRAPH_ENABLED = False
    instance = make()
    before = stored(instance)
    pages = [
        url("changelist"),
        url("add"),
        url("change", instance.pk),
        url("delete", instance.pk),
        url("history", instance.pk),
        # An unknown id is answered as a known one is.
        url("change", 999_999),
        url("delete", 999_999),
        url("history", 999_999),
    ]
    for page in pages:
        assert signed_in.get(page).status_code == 403, page
    assert signed_in.post(url("add"), {"slug": "x"}).status_code == 403
    assert signed_in.post(url("change", instance.pk), {}).status_code == 403
    deleted = signed_in.post(url("delete", instance.pk), {"post": "yes"})
    assert deleted.status_code == 403
    search = signed_in.get(
        reverse("admin:autocomplete"),
        {
            "app_label": "browsers",
            "model_name": "browserinstance",
            "field_name": "taxa",
        },
    )
    assert search.status_code == 403
    assert stored(instance) == before


def test_the_app_is_listed_while_the_switch_is_on(signed_in, switch_on):
    instance = make()
    index = signed_in.get(reverse("admin:index"))
    [app] = [app for app in index.context["app_list"] if app["app_label"] == "browsers"]
    assert app["name"] == "Browser instances"
    assert [entry["object_name"] for entry in app["models"]] == ["BrowserInstance"]
    app_index = reverse("admin:app_list", kwargs={"app_label": "browsers"})
    assert signed_in.get(app_index).status_code == 200
    for page in (
        url("changelist"),
        url("add"),
        url("change", instance.pk),
        url("history", instance.pk),
        url("delete", instance.pk),
    ):
        assert signed_in.get(page).status_code == 200, page


def test_only_the_instance_is_registered_and_its_admin_passes_the_checks():
    registered = {
        model for model in admin.site._registry if model._meta.app_label == "browsers"
    }
    assert registered == {BrowserInstance}
    model_admin = admin.site._registry[BrowserInstance]
    assert model_admin.autocomplete_fields == ("taxa", "primary_taxon", "assays")
    assert model_admin.filter_horizontal == ("relationship_types", "entity_types")
    # Inlines are checked with the admin that holds them.
    assert model_admin.check() == []


def test_staff_need_their_django_permissions_while_the_switch_is_on(client, switch_on):
    staff = StaffUserFactory()
    client.force_login(staff)
    instance = make()
    index = client.get(reverse("admin:index"))
    assert "browsers" not in {app["app_label"] for app in index.context["app_list"]}
    assert client.get(url("changelist")).status_code == 403
    assert client.get(url("change", instance.pk)).status_code == 403

    staff.user_permissions.add(permission("browsers.view_browserinstance"))
    assert client.get(url("changelist")).status_code == 200
    page = client.get(url("change", instance.pk))
    assert page.status_code == 200
    assert page.context["has_change_permission"] is False
    shown = inlines(page)
    assert set(shown) == {"citations", "selection"}
    for inline in shown.values():
        assert inline.has_view_permission
        assert not (
            inline.has_add_permission
            or inline.has_change_permission
            or inline.has_delete_permission
        )
    assert client.get(url("add")).status_code == 403
    assert client.post(url("change", instance.pk), {}).status_code == 403
    assert client.get(url("delete", instance.pk)).status_code == 403


# ── The form ────────────────────────────────────────────────────────────────


def test_the_forms_hold_no_updated_by_and_each_save_records_who_saved(
    client, signed_in, switch_on, superuser
):
    page = signed_in.get(url("add"))
    assert "updated_by" not in page.context["adminform"].form.fields
    assert b'name="updated_by"' not in page.content
    response = signed_in.post(
        url("add"),
        submitted(page, slug="a-card", title="A card", kind=Kind.METADATA_ONLY),
    )
    assert response.status_code == 302, errors(response)
    instance = BrowserInstance.objects.get(slug="a-card")
    assert (instance.updated_by, instance.updated_by_username) == (
        superuser,
        "browsers-superuser",
    )

    editor = account("browsers.change_browserinstance")
    client.force_login(editor)
    page = client.get(url("change", instance.pk))
    assert "updated_by" not in page.context["adminform"].form.fields
    assert b'name="updated_by"' not in page.content
    response = client.post(url("change", instance.pk), submitted(page, title="Renamed"))
    assert response.status_code == 302, errors(response)
    row = stored(instance)
    assert (row["title"], row["updated_by_id"], row["updated_by_username"]) == (
        "Renamed",
        editor.pk,
        editor.username,
    )


def test_the_slug_is_read_only_once_saved(signed_in, switch_on):
    assert "slug" in signed_in.get(url("add")).context["adminform"].form.fields
    instance = make(slug="kept")
    page = signed_in.get(url("change", instance.pk))
    assert "slug" not in page.context["adminform"].form.fields
    assert "slug" in flatten_fieldsets(page.context["adminform"].fieldsets)
    response = signed_in.post(
        url("change", instance.pk), submitted(page, slug="changed", title="Retitled")
    )
    assert response.status_code == 302, errors(response)
    assert (stored(instance)["slug"], stored(instance)["title"]) == ("kept", "Retitled")


def test_an_instance_whose_slug_has_become_reserved_still_saves(
    signed_in, switch_on, monkeypatch
):
    instance = make(slug="gallery")
    monkeypatch.setattr(
        rules, "RESERVED_SLUGS", (*rules.RESERVED_SLUGS, "gallery", "showcase")
    )
    page = signed_in.get(url("change", instance.pk))
    response = signed_in.post(url("change", instance.pk), submitted(page, title="Kept"))
    assert response.status_code == 302, errors(response)
    assert stored(instance)["title"] == "Kept"
    # A new instance may not take a reserved slug.
    page = signed_in.get(url("add"))
    response = signed_in.post(
        url("add"),
        submitted(page, slug="showcase", title="New", kind=Kind.METADATA_ONLY),
    )
    assert response.status_code == 200
    assert errors(response)["slug"] == ["'showcase' is reserved."]


def test_no_help_text_holds_markup():
    # Django's admin shows help text unescaped, so a tag would vanish from it.
    for name, field in BrowserInstanceForm.base_fields.items():
        assert "<" not in str(field.help_text), name


def test_a_taken_slug_is_a_form_error(signed_in, switch_on):
    make(slug="taken")
    page = signed_in.get(url("add"))
    response = signed_in.post(
        url("add"), submitted(page, slug="taken", title="T", kind=Kind.METADATA_ONLY)
    )
    assert response.status_code == 200
    assert errors(response)["slug"] == [
        "Browser instance with this Slug already exists."
    ]


def test_a_url_typed_without_a_scheme_becomes_https_and_http_or_ftp_is_refused(
    signed_in, switch_on
):
    page = signed_in.get(url("add"))
    response = signed_in.post(
        url("add"),
        submitted(
            page,
            slug="elsewhere",
            title="Elsewhere",
            kind=Kind.EXTERNAL,
            external_url="portal.example/browse",
        ),
    )
    assert response.status_code == 302, errors(response)
    instance = BrowserInstance.objects.get(slug="elsewhere")
    assert instance.external_url == "https://portal.example/browse"
    page = signed_in.get(url("change", instance.pk))
    for value in ("http://portal.example/", "ftp://portal.example/"):
        response = signed_in.post(
            url("change", instance.pk), submitted(page, external_url=value)
        )
        assert response.status_code == 200
        assert errors(response)["external_url"] == [rules.NOT_HTTPS], value
    response = signed_in.post(
        url("change", instance.pk), submitted(page, external_url="")
    )
    assert errors(response)["external_url"] == [rules.EXTERNAL_NEEDS_URL]
    assert stored(instance)["external_url"] == "https://portal.example/browse"


def test_the_facet_rules_are_form_errors_on_their_fields(signed_in, switch_on):
    human, fly = taxon_of(9606), taxon_of(7227)
    evidence_code = TermFactory(curie="ECO:0000353", label="physical interaction")
    page = signed_in.get(url("add"))
    response = signed_in.post(
        url("add"),
        submitted(
            page,
            slug="facets",
            title="Facets",
            kind=Kind.METADATA_ONLY,
            taxa=[human.pk],
            primary_taxon=fly.pk,
            assays=[evidence_code.pk],
        ),
    )
    assert response.status_code == 200
    found = errors(response)
    assert found["primary_taxon"] == [rules.PRIMARY_TAXON_OUTSIDE_TAXA]
    assert found["assays"] == [rules.ASSAY_NOT_PSI_MI]
    assert not BrowserInstance.objects.exists()


def test_tags_are_checkboxes_and_an_emptied_presentation_saves_none(
    signed_in, switch_on
):
    # A new instance starts with none of either, as the model's defaults.
    form = signed_in.get(url("add")).context["adminform"].form
    assert (form["tags"].value(), form["presentation"].value()) == ([], "{}")
    instance = make(kind=Kind.METADATA_ONLY, presentation={"version": 1})
    page = signed_in.get(url("change", instance.pk))
    form = page.context["adminform"].form
    assert isinstance(form.fields["tags"].widget, CheckboxSelectMultiple)
    for tag in rules.TAGS:
        assert f'type="checkbox" name="tags" value="{tag}"'.encode() in page.content
    response = signed_in.post(
        url("change", instance.pk),
        submitted(
            page, tags=["gene_regulation", "protein_interaction"], presentation=""
        ),
    )
    assert response.status_code == 302, errors(response)
    row = stored(instance)
    assert (row["tags"], row["presentation"]) == (
        ["gene_regulation", "protein_interaction"],
        {},
    )
    page = signed_in.get(url("change", instance.pk))
    response = signed_in.post(url("change", instance.pk), submitted(page, tags=["x"]))
    assert response.status_code == 200
    assert errors(response)["tags"] == [
        "Select a valid choice. x is not one of the available choices."
    ]
    # No box ticked: no tags.
    response = signed_in.post(url("change", instance.pk), submitted(page, tags=[]))
    assert response.status_code == 302, errors(response)
    assert stored(instance)["tags"] == []


def test_the_pickers_keep_the_admin_s_widgets_with_their_safe_fields(
    signed_in, switch_on
):
    page = signed_in.get(url("add"))
    fields = page.context["adminform"].form.fields
    for name, field_class, widget in (
        ("taxa", SafeModelMultipleChoiceField, AutocompleteSelectMultiple),
        ("primary_taxon", SafeModelChoiceField, AutocompleteSelect),
        ("assays", SafeModelMultipleChoiceField, AutocompleteSelectMultiple),
        ("relationship_types", SafeModelMultipleChoiceField, FilteredSelectMultiple),
        ("entity_types", SafeModelMultipleChoiceField, FilteredSelectMultiple),
    ):
        field = fields[name]
        assert type(field) is field_class, name
        assert isinstance(field.widget, RelatedFieldWidgetWrapper), name
        assert type(field.widget.widget) is widget, name
    assert type(fields["presentation"]) is PresentationField
    publication = inlines(page)["citations"].formset.empty_form.fields["publication"]
    assert type(publication) is SafeModelChoiceField
    assert type(publication.widget.widget) is AutocompleteSelect


@pytest.mark.parametrize(
    "model_name, field_name, needed",
    [
        ("browserinstance", "taxa", "graph.view_taxon"),
        ("browserinstance", "primary_taxon", "graph.view_taxon"),
        ("browserinstance", "assays", "graph.view_term"),
        ("browserinstancepublication", "publication", "graph.view_publication"),
    ],
)
def test_each_picker_s_search_asks_its_registry_s_view_permission(
    client, switch_on, model_name, field_name, needed
):
    query = {
        "app_label": "browsers",
        "model_name": model_name,
        "field_name": field_name,
        "term": "",
    }
    editor = account("browsers.add_browserinstance", "browsers.change_browserinstance")
    client.force_login(editor)
    search = reverse("admin:autocomplete")
    assert client.get(search, query).status_code == 403
    editor.user_permissions.add(permission(needed))
    assert client.get(search, query).status_code == 200


def test_the_publication_search_finds_a_paper(signed_in, switch_on):
    paper = PublicationFactory(title="A findable paper")
    PublicationFactory(title="Another")
    response = signed_in.get(
        reverse("admin:autocomplete"),
        {
            "app_label": "browsers",
            "model_name": "browserinstancepublication",
            "field_name": "publication",
            "term": "findable",
        },
    )
    assert response.status_code == 200
    assert [result["id"] for result in response.json()["results"]] == [str(paper.pk)]


def instance_search(model_name, term) -> dict:
    return {
        "app_label": "browsers",
        "model_name": model_name,
        "field_name": "instance",
        "term": term,
    }


@pytest.mark.parametrize(
    "model_name", ["browserinstancerelease", "browserinstancepublication"]
)
@pytest.mark.parametrize("term", ["\x00", "an\x00"], ids=["NUL", "NUL after text"])
def test_a_search_for_an_instance_finds_nothing_for_a_nul(
    signed_in, switch_on, model_name, term
):
    make()
    response = signed_in.get(
        reverse("admin:autocomplete"), instance_search(model_name, term)
    )
    assert response.status_code == 200
    assert response.json() == {"results": [], "pagination": {"more": False}}


def test_a_search_for_an_instance_finds_it(signed_in, switch_on):
    instance = make()
    response = signed_in.get(
        reverse("admin:autocomplete"), instance_search("browserinstancerelease", "an-")
    )
    assert response.status_code == 200
    found = response.json()["results"]
    assert [result["id"] for result in found] == [str(instance.pk)]


# ── Crafted values ──────────────────────────────────────────────────────────

CRAFTED = [
    ({"assays": ["MI:0018\x00"]}, "assays", "“MI:0018\x00” is not a valid value."),
    (
        {"relationship_types": ["encodes\x00"]},
        "relationship_types",
        "“encodes\x00” is not a valid value.",
    ),
    ({"taxa": ["x"]}, "taxa", "“x” is not a valid value."),
    ({"taxa": ["9" * 20]}, "taxa", f"“{'9' * 20}” is not a valid value."),
    ({"primary_taxon": "x"}, "primary_taxon", INVALID_CHOICE),
    ({"presentation": "[" * 100_000 + "]" * 100_000}, "presentation", TOO_LONG),
    # Within the cap, and readable: the schema refuses it.
    ({"presentation": "[" * 4_000 + "]" * 4_000}, "presentation", NOT_OBJECT),
    (
        {"presentation": "1" * 5_000},
        "presentation",
        "presentation is not valid JSON.",
    ),
    (
        {"presentation": '{"version": 1, "default_layers": ["a\\u0000"]}'},
        "presentation",
        "presentation.default_layers names 'a\x00', which is not one of its values.",
    ),
    (
        {"presentation": '{"\\ud800": 1}'},
        "presentation",
        "presentation is not valid JSON.",
    ),
]
CRAFTED_IDS = [
    "assays NUL",
    "relationship_types NUL",
    "taxa x",
    "taxa past 64 bits",
    "primary_taxon x",
    "presentation 100000 deep",
    "presentation 4000 deep",
    "presentation 5000 digits",
    "presentation NUL layer",
    "presentation lone surrogate",
]


@pytest.mark.parametrize("changes, field, message", CRAFTED, ids=CRAFTED_IDS)
@pytest.mark.parametrize("view", ["add", "change"])
def test_a_crafted_value_is_a_form_error_on_the_rendered_page(
    signed_in, switch_on, view, changes, field, message
):
    instance = make(kind=Kind.METADATA_ONLY, title="Unchanged")
    before = stored(instance)
    if view == "add":
        address = url("add")
        changes = {
            "slug": "crafted",
            "title": "T",
            "kind": Kind.METADATA_ONLY,
            **changes,
        }
    else:
        address = url("change", instance.pk)
    page = signed_in.get(address)
    response = signed_in.post(address, submitted(page, **changes))
    assert response.status_code == 200
    assert errors(response)[field] == [message]
    assert escape(message) in response.content.decode()
    assert stored(instance) == before
    assert BrowserInstance.objects.count() == 1


@pytest.mark.parametrize("view", ["add", "change"])
def test_valid_json_over_the_cap_is_refused_and_shown_as_typed(
    signed_in, switch_on, view
):
    instance = make(kind=Kind.METADATA_ONLY, title="Unchanged")
    before = stored(instance)
    typed = '{"version":   1, "x": "' + "a" * 9_000 + '"}'
    if view == "add":
        address = url("add")
        changes = {"slug": "over-cap", "title": "T", "kind": Kind.METADATA_ONLY}
    else:
        address = url("change", instance.pk)
        changes = {}
    page = signed_in.get(address)
    response = signed_in.post(address, submitted(page, presentation=typed, **changes))
    assert response.status_code == 200
    assert errors(response)["presentation"] == [TOO_LONG]
    assert escape(typed) in response.content.decode()
    assert stored(instance) == before
    assert BrowserInstance.objects.count() == 1


@pytest.mark.parametrize("view", ["add", "change"])
def test_a_crafted_citation_is_a_form_error_on_the_rendered_page(
    signed_in, switch_on, view
):
    instance = make(kind=Kind.METADATA_ONLY)
    address = url("add") if view == "add" else url("change", instance.pk)
    extra = {"slug": "crafted", "title": "T", "kind": Kind.METADATA_ONLY}
    page = signed_in.get(address)
    response = signed_in.post(
        address,
        submitted(
            page,
            **(extra if view == "add" else {}),
            **{
                "citations-TOTAL_FORMS": 1,
                "citations-0-publication": "x",
                "citations-0-role": "primary",
                "citations-0-order": 0,
            },
        ),
    )
    assert response.status_code == 200
    [form_errors] = inlines(response)["citations"].formset.errors
    assert form_errors["publication"] == [INVALID_CHOICE]
    assert escape(INVALID_CHOICE) in response.content.decode()
    assert not BrowserInstancePublication.objects.exists()


@pytest.mark.parametrize(
    "query",
    [
        "taxa=x",
        "taxa=%00",
        "assays=MI:0018%00",
        "assays=x%00y",
        "primary_taxon=x",
        "primary_taxon=%00",
    ],
)
def test_a_crafted_query_string_leaves_the_pickers_of_the_add_page_empty(
    signed_in, switch_on, query
):
    # The add page prefills its form from the query string, which no form
    # validates, and an autocomplete widget filters by the values it renders.
    response = signed_in.get(f"{url('add')}?{query}")
    assert response.status_code == 200
    form = response.context["adminform"].form
    assert not any(form[name].value() for name in ("taxa", "primary_taxon", "assays"))


def test_a_query_string_naming_rows_still_prefills_the_add_page(signed_in, switch_on):
    taxon, term = taxon_of(9606), TermFactory(curie="MI:0018")
    response = signed_in.get(
        f"{url('add')}?taxa={taxon.pk}&primary_taxon={taxon.pk}&assays={term.pk}"
    )
    assert response.status_code == 200
    content = response.content.decode()
    assert content.count(f'<option value="{taxon.pk}" selected>') == 2
    assert f'<option value="{term.pk}" selected>' in content


# ── The list ────────────────────────────────────────────────────────────────


@pytest.mark.postgres
@pytest.mark.parametrize(
    "query",
    [
        "kind=%00",
        "kind__exact=%00",
        "status__exact=%00",
        "visibility__exact=%00",
        "visibility__exact=a%00b",
        "title=%00",
        "slug=%00",
        "slug__icontains=%00",
        "title__contains=%00",
        "tags=%00",
        "presentation=%00",
        "title__regex=(",
        "tags__2147483648=1",
        "presentation__2147483648=1",
    ],
)
def test_a_lookup_the_database_refuses_is_django_s_redirect_for_a_wrong_one(
    signed_in, switch_on, query
):
    make()
    # A statement PostgreSQL refuses aborts the transaction this test runs in.
    with transaction.atomic():
        response = signed_in.get(f"{url('changelist')}?{query}")
        transaction.set_rollback(True)
    assert response.status_code == 302
    assert response["Location"] == f"{url('changelist')}?e=1"
    assert signed_in.get(response["Location"]).status_code == 200


@pytest.mark.parametrize(
    "query",
    [
        "display_order__range=1",
        "slug__range=x",
        "title__range=a",
        "title__range=abc",
        "slug__range=abc",
        "created_at__year=99999999999",
        "created_at__year=2147483648",
        "created_at__year=9223372036854775808",
        pytest.param(f"presentation{'__a' * 400}=x", id="a JSON path 400 keys deep"),
    ],
)
def test_a_lookup_that_cannot_be_compiled_is_django_s_redirect_for_a_wrong_one(
    signed_in, switch_on, query
):
    make()
    response = signed_in.get(f"{url('changelist')}?{query}")
    assert response.status_code == 302
    assert response["Location"] == f"{url('changelist')}?e=1"
    assert signed_in.get(response["Location"]).status_code == 200


@pytest.mark.skipif(
    connection.vendor != "sqlite",
    reason="SQLite's own refusals: PostgreSQL's are above",
)
@pytest.mark.parametrize(
    "query",
    ["title__regex=(", "tags__contains=x", "tags__has_keys=", "pk__contained_by=1"],
)
def test_a_lookup_the_sqlite_fallback_refuses_is_the_same_redirect(
    signed_in, switch_on, query
):
    make()
    response = signed_in.get(f"{url('changelist')}?{query}")
    assert response.status_code == 302
    assert response["Location"] == f"{url('changelist')}?e=1"
    assert signed_in.get(response["Location"]).status_code == 200


@pytest.mark.skipif(
    connection.vendor != "sqlite",
    reason="SQLite runs a regular expression row by row",
)
def test_a_regular_expression_only_the_list_reaches_is_the_same_redirect(
    signed_in, switch_on
):
    make()
    response = signed_in.get(
        f"{url('changelist')}?kind__exact=nosuch&slug__iregex=(&o=1"
    )
    assert response.status_code == 302
    assert response["Location"] == f"{url('changelist')}?e=1"


@pytest.mark.skipif(
    connection.vendor != "sqlite",
    reason="SQLite runs a regular expression row by row",
)
def test_a_regular_expression_no_statement_reaches_lists_nothing(signed_in, switch_on):
    make()
    response = signed_in.get(
        f"{url('changelist')}?kind__exact=nosuch&title__regex=(&_facets=True"
    )
    assert response.status_code == 200
    assert response.context["cl"].result_count == 0


def deep_lookup(keys) -> str:
    return f"presentation{'__a' * keys}=x"


@pytest.mark.parametrize("facets", ["", "&_facets=True"], ids=["plain", "facets"])
@pytest.mark.parametrize("keys", [33, 295, 300, 305, 310, 315, 5000])
def test_a_lookup_path_past_the_cap_is_refused_before_it_is_compiled(
    signed_in, switch_on, keys, facets
):
    make()
    response = signed_in.get(f"{url('changelist')}?{deep_lookup(keys)}{facets}")
    assert response.status_code == 302
    assert response["Location"] == f"{url('changelist')}?e=1"
    assert signed_in.get(response["Location"]).status_code == 200


@pytest.mark.parametrize("keys", [33, 295, 300, 305, 310, 315])
def test_an_action_posted_under_a_path_past_the_cap_deletes_nothing(
    signed_in, switch_on, keys
):
    instance = make()
    response = signed_in.post(
        f"{url('changelist')}?{deep_lookup(keys)}",
        {"action": "delete_selected", "_selected_action": instance.pk, "post": "yes"},
    )
    assert response.status_code == 302
    assert response["Location"] == f"{url('changelist')}?e=1"
    assert BrowserInstance.objects.filter(pk=instance.pk).exists()


def test_a_lookup_path_at_the_cap_still_runs(signed_in, switch_on):
    make()
    response = signed_in.get(f"{url('changelist')}?{deep_lookup(32)}")
    assert response.status_code == 200
    assert response.context["cl"].result_count == 0


def test_a_short_json_lookup_still_filters(signed_in, switch_on):
    make(presentation={"version": 1, "accent": "blue"})
    make(slug="another", presentation={"version": 1, "accent": "red"})
    response = signed_in.get(url("changelist"), {"presentation__accent": "blue"})
    assert response.status_code == 200
    assert response.context["cl"].result_count == 1


def test_the_list_offers_no_facet_counts(signed_in, switch_on):
    make()
    response = signed_in.get(url("changelist"), {"_facets": "True"})
    assert response.status_code == 200
    assert response.context["cl"].add_facets is False


@pytest.mark.postgres
def test_a_database_that_fails_is_still_an_error(signed_in, switch_on, monkeypatch):
    make()

    def lost(self, request):
        raise OperationalError("server closed the connection unexpectedly")

    monkeypatch.setattr(ChangeList, "get_results", lost)
    with pytest.raises(OperationalError):
        signed_in.get(url("changelist"))


def test_an_action_posted_under_a_lookup_that_cannot_be_compiled_deletes_nothing(
    signed_in, switch_on
):
    instance = make()
    response = signed_in.post(
        f"{url('changelist')}?display_order__range=1",
        {"action": "delete_selected", "_selected_action": instance.pk, "post": "yes"},
    )
    assert response.status_code == 302
    assert response["Location"] == f"{url('changelist')}?e=1"
    assert BrowserInstance.objects.filter(pk=instance.pk).exists()


@pytest.mark.postgres
def test_an_action_posted_under_a_refused_lookup_deletes_nothing(signed_in, switch_on):
    instance = make()
    response = signed_in.post(
        f"{url('changelist')}?kind__exact=%00",
        {"action": "delete_selected", "_selected_action": instance.pk, "post": "yes"},
    )
    assert response.status_code == 302
    assert response["Location"] == f"{url('changelist')}?e=1"
    assert BrowserInstance.objects.filter(pk=instance.pk).exists()


def test_a_refused_lookup_is_still_refused_while_the_switch_is_off(signed_in, settings):
    settings.TYPED_GRAPH_ENABLED = False
    assert signed_in.get(f"{url('changelist')}?kind__exact=%00").status_code == 403


def test_a_lookup_the_database_takes_still_filters(signed_in, switch_on):
    make(slug="typed-one")
    make(slug="a-card", kind=Kind.METADATA_ONLY, display_order=5)
    for query in (
        {"kind__exact": Kind.TYPED},
        {"slug__regex": "^typed"},
        # Two characters: Django reads a __range value as its two bounds.
        {"display_order__range": "01"},
    ):
        response = signed_in.get(url("changelist"), query)
        assert response.status_code == 200
        assert response.context["cl"].result_count == 1


@pytest.mark.parametrize(
    "value",
    ["x", "", " ", "1.5", "\x00", "-1", "9" * 23],
    ids=["a word", "empty", "a space", "a decimal", "NUL", "negative", "past 64 bits"],
)
@pytest.mark.parametrize(
    "posted", [{}, {"index": "0"}], ids=["confirming", "from the list"]
)
def test_a_selected_id_no_row_could_have_stops_the_action(
    signed_in, switch_on, posted, value
):
    instance = make()
    response = signed_in.post(
        url("changelist"),
        {"action": "delete_selected", "_selected_action": value, **posted},
        follow=True,
    )
    assert response.redirect_chain == [(url("changelist"), 302)]
    assert NOTHING_SELECTED in response.content.decode()
    assert BrowserInstance.objects.filter(pk=instance.pk).exists()


def test_one_id_no_row_could_have_stops_the_whole_action(signed_in, switch_on):
    first, second = make(slug="first"), make(slug="second")
    response = signed_in.post(
        url("changelist"),
        {
            "action": "delete_selected",
            "_selected_action": [first.pk, second.pk, "x"],
            "post": "yes",
        },
    )
    assert response.status_code == 302
    assert BrowserInstance.objects.count() == 2


def test_the_delete_action_deletes_the_rows_it_names(signed_in, switch_on):
    doomed, kept = make(slug="doomed"), make(slug="kept")
    action = {"action": "delete_selected", "_selected_action": doomed.pk}
    assert signed_in.post(url("changelist"), action).status_code == 200
    response = signed_in.post(url("changelist"), {**action, "post": "yes"})
    assert response.status_code == 302
    assert [row.pk for row in BrowserInstance.objects.all()] == [kept.pk]


# ── The two inlines ─────────────────────────────────────────────────────────


def test_an_editor_who_may_change_instances_edits_both_inlines(client, switch_on):
    first, second, third = (DatasetReleaseFactory() for _ in range(3))
    paper = PublicationFactory()
    instance = make(status=Status.AVAILABLE)
    BrowserInstanceRelease.objects.create(instance=instance, release=first)
    client.force_login(
        account("browsers.view_browserinstance", "browsers.change_browserinstance")
    )
    address = url("change", instance.pk)
    page = client.get(address)
    shown = inlines(page)
    assert set(shown) == {"citations", "selection"}
    for inline in shown.values():
        assert inline.has_add_permission and inline.has_change_permission
        assert inline.has_delete_permission and inline.has_view_permission
    forms = shown["selection"].formset.initial_forms
    assert [form.instance.release for form in forms] == [first]

    def post(**changes):
        return client.post(address, submitted(client.get(address), **changes))

    response = post(
        **{"selection-TOTAL_FORMS": 2},
        **row(1, second, Role.CONTEXT, 1),
        **{
            "citations-TOTAL_FORMS": 1,
            "citations-0-publication": paper.pk,
            "citations-0-role": "primary",
            "citations-0-order": 0,
        },
    )
    assert response.status_code == 302, errors(response)
    assert selected(instance) == [(first.pk, "primary"), (second.pk, "context")]
    assert [row.publication for row in instance.citations.all()] == [paper]
    response = post(**{"selection-0-release": third.pk})
    assert response.status_code == 302, errors(response)
    assert selected(instance) == [(third.pk, "primary"), (second.pk, "context")]
    response = post(**{"selection-1-DELETE": True, "citations-0-DELETE": True})
    assert response.status_code == 302, errors(response)
    assert selected(instance) == [(third.pk, "primary")]
    assert not instance.citations.exists()


def test_an_editor_with_only_the_change_permission_sees_the_existing_rows(
    client, switch_on
):
    release, paper = DatasetReleaseFactory(), PublicationFactory()
    instance = make(status=Status.AVAILABLE)
    BrowserInstanceRelease.objects.create(instance=instance, release=release)
    BrowserInstancePublication.objects.create(instance=instance, publication=paper)
    client.force_login(account("browsers.change_browserinstance"))
    shown = inlines(client.get(url("change", instance.pk)))
    forms = shown["selection"].formset.initial_forms
    assert [form.instance.release for form in forms] == [release]
    forms = shown["citations"].formset.initial_forms
    assert [form.instance.publication for form in forms] == [paper]


def test_an_editor_who_may_only_add_instances_adds_one_with_both_inlines(
    client, switch_on
):
    release = DatasetReleaseFactory()
    paper = PublicationFactory()
    client.force_login(account("browsers.add_browserinstance"))
    page = client.get(url("add"))
    shown = inlines(page)
    assert set(shown) == {"citations", "selection"}
    for inline in shown.values():
        assert inline.has_add_permission and inline.has_change_permission
        assert inline.has_delete_permission and inline.has_view_permission
    response = client.post(
        url("add"),
        submitted(
            page,
            slug="added",
            title="Added",
            kind=Kind.TYPED,
            status=Status.AVAILABLE,
            **{"selection-TOTAL_FORMS": 1},
            **row(0, release),
            **{
                "citations-TOTAL_FORMS": 1,
                "citations-0-publication": paper.pk,
                "citations-0-role": "describes",
                "citations-0-order": 0,
            },
        ),
    )
    assert response.status_code == 302, errors(response)
    instance = BrowserInstance.objects.get(slug="added")
    assert selected(instance) == [(release.pk, "primary")]
    assert list(instance.citations.values_list("publication", "role")) == [
        (paper.pk, "describes")
    ]
    # Without a release, an available typed instance is refused there too.
    response = client.post(
        url("add"),
        submitted(
            page, slug="bare", title="Bare", kind=Kind.TYPED, status=Status.AVAILABLE
        ),
    )
    assert response.status_code == 200
    assert selection_errors(response) == [rules.AVAILABLE_TYPED_NEEDS_RELEASE]
    assert not BrowserInstance.objects.filter(slug="bare").exists()


def test_one_post_turns_a_typed_instance_external_and_drops_its_row(client, switch_on):
    release = DatasetReleaseFactory()
    instance = make(status=Status.AVAILABLE)
    BrowserInstanceRelease.objects.create(instance=instance, release=release)
    client.force_login(
        account("browsers.view_browserinstance", "browsers.change_browserinstance")
    )
    address = url("change", instance.pk)
    page = client.get(address)
    external = {"kind": Kind.EXTERNAL, "external_url": "https://portal.example/"}

    response = client.post(address, submitted(page, **external))
    assert response.status_code == 200
    assert selection_errors(response) == [rules.ONLY_TYPED_SELECTS]
    assert (stored(instance)["kind"], selected(instance)) == (
        "typed",
        [(release.pk, "primary")],
    )

    response = client.post(
        address, submitted(page, **external, **{"selection-0-DELETE": True})
    )
    assert response.status_code == 302, errors(response)
    assert (stored(instance)["kind"], selected(instance)) == ("external", [])


def test_deleting_the_only_row_of_an_available_typed_instance_is_refused(
    signed_in, switch_on
):
    release = DatasetReleaseFactory()
    instance = make(status=Status.AVAILABLE)
    BrowserInstanceRelease.objects.create(instance=instance, release=release)
    page = signed_in.get(url("change", instance.pk))
    response = signed_in.post(
        url("change", instance.pk), submitted(page, **{"selection-0-DELETE": True})
    )
    assert response.status_code == 200
    assert selection_errors(response) == [rules.AVAILABLE_TYPED_NEEDS_RELEASE]
    assert selected(instance) == [(release.pk, "primary")]
    # Planned, it may select none.
    response = signed_in.post(
        url("change", instance.pk),
        submitted(page, status=Status.PLANNED, **{"selection-0-DELETE": True}),
    )
    assert response.status_code == 302, errors(response)
    assert selected(instance) == []


def test_a_stored_row_the_post_does_not_name_still_counts(signed_in, switch_on):
    # A page opened before the rows were added, or a crafted POST, leaves
    # them selected, so the rules judge them with the rows the POST holds.
    releases = [DatasetReleaseFactory() for _ in range(rules.MAX_INSTANCE_RELEASES + 1)]
    instance = make(status=Status.PLANNED)
    address = url("change", instance.pk)
    stale = signed_in.get(address)
    BrowserInstanceRelease.objects.bulk_create(
        BrowserInstanceRelease(instance=instance, release=release)
        for release in releases[:-1]
    )
    response = signed_in.post(
        address,
        submitted(stale, **{"selection-TOTAL_FORMS": 1}, **row(0, releases[-1])),
    )
    assert response.status_code == 200
    assert selection_errors(response) == [rules.TOO_MANY_RELEASES]
    response = signed_in.post(
        address,
        submitted(stale, kind=Kind.EXTERNAL, external_url="https://portal.example/"),
    )
    assert response.status_code == 200
    assert selection_errors(response) == [rules.ONLY_TYPED_SELECTS]
    assert stored(instance)["kind"] == "typed"
    assert instance.selection.count() == rules.MAX_INSTANCE_RELEASES


def test_a_second_portal_ppi_entry_is_refused(signed_in, switch_on):
    make(slug="protein-interactions", kind=Kind.PORTAL_PPI)
    page = signed_in.get(url("add"))
    response = signed_in.post(
        url("add"), submitted(page, slug="another", title="T", kind=Kind.PORTAL_PPI)
    )
    assert response.status_code == 200
    assert selection_errors(response) == [
        "This portal already has a portal_ppi entry, protein-interactions."
    ]


def test_the_add_page_with_a_row_and_an_invalid_form_shows_its_errors(
    signed_in, switch_on
):
    release = DatasetReleaseFactory()
    page = signed_in.get(url("add"))
    response = signed_in.post(
        url("add"),
        submitted(
            page,
            slug="Not A Slug",
            title="",
            kind=Kind.TYPED,
            status=Status.AVAILABLE,
            **{"selection-TOTAL_FORMS": 1},
            **row(0, release),
        ),
    )
    assert response.status_code == 200
    found = errors(response)
    assert set(found) >= {"slug", "title"}
    forms = inlines(response)["selection"].formset.forms
    assert [form["release"].value() for form in forms] == [str(release.pk)]
    assert not BrowserInstance.objects.exists()


def test_the_release_inline_offers_releases_by_dataset_and_version(
    signed_in, switch_on
):
    release = DatasetReleaseFactory(version="v7")
    page = signed_in.get(url("add"))
    field = inlines(page)["selection"].formset.empty_form.fields["release"]
    label = f"{release.dataset.slug}/v7 (draft)"
    assert field.label_from_instance(release) == label
    assert label.encode() in page.content


def test_the_citation_inline_orders_the_papers(signed_in, switch_on):
    instance = make(kind=Kind.METADATA_ONLY)
    later, sooner = PublicationFactory(), PublicationFactory()
    BrowserInstancePublication.objects.create(
        instance=instance, publication=later, order=1
    )
    BrowserInstancePublication.objects.create(
        instance=instance, publication=sooner, order=0
    )
    page = signed_in.get(url("change", instance.pk))
    forms = inlines(page)["citations"].formset.initial_forms
    assert [form.instance.publication for form in forms] == [sooner, later]
    response = signed_in.post(
        url("change", instance.pk), submitted(page, **{"citations-0-order": 2})
    )
    assert response.status_code == 302, errors(response)
    assert [row.publication for row in instance.citations.all()] == [later, sooner]

"""Browser instances' records (browsers.models): the database constraints,
which hold against a bare save(); what deletes are refused; every field
validator, clean() rule, presentation rule and cross-row rule, each with its
sentence; and which instances a caller may list, reach and pin
(browsers.selection)."""

import unicodedata

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.core.validators import validate_slug as django_validate_slug
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext

from browsers import presentation, rules
from browsers.models import (
    BrowserInstance,
    BrowserInstancePublication,
    BrowserInstanceRelease,
    Kind,
    Role,
    Status,
    Visibility,
)
from browsers.presentation import validate_presentation
from browsers.rules import validate_facets, validate_selection
from browsers.selection import EMPTY_PIN, instance_releases, listable, reachable
from graph.models import (
    DatasetRelease,
    ImmutableRecordError,
    Publication,
    Taxon,
)
from graph.tests.factories import (
    DatasetReleaseFactory,
    PublicationFactory,
    RelationshipTypeFactory,
    StaffUserFactory,
    TermFactory,
    taxon_of,
)
from graph.tests.test_provenance import PUBLISHED, advance

pytestmark = pytest.mark.django_db

DJANGO_SLUG = django_validate_slug.message
TOO_LONG_SLUG = "Ensure this value has at most 50 characters (it has 51)."


def make(**fields) -> BrowserInstance:
    """An instance written with a bare save(), past every validator."""
    return BrowserInstance.objects.create(
        **{"slug": "an-instance", "title": "An instance", "kind": Kind.TYPED, **fields}
    )


def problems(**fields) -> dict:
    """What full_clean() says of an unsaved instance with these fields, by
    field; {} when it is valid."""
    instance = BrowserInstance(
        **{"slug": "an-instance", "title": "An instance", "kind": Kind.TYPED, **fields}
    )
    try:
        instance.full_clean()
    except ValidationError as error:
        return error.message_dict
    return {}


def refused(save) -> str | None:
    """The constraint that refuses save(): its name on PostgreSQL. SQLite's
    messages name no unique constraint, so there only the refusal is
    checked."""
    with pytest.raises(IntegrityError) as raised:
        with transaction.atomic():
            save()
    if connection.vendor != "postgresql":
        return None
    return raised.value.__cause__.diag.constraint_name


def named(constraint) -> str | None:
    return constraint if connection.vendor == "postgresql" else None


# ── The schema ──────────────────────────────────────────────────────────────


def test_every_choice_states_its_label():
    # A later change to a value or a label is a migration the isolation
    # tests refuse, so all are fixed now.
    assert Kind.choices == [
        ("typed", "Typed data"),
        ("portal_ppi", "This portal's search"),
        ("external", "External portal"),
        ("metadata_only", "Citation card"),
    ]
    assert Status.choices == [
        ("planned", "Planned"),
        ("available", "Available"),
        ("retired", "Retired"),
    ]
    assert Visibility.choices == [
        ("staff", "Staff only"),
        ("unlisted", "Unlisted (reachable by slug)"),
        ("public", "Public (listed)"),
    ]
    assert Role.choices == [("primary", "Primary"), ("context", "Context")]


@pytest.mark.parametrize(
    "model", [BrowserInstance, BrowserInstanceRelease, BrowserInstancePublication]
)
def test_no_field_carries_help_text_or_a_verbose_name(model):
    # Operator help lives in the admin's form, which is code.
    for field in [*model._meta.local_fields, *model._meta.local_many_to_many]:
        assert field.help_text == "", field.name
        if not field.primary_key:
            assert field.verbose_name == field.name.replace("_", " "), field.name


def test_the_defaults_fail_closed():
    instance = BrowserInstance(slug="defaults", title="Defaults", kind=Kind.TYPED)
    assert (instance.status, instance.visibility) == (Status.PLANNED, Visibility.STAFF)
    assert (instance.tags, instance.presentation) == ([], {})
    assert (instance.is_synthetic, instance.allow_external_services) == (False, False)
    assert instance.display_order == 0


# ── The database constraints, against a bare save() ─────────────────────────


@pytest.mark.parametrize(
    "fields, constraint",
    [
        ({"kind": "hub"}, "browsers_instance_kind_valid"),
        ({"status": "hidden"}, "browsers_instance_status_valid"),
        ({"visibility": "everyone"}, "browsers_instance_visibility_valid"),
        ({"kind": Kind.EXTERNAL, "external_url": ""}, "browsers_instance_external_url"),
        (
            {"kind": Kind.EXTERNAL, "external_url": "http://example.org/"},
            "browsers_instance_external_url",
        ),
        (
            {"kind": Kind.TYPED, "external_url": "https://example.org/"},
            "browsers_instance_external_url",
        ),
        (
            {"kind": Kind.PORTAL_PPI, "external_url": "https://example.org/"},
            "browsers_instance_external_url",
        ),
        ({"slug": ""}, "browsers_instance_slug_nonempty"),
        ({"slug": "Upper"}, "browsers_instance_slug_lower"),
    ],
    ids=[
        "kind",
        "status",
        "visibility",
        "external without a URL",
        "external with http",
        "typed with a URL",
        "portal_ppi with a URL",
        "blank slug",
        "upper-case slug",
    ],
)
def test_an_instance_constraint_refuses_a_bare_save(fields, constraint):
    assert refused(lambda: make(**fields)) == named(constraint)
    assert not BrowserInstance.objects.exists()


def test_an_external_instance_saves_with_an_https_url_in_any_case():
    make(slug="lower", kind=Kind.EXTERNAL, external_url="https://example.org/")
    make(slug="upper", kind=Kind.EXTERNAL, external_url="HTTPS://EXAMPLE.ORG/")
    assert BrowserInstance.objects.count() == 2


def test_a_duplicate_slug_is_refused():
    make(slug="taken")
    assert refused(lambda: make(slug="taken")) == named(
        "browsers_browserinstance_slug_key"
    )


def test_a_non_ascii_upper_case_slug_is_refused_on_postgresql_only():
    # SQLite's LOWER is ASCII-only; validate_slug refuses it on every path
    # that validates.
    if connection.vendor == "postgresql":
        assert refused(lambda: make(slug="Äb")) == "browsers_instance_slug_lower"
    else:
        assert make(slug="Äb").pk
    assert problems(slug="Äb") == {"slug": [DJANGO_SLUG, rules.SLUG_GRAMMAR]}


def test_two_portal_ppi_entries_save_with_no_database_rule():
    # One entry per install is validate_selection's rule, so it can be
    # relaxed with no migration.
    make(slug="first-entry", kind=Kind.PORTAL_PPI)
    make(slug="second-entry", kind=Kind.PORTAL_PPI)
    assert BrowserInstance.objects.filter(kind=Kind.PORTAL_PPI).count() == 2


def test_a_selection_constraint_refuses_a_bare_save():
    instance = make()
    release = DatasetReleaseFactory()
    BrowserInstanceRelease.objects.create(instance=instance, release=release)
    assert refused(
        lambda: BrowserInstanceRelease.objects.create(
            instance=instance, release=release
        )
    ) == named("browsers_selection_unique")
    assert refused(
        lambda: BrowserInstanceRelease.objects.create(
            instance=instance, release=DatasetReleaseFactory(), role="supporting"
        )
    ) == named("browsers_selection_role_valid")
    assert instance.selection.count() == 1


def test_a_citation_constraint_refuses_a_bare_save():
    instance = make()
    paper = PublicationFactory()
    BrowserInstancePublication.objects.create(instance=instance, publication=paper)
    assert refused(
        lambda: BrowserInstancePublication.objects.create(
            instance=instance, publication=paper, role="describes"
        )
    ) == named("browsers_citation_unique")
    assert refused(
        lambda: BrowserInstancePublication.objects.create(
            instance=instance, publication=PublicationFactory(), role="author"
        )
    ) == named("browsers_citation_role_valid")
    assert instance.citations.count() == 1


def test_the_rows_keep_their_order_then_their_target_id():
    instance = make()
    first, second, third = (DatasetReleaseFactory() for _ in range(3))
    for release, order in ((third, 0), (second, 1), (first, 1)):
        BrowserInstanceRelease.objects.create(
            instance=instance, release=release, order=order
        )
    assert [row.release for row in instance.selection.all()] == [third, first, second]
    papers = [PublicationFactory() for _ in range(3)]
    for paper, order in zip(papers, (2, 0, 0)):
        BrowserInstancePublication.objects.create(
            instance=instance, publication=paper, order=order
        )
    assert [row.publication for row in instance.citations.all()] == [
        papers[1],
        papers[2],
        papers[0],
    ]


# ── Deletes ─────────────────────────────────────────────────────────────────


def test_a_selected_draft_release_a_cited_paper_and_the_primary_taxon_are_kept():
    release = DatasetReleaseFactory()
    paper = PublicationFactory()
    taxon = taxon_of(999_999_921)
    instance = make(primary_taxon=taxon)
    BrowserInstanceRelease.objects.create(instance=instance, release=release)
    BrowserInstancePublication.objects.create(instance=instance, publication=paper)
    for row in (release, paper, taxon):
        with pytest.raises(ProtectedError), transaction.atomic():
            row.delete()
    assert DatasetRelease.objects.filter(pk=release.pk).exists()
    assert Publication.objects.filter(pk=paper.pk).exists()
    assert Taxon.objects.filter(pk=taxon.pk).exists()


def test_a_selected_release_past_draft_is_refused_by_graph_s_rule_first():
    release = advance(DatasetReleaseFactory(), PUBLISHED)
    instance = make()
    BrowserInstanceRelease.objects.create(instance=instance, release=release)
    with pytest.raises(ImmutableRecordError, match="deleted only while it is a draft"):
        with transaction.atomic():
            release.delete()
    assert instance.selection.get().release == release


def test_deleting_an_instance_removes_its_rows_and_keeps_their_targets():
    release = DatasetReleaseFactory()
    paper = PublicationFactory()
    taxon = taxon_of(999_999_922)
    instance = make()
    instance.taxa.add(taxon)
    BrowserInstanceRelease.objects.create(instance=instance, release=release)
    BrowserInstancePublication.objects.create(instance=instance, publication=paper)
    instance.delete()
    assert not BrowserInstanceRelease.objects.exists()
    assert not BrowserInstancePublication.objects.exists()
    assert not BrowserInstance.taxa.through.objects.exists()
    assert DatasetRelease.objects.filter(pk=release.pk).exists()
    assert Publication.objects.filter(pk=paper.pk).exists()


# ── The slug ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("slug", ["synthetic-regulation", "a", "a1-b2-c3", "x" * 50])
def test_a_well_formed_slug_is_valid(slug):
    assert problems(slug=slug) == {}


@pytest.mark.parametrize("slug", ["Synthetic", "a--b", "-a", "a-", "a_b"])
def test_a_malformed_slug_gets_the_grammar_sentence(slug):
    assert problems(slug=slug) == {"slug": [rules.SLUG_GRAMMAR]}


@pytest.mark.parametrize("slug", ["a b", "ab.c", "a\x00b"])
def test_a_slug_django_refuses_gets_django_s_sentence_then_the_grammar(slug):
    # SlugField runs Django's own slug validator before the field's.
    assert problems(slug=slug) == {"slug": [DJANGO_SLUG, rules.SLUG_GRAMMAR]}


def test_a_51_character_slug_gets_the_grammar_then_django_s_length_sentence():
    # CharField appends its length validator after the field's own.
    assert problems(slug="a" * 51) == {"slug": [rules.SLUG_GRAMMAR, TOO_LONG_SLUG]}


@pytest.mark.parametrize("word", rules.RESERVED_SLUGS)
def test_a_reserved_word_is_refused(word):
    assert problems(slug=word) == {"slug": [f"'{word}' is reserved."]}


def test_the_reserved_words_grow_with_no_migration(monkeypatch):
    # The migration names the validator, which reads the list when it runs.
    assert problems(slug="gallery") == {}
    monkeypatch.setattr(rules, "RESERVED_SLUGS", (*rules.RESERVED_SLUGS, "gallery"))
    assert problems(slug="gallery") == {"slug": ["'gallery' is reserved."]}


# ── Plain text ──────────────────────────────────────────────────────────────

LINE_FIELDS = ("title", "short_title", "summary")
BLOCK_FIELDS = ("description", "methods_text")


@pytest.mark.parametrize("field", LINE_FIELDS)
@pytest.mark.parametrize(
    "character, sentence",
    [
        ("\x00", rules.CONTROL_CHARACTER),
        ("\x1b", rules.CONTROL_CHARACTER),
        ("\x85", rules.CONTROL_CHARACTER),
        ("\t", rules.CONTROL_CHARACTER),
        ("\n", rules.CONTROL_CHARACTER),
        ("\u202e", rules.REORDERING_CHARACTER),
        ("\u2066", rules.REORDERING_CHARACTER),
        ("\u2028", rules.REORDERING_CHARACTER),
        ("\u2029", rules.REORDERING_CHARACTER),
    ],
    ids=["NUL", "ESC", "NEL", "tab", "newline", "RLO", "LRI", "LSEP", "PSEP"],
)
def test_a_line_refuses_control_reordering_and_breaking_characters(
    field, character, sentence
):
    assert problems(**{field: f"a{character}b"}) == {field: [sentence]}


@pytest.mark.parametrize("field", LINE_FIELDS)
def test_a_line_with_both_kinds_of_character_gets_both_sentences(field):
    assert problems(**{field: "a\x1b\u202eb"}) == {
        field: [rules.CONTROL_CHARACTER, rules.REORDERING_CHARACTER]
    }


@pytest.mark.parametrize("field", BLOCK_FIELDS)
def test_a_block_keeps_tabs_and_line_breaks(field):
    text = "First line.\nSecond line,\r\n\tindented.\u2028A line separator."
    assert problems(**{field: text}) == {}


@pytest.mark.parametrize("field", BLOCK_FIELDS)
@pytest.mark.parametrize(
    "character, sentence",
    [
        ("\x00", rules.CONTROL_CHARACTER),
        ("\x1b", rules.CONTROL_CHARACTER),
        ("\x85", rules.CONTROL_CHARACTER),
        ("\x0b", rules.CONTROL_CHARACTER),
        ("\u202e", rules.REORDERING_CHARACTER),
        ("\u2066", rules.REORDERING_CHARACTER),
    ],
    ids=["NUL", "ESC", "NEL", "VT", "RLO", "LRI"],
)
def test_a_block_refuses_other_control_and_reordering_characters(
    field, character, sentence
):
    assert problems(**{field: f"a{character}b"}) == {field: [sentence]}


@pytest.mark.parametrize("field", BLOCK_FIELDS)
def test_a_block_holds_at_most_20000_characters(field):
    assert problems(**{field: "a" * rules.MAX_LONG_TEXT_CHARS}) == {}
    assert problems(**{field: "a" * (rules.MAX_LONG_TEXT_CHARS + 1)}) == {
        field: ["This text is at most 20,000 characters."]
    }


def test_markup_is_kept_as_typed():
    text = {
        "title": "<b>x</b> &amp; y",
        "short_title": "<i>x</i>",
        "summary": "<script>alert(1)</script> &lt;",
        "description": '<a href="https://example.org/">x</a>\n&amp;',
        "methods_text": "<p>x</p>",
    }
    assert problems(**text) == {}
    stored = BrowserInstance.objects.filter(pk=make(**text).pk).values(*text).get()
    assert stored == text


def test_the_control_classes_are_exactly_category_cc():
    every = "".join(map(chr, range(0x110000)))
    controls = {c for c in every if unicodedata.category(c) == "Cc"}
    assert set(rules.CONTROL_CHARACTERS.findall(every)) == controls
    assert set(rules.BLOCK_CONTROL_CHARACTERS.findall(every)) == controls - set(
        "\t\n\r"
    )


# ── The external URL ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "https://example.org/",
        "https://example.org/data?release=v2#top",
        "HTTPS://EXAMPLE.ORG/",
        "https://xn--exmple-cua.org/",
        "https://example.org:8443/",
    ],
)
def test_an_external_instance_with_an_https_url_is_valid(url):
    assert problems(kind=Kind.EXTERNAL, external_url=url) == {}


@pytest.mark.parametrize(
    "url, sentences",
    [
        ("http://example.org/", [rules.NOT_HTTPS]),
        ("ftp://example.org/", [rules.NOT_HTTPS]),
        # The field's own URLValidator speaks first.
        ("javascript:alert(1)", ["Enter a valid URL.", rules.NOT_HTTPS]),
        ("https://user:secret@example.org/", [rules.URL_CREDENTIALS]),
        ("https://trusted.example@evil.example/", [rules.URL_CREDENTIALS]),
        ("https://exämple.org/", [rules.URL_HOST_NOT_ASCII]),
        ("https://аpple.com/", [rules.URL_HOST_NOT_ASCII]),
        ("https://example.org/\u202egnp.exe", [rules.URL_CHARACTER]),
        ("https://example.org/a\u200bb", [rules.URL_CHARACTER]),
        ("https://example.org/a\x1bb", [rules.URL_CHARACTER]),
    ],
    ids=[
        "http",
        "ftp",
        "javascript",
        "credentials",
        "a user that reads as a host",
        "non-ASCII host",
        "Cyrillic look-alike",
        "RLO in the path",
        "zero-width space",
        "ESC in the path",
    ],
)
def test_an_external_url_that_misleads_is_refused(url, sentences):
    assert problems(kind=Kind.EXTERNAL, external_url=url) == {"external_url": sentences}


def test_an_external_instance_needs_its_url_and_no_other_kind_has_one():
    assert problems(kind=Kind.EXTERNAL) == {"external_url": [rules.EXTERNAL_NEEDS_URL]}
    for kind in (Kind.TYPED, Kind.PORTAL_PPI, Kind.METADATA_ONLY):
        assert problems(kind=kind, external_url="https://example.org/") == {
            "external_url": [rules.ONLY_EXTERNAL_HAS_URL]
        }, kind


# ── Tags ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("tags", [[], ["gene_regulation", "protein_interaction"]])
def test_distinct_known_tags_are_valid(tags):
    assert problems(tags=tags) == {}


@pytest.mark.parametrize(
    "tags, sentences",
    [
        ({}, [rules.TAGS_NOT_LIST]),
        ("", [rules.TAGS_NOT_LIST]),
        (5, [rules.TAGS_NOT_LIST]),
        ("x", [rules.TAGS_NOT_LIST]),
        ({"disease": True}, [rules.TAGS_NOT_LIST]),
        ([1], [rules.TAGS_NOT_NAMES]),
        ([{}], [rules.TAGS_NOT_NAMES]),
        ([[]], [rules.TAGS_NOT_NAMES]),
        ([None], [rules.TAGS_NOT_NAMES]),
        (["disease", True], [rules.TAGS_NOT_NAMES]),
        (["x", "disease", "x"], ["Unknown tag 'x'.", rules.REPEATED_TAG]),
        (["disease", "disease"], [rules.REPEATED_TAG]),
        ([*rules.TAGS, "disease"], [rules.REPEATED_TAG, rules.TOO_MANY_TAGS]),
    ],
    ids=[
        "{}",
        "''",
        "5",
        "'x'",
        "an object",
        "[1]",
        "[{}]",
        "[[]]",
        "[null]",
        "a boolean",
        "unknown",
        "duplicate",
        "eleven of ten",
    ],
)
def test_tags_are_refused_with_their_sentences(tags, sentences):
    assert problems(tags=tags) == {"tags": sentences}


def test_eleven_distinct_tags_are_too_many_once_the_list_grows(monkeypatch):
    # The list grows with no migration; the cap still holds.
    monkeypatch.setattr(rules, "TAGS", (*rules.TAGS, "new_area"))
    assert problems(tags=list(rules.TAGS)) == {"tags": [rules.TOO_MANY_TAGS]}


def test_an_emptied_tags_or_presentation_saves_as_none_of_each():
    instance = BrowserInstance(
        slug="emptied", title="Emptied", kind=Kind.TYPED, tags=None, presentation=None
    )
    instance.full_clean()
    instance.save()
    stored = BrowserInstance.objects.values("tags", "presentation").get()
    assert stored == {"tags": [], "presentation": {}}


@pytest.mark.parametrize("value", ["", [], [1], 5, "x"], ids=repr)
def test_a_presentation_that_is_not_an_object_is_refused_once(value):
    assert problems(presentation=value) == {"presentation": [presentation.NOT_OBJECT]}


# ── Presentation schema v1 ──────────────────────────────────────────────────


def presentation_problems(value) -> list:
    try:
        validate_presentation(value)
    except ValidationError as error:
        return error.messages
    return []


def test_every_default_and_version_1_alone_are_valid():
    assert presentation_problems({}) == []
    assert presentation_problems({"version": 1}) == []


@pytest.mark.parametrize("value", [None, [], "x", 5, [{"version": 1}]], ids=repr)
def test_presentation_is_an_object(value):
    assert presentation_problems(value) == [presentation.NOT_OBJECT]


@pytest.mark.parametrize(
    "value",
    [
        {"version": True},
        {"version": 1.0},
        {"version": 2},
        {"version": "1"},
        {"version": None},
        {"accent": "blue"},
    ],
    ids=["true", "1.0", "2", "'1'", "null", "absent beside another key"],
)
def test_version_is_the_integer_1(value):
    assert presentation_problems(value) == [presentation.NOT_VERSION_1]


def test_an_unknown_key_is_refused():
    assert presentation_problems({"version": 1, "colour": "red", "Accent": "blue"}) == [
        "presentation has no key 'colour'.",
        "presentation has no key 'Accent'.",
    ]


def test_every_problem_is_listed_in_key_order():
    assert presentation_problems(
        {"accent": "#ff0000", "layout": "spiral", "version": 2, "colour": 1}
    ) == [
        presentation.NOT_VERSION_1,
        "presentation has no key 'colour'.",
        "presentation.layout is one of: "
        "cose, cola, concentric, circle, grid, breadthfirst.",
        "presentation.accent is one of: blue, teal, green, amber, red, purple, grey.",
    ]


def test_each_closed_key_takes_each_of_its_values():
    for key, values in (
        ("layout", presentation.LAYOUTS),
        ("accent", presentation.ACCENTS),
    ):
        for value in values:
            assert presentation_problems({"version": 1, key: value}) == [], value
    assert (
        presentation_problems(
            {
                "version": 1,
                "filter_panels": list(presentation.FILTER_PANELS),
                "evidence_panels": list(presentation.EVIDENCE_PANELS),
            }
        )
        == []
    )
    assert presentation_problems({"version": 1, "filter_panels": []}) == []


@pytest.mark.parametrize("key", ["layout", "accent"])
@pytest.mark.parametrize("value", ["#ff0000", "Cose", ["blue"], 5, None, ""], ids=repr)
def test_a_closed_key_refuses_anything_else(key, value):
    domain = ", ".join(getattr(presentation, f"{key.upper()}S"))
    assert presentation_problems({"version": 1, key: value}) == [
        f"presentation.{key} is one of: {domain}."
    ]


LIST_KEYS = [
    ("filter_panels", lambda value: {"filter_panels": value}),
    ("evidence_panels", lambda value: {"evidence_panels": value}),
    ("default_layers", lambda value: {"default_layers": value}),
    ("default_filters.outcome", lambda value: {"default_filters": {"outcome": value}}),
    (
        "default_filters.evidence_basis",
        lambda value: {"default_filters": {"evidence_basis": value}},
    ),
    ("default_filters.effect", lambda value: {"default_filters": {"effect": value}}),
    ("default_filters.level", lambda value: {"default_filters": {"level": value}}),
]
# A valid name of each list, given twice for the duplicate case.
NAME_OF = {
    "filter_panels": "layers",
    "evidence_panels": "summary",
    "default_layers": "encodes",
    "default_filters.outcome": "supported",
    "default_filters.evidence_basis": "experimental",
    "default_filters.effect": "activating",
    "default_filters.level": "direct_interaction",
}


@pytest.mark.parametrize("key, shape", LIST_KEYS, ids=[key for key, _ in LIST_KEYS])
@pytest.mark.parametrize("value", [{}, 5, "layers", [1], [[]], [{}], [None]], ids=repr)
def test_a_list_key_refuses_anything_but_a_list_of_names(key, shape, value):
    # The type is checked first, so nothing hashes an unhashable item.
    assert presentation_problems({"version": 1, **shape(value)}) == [
        f"presentation.{key} is a list of names."
    ]


@pytest.mark.parametrize("key, shape", LIST_KEYS, ids=[key for key, _ in LIST_KEYS])
def test_a_list_key_names_known_values_each_once(key, shape, registry):
    name = NAME_OF[key]
    assert presentation_problems({"version": 1, **shape([name])}) == []
    assert presentation_problems({"version": 1, **shape([name, name])}) == [
        f"presentation.{key} names each value once."
    ]
    unknown = f"presentation.{key} names 'nope', which is not one of its values."
    assert presentation_problems({"version": 1, **shape(["nope", name])}) == [unknown]
    # A name given twice is named once.
    assert presentation_problems({"version": 1, **shape(["nope", "nope"])}) == [
        unknown,
        f"presentation.{key} names each value once.",
    ]


def test_default_filters_is_an_object_of_the_network_s_filters():
    for value in ([], 5, "direction"):
        assert presentation_problems({"version": 1, "default_filters": value}) == [
            presentation.FILTERS_NOT_OBJECT
        ]
    assert presentation_problems(
        {"version": 1, "default_filters": {"seeds": "x", "direction": "out"}}
    ) == ["presentation.default_filters has no key 'seeds'."]
    valid = {
        "direction": "both",
        "outcome": ["supported", "tested_absent"],
        "evidence_basis": ["experimental", "computational_prediction"],
        "effect": ["activating", "inhibiting", "unknown", "not_stated"],
        "level": ["direct_interaction", "not_stated"],
    }
    assert presentation_problems({"version": 1, "default_filters": valid}) == []


@pytest.mark.parametrize("direction", ["out", "in", "both"])
def test_direction_is_one_of_the_network_s(direction):
    assert (
        presentation_problems(
            {"version": 1, "default_filters": {"direction": direction}}
        )
        == []
    )


@pytest.mark.parametrize("direction", ["up", "OUT", ["out"], 1, None])
def test_another_direction_is_refused(direction):
    assert presentation_problems(
        {"version": 1, "default_filters": {"direction": direction}}
    ) == ["presentation.default_filters.direction is one of: out, in, both."]


def test_outcome_all_stands_alone():
    def outcome(value):
        return presentation_problems(
            {"version": 1, "default_filters": {"outcome": value}}
        )

    assert outcome(["all"]) == []
    assert outcome(["all", "supported"]) == [presentation.OUTCOME_ALL_ALONE]
    assert outcome(["all", "all"]) == [
        presentation.OUTCOME_ALL_ALONE,
        "presentation.default_filters.outcome names each value once.",
    ]


def test_effect_is_the_network_s_effect_domain():
    # not_stated is a network filter value; not_applicable is a summary's.
    assert (
        presentation_problems(
            {"version": 1, "default_filters": {"effect": ["not_stated"]}}
        )
        == []
    )
    assert presentation_problems(
        {"version": 1, "default_filters": {"effect": ["not_applicable"]}}
    ) == [
        "presentation.default_filters.effect names 'not_applicable', which is not "
        "one of its values."
    ]


def test_default_layers_name_registered_types_and_their_families(registry):
    assert (
        presentation_problems(
            {
                "version": 1,
                "default_layers": [
                    "transcriptional_regulation",
                    "family:physical",
                    "family:reference",
                ],
            }
        )
        == []
    )
    assert presentation_problems(
        {"version": 1, "default_layers": ["family:genetic", "family:nope"]}
    ) == [
        "presentation.default_layers names 'family:genetic', which is not one of its "
        "values.",
        "presentation.default_layers names 'family:nope', which is not one of its "
        "values.",
    ]


def test_default_layers_name_at_most_20_layers():
    layers = [RelationshipTypeFactory().slug for _ in range(21)]
    assert presentation_problems({"version": 1, "default_layers": layers[:20]}) == []
    assert presentation_problems({"version": 1, "default_layers": layers}) == [
        "presentation.default_layers names at most 20 layers."
    ]


def test_node_label_namespace_is_a_registered_namespace_or_null(registry):
    assert presentation_problems({"version": 1, "node_label_namespace": "hgnc"}) == []
    assert presentation_problems({"version": 1, "node_label_namespace": None}) == []
    sentence = (
        "presentation.node_label_namespace is one of: ensembl_gene, hgnc, ncbigene, "
        "synthetic_gene, synthetic_protein, uniprotkb."
    )
    for value in ("nope", "HGNC", ["hgnc"], 5):
        assert presentation_problems({"version": 1, "node_label_namespace": value}) == [
            sentence
        ], value


def test_a_nul_in_a_value_is_refused_and_never_reaches_a_filter(registry):
    value = {
        "version": 1,
        "default_layers": ["a\x00", "encodes"],
        "node_label_namespace": "hgnc\x00",
        "layout": "cose\x00",
        "default_filters": {"outcome": ["supported\x00"]},
    }
    with CaptureQueriesContext(connection) as captured:
        found = presentation_problems(value)
    assert found == [
        "presentation.default_layers names 'a\x00', which is not one of its values.",
        "presentation.default_filters.outcome names 'supported\x00', which is not one "
        "of its values.",
        "presentation.layout is one of: "
        "cose, cola, concentric, circle, grid, breadthfirst.",
        "presentation.node_label_namespace is one of: ensembl_gene, hgnc, ncbigene, "
        "synthetic_gene, synthetic_protein, uniprotkb.",
    ]
    # One read of each registry, by values_list, filtered by nothing.
    sql = [query["sql"] for query in captured.captured_queries]
    assert len(sql) == 2
    assert all("WHERE" not in statement for statement in sql)


def test_a_registry_is_read_only_when_its_key_needs_it(django_assert_num_queries):
    with django_assert_num_queries(0):
        validate_presentation(
            {
                "version": 1,
                "layout": "grid",
                "accent": "red",
                "default_layers": [],
                "node_label_namespace": None,
            }
        )
        presentation_problems({"version": 1, "default_layers": 5})


# ── The facets ──────────────────────────────────────────────────────────────


def facet_problems(**facets) -> dict:
    try:
        validate_facets(
            **{"taxa": None, "primary_taxon": None, "assays": None, **facets}
        )
    except ValidationError as error:
        return error.message_dict
    return {}


def test_the_primary_taxon_is_one_of_the_taxa_when_both_are_given():
    human, fly, worm = taxon_of(9606), taxon_of(7227), taxon_of(999_999_931)
    assert facet_problems(taxa=[human, fly], primary_taxon=fly) == {}
    assert facet_problems(taxa=Taxon.objects.filter(pk=9606), primary_taxon=human) == {}
    assert facet_problems(taxa=[human, fly], primary_taxon=worm) == {
        "primary_taxon": [rules.PRIMARY_TAXON_OUTSIDE_TAXA]
    }
    # No taxa stated: any primary taxon, and none at all.
    assert facet_problems(taxa=[], primary_taxon=worm) == {}
    assert facet_problems(taxa=None, primary_taxon=worm) == {}
    assert facet_problems(taxa=[human]) == {}


def test_every_assay_is_a_psi_mi_term():
    two_hybrid = TermFactory(curie="MI:0018", label="two hybrid")
    test_term = TermFactory()
    evidence_code = TermFactory(curie="ECO:0000353", label="physical interaction")
    assert facet_problems(assays=[two_hybrid, test_term]) == {}
    assert facet_problems(assays=[two_hybrid, evidence_code]) == {
        "assays": [rules.ASSAY_NOT_PSI_MI]
    }


def test_both_facet_rules_are_keyed_to_their_fields():
    assert facet_problems(
        taxa=[taxon_of(9606)],
        primary_taxon=taxon_of(7227),
        assays=[TermFactory(curie="ECO:0000353", label="physical interaction")],
    ) == {
        "primary_taxon": [rules.PRIMARY_TAXON_OUTSIDE_TAXA],
        "assays": [rules.ASSAY_NOT_PSI_MI],
    }


# ── The selection ───────────────────────────────────────────────────────────


def selection_problems(instance, releases) -> list:
    try:
        validate_selection(instance, releases)
    except ValidationError as error:
        # A list, never a dict: a formset shows it as its own errors.
        assert not hasattr(error, "error_dict")
        return error.messages
    return []


def unsaved(**fields) -> BrowserInstance:
    return BrowserInstance(
        **{"slug": "unsaved", "title": "Unsaved", "kind": Kind.TYPED, **fields}
    )


def test_an_available_typed_instance_selects_at_least_one_release():
    release = DatasetReleaseFactory()
    assert selection_problems(unsaved(status=Status.AVAILABLE), []) == [
        rules.AVAILABLE_TYPED_NEEDS_RELEASE
    ]
    assert selection_problems(unsaved(status=Status.AVAILABLE), [release]) == []
    for status in (Status.PLANNED, Status.RETIRED):
        assert selection_problems(unsaved(status=status), []) == [], status


@pytest.mark.parametrize("kind", [Kind.PORTAL_PPI, Kind.EXTERNAL, Kind.METADATA_ONLY])
def test_only_a_typed_instance_selects_releases(kind):
    for status in Status.values:
        instance = unsaved(kind=kind, status=status)
        assert selection_problems(instance, []) == [], status
        assert selection_problems(instance, [1]) == [rules.ONLY_TYPED_SELECTS], status


def test_an_instance_selects_at_most_50_releases():
    instance = unsaved(status=Status.AVAILABLE)
    assert rules.MAX_INSTANCE_RELEASES == 50
    assert selection_problems(instance, range(1, 51)) == []
    assert selection_problems(instance, range(1, 52)) == [
        "An instance selects at most 50 releases."
    ]
    assert selection_problems(unsaved(kind=Kind.EXTERNAL), range(1, 52)) == [
        rules.ONLY_TYPED_SELECTS,
        rules.TOO_MANY_RELEASES,
    ]


def test_an_install_has_one_portal_ppi_entry(django_assert_num_queries):
    first = make(slug="first-entry", kind=Kind.PORTAL_PPI, display_order=5)
    make(slug="another-entry", kind=Kind.PORTAL_PPI, display_order=9)
    second = unsaved(slug="second-entry", kind=Kind.PORTAL_PPI)
    with django_assert_num_queries(1):
        assert selection_problems(second, []) == [
            "This portal already has a portal_ppi entry, first-entry."
        ]
    # An entry saved already does not count against itself.
    BrowserInstance.objects.filter(slug="another-entry").delete()
    assert selection_problems(first, []) == []
    # No other kind reads the other rows.
    with django_assert_num_queries(0):
        assert selection_problems(unsaved(status=Status.AVAILABLE), [1]) == []


# ── Who lists, reaches and pins an instance ─────────────────────────────────


def caller(user=None):
    request = RequestFactory().get("/")
    request.user = user or AnonymousUser()
    return request


def test_staff_list_and_reach_every_instance_and_visitors_fewer():
    for visibility in Visibility.values:
        make(slug=visibility, visibility=visibility)

    def slugs(rule, request):
        return sorted(
            BrowserInstance.objects.filter(rule(request)).values_list("slug", flat=True)
        )

    visitor, member = caller(), caller(StaffUserFactory(is_staff=False))
    staff = caller(StaffUserFactory())
    inactive = caller(StaffUserFactory(is_active=False))
    for request in (visitor, member, inactive):
        assert slugs(listable, request) == ["public"]
        assert slugs(reachable, request) == ["public", "unlisted"]
    assert slugs(listable, staff) == ["public", "staff", "unlisted"]
    assert slugs(reachable, staff) == ["public", "staff", "unlisted"]


def test_a_reachable_typed_instance_pins_every_release_it_selects(
    django_assert_num_queries,
):
    primary, context = DatasetReleaseFactory(), DatasetReleaseFactory()
    for status in Status.values:
        instance = make(slug=f"typed-{status}", status=status, visibility="public")
        BrowserInstanceRelease.objects.create(instance=instance, release=primary)
        BrowserInstanceRelease.objects.create(
            instance=instance, release=context, role=Role.CONTEXT
        )
        # Whatever its stored status: the release gate decides what is seen.
        with django_assert_num_queries(1):
            pinned = instance_releases(instance.slug, caller())
        assert pinned == {primary.pk, context.pk}, status


def test_whatever_pins_nothing_pins_the_empty_pin_in_one_statement(
    django_assert_num_queries,
):
    release = DatasetReleaseFactory()
    hidden = make(slug="hidden", visibility=Visibility.STAFF)
    BrowserInstanceRelease.objects.create(instance=hidden, release=release)
    # No other kind may select releases; a bare save shows they would not pin.
    card = make(slug="card", kind=Kind.METADATA_ONLY, visibility=Visibility.PUBLIC)
    BrowserInstanceRelease.objects.create(instance=card, release=release)
    make(slug="empty", visibility=Visibility.PUBLIC)
    assert EMPTY_PIN == frozenset({0})
    for slug in ("no-such-instance", "hidden", "card", "empty"):
        with django_assert_num_queries(1):
            assert instance_releases(slug, caller()) == EMPTY_PIN, slug
    staff = caller(StaffUserFactory())
    with django_assert_num_queries(1):
        assert instance_releases("hidden", staff) == {release.pk}


def test_every_sentence_is_the_specification_s():
    assert rules.SLUG_GRAMMAR == (
        "A slug is lower-case letters and digits in groups joined by single "
        "hyphens, such as synthetic-regulation."
    )
    assert {
        name: getattr(rules, name)
        for name in (
            "SLUG_RESERVED",
            "CONTROL_CHARACTER",
            "REORDERING_CHARACTER",
            "TEXT_TOO_LONG",
            "NOT_HTTPS",
            "URL_CREDENTIALS",
            "URL_HOST_NOT_ASCII",
            "URL_CHARACTER",
            "TAGS_NOT_LIST",
            "TAGS_NOT_NAMES",
            "UNKNOWN_TAG",
            "REPEATED_TAG",
            "TOO_MANY_TAGS",
            "EXTERNAL_NEEDS_URL",
            "ONLY_EXTERNAL_HAS_URL",
            "PRIMARY_TAXON_OUTSIDE_TAXA",
            "ASSAY_NOT_PSI_MI",
            "AVAILABLE_TYPED_NEEDS_RELEASE",
            "ONLY_TYPED_SELECTS",
            "TOO_MANY_RELEASES",
            "SECOND_PORTAL_PPI",
        )
    } == {
        "SLUG_RESERVED": "'{slug}' is reserved.",
        "CONTROL_CHARACTER": "This text holds a control character.",
        "REORDERING_CHARACTER": (
            "This text holds a character that reorders or breaks it."
        ),
        "TEXT_TOO_LONG": "This text is at most 20,000 characters.",
        "NOT_HTTPS": "An external URL is an https URL.",
        "URL_CREDENTIALS": "An external URL holds no user name or password.",
        "URL_HOST_NOT_ASCII": "Write the host in its ASCII (xn--) form.",
        "URL_CHARACTER": "An external URL holds no control or formatting character.",
        "TAGS_NOT_LIST": "tags is a list of tag names.",
        "TAGS_NOT_NAMES": "tags holds tag names only.",
        "UNKNOWN_TAG": "Unknown tag '{tag}'.",
        "REPEATED_TAG": "tags names each tag once.",
        "TOO_MANY_TAGS": "tags names at most 10 tags.",
        "EXTERNAL_NEEDS_URL": "An external instance needs an https URL.",
        "ONLY_EXTERNAL_HAS_URL": "Only an external instance has a URL.",
        "PRIMARY_TAXON_OUTSIDE_TAXA": "The primary taxon is one of the taxa.",
        "ASSAY_NOT_PSI_MI": "An assay is a PSI-MI term, such as MI:0018.",
        "AVAILABLE_TYPED_NEEDS_RELEASE": (
            "An available typed instance selects at least one release."
        ),
        "ONLY_TYPED_SELECTS": "Only a typed instance selects releases.",
        "TOO_MANY_RELEASES": "An instance selects at most 50 releases.",
        "SECOND_PORTAL_PPI": "This portal already has a portal_ppi entry, {slug}.",
    }
    assert {
        name: getattr(presentation, name)
        for name in (
            "NOT_OBJECT",
            "NOT_VERSION_1",
            "UNKNOWN_KEY",
            "NOT_A_LIST",
            "UNKNOWN_VALUE",
            "REPEATED_VALUE",
            "TOO_MANY_LAYERS",
            "FILTERS_NOT_OBJECT",
            "UNKNOWN_FILTER",
            "DIRECTION_NOT_ONE",
            "OUTCOME_ALL_ALONE",
            "NOT_ONE_OF",
            "TOO_LONG",
            "NOT_JSON",
        )
    } == {
        "NOT_OBJECT": "presentation is an object.",
        "NOT_VERSION_1": "presentation.version is the number 1.",
        "UNKNOWN_KEY": "presentation has no key '{key}'.",
        "NOT_A_LIST": "presentation.{key} is a list of names.",
        "UNKNOWN_VALUE": (
            "presentation.{key} names '{value}', which is not one of its values."
        ),
        "REPEATED_VALUE": "presentation.{key} names each value once.",
        "TOO_MANY_LAYERS": "presentation.default_layers names at most 20 layers.",
        "FILTERS_NOT_OBJECT": "presentation.default_filters is an object.",
        "UNKNOWN_FILTER": "presentation.default_filters has no key '{key}'.",
        "DIRECTION_NOT_ONE": (
            "presentation.default_filters.direction is one of: out, in, both."
        ),
        "OUTCOME_ALL_ALONE": (
            "presentation.default_filters.outcome is all alone or a list of outcomes."
        ),
        "NOT_ONE_OF": "presentation.{key} is one of: {values}.",
        "TOO_LONG": "presentation is at most 8,192 characters.",
        "NOT_JSON": "presentation is not valid JSON.",
    }


def test_the_lists_and_caps_are_the_specification_s():
    assert rules.RESERVED_SLUGS == (
        "new",
        "edit",
        "admin",
        "api",
        "all",
        "search",
        "explore",
        "static",
        "media",
        "settings",
    )
    assert rules.TAGS == (
        "protein_interaction",
        "gene_regulation",
        "rna_regulation",
        "genetic_interaction",
        "signaling",
        "ligand_receptor",
        "protein_modification",
        "disease",
        "host_pathogen",
        "phenotype",
    )
    assert (rules.MAX_SLUG_CHARS, rules.MAX_TAGS, rules.MAX_LONG_TEXT_CHARS) == (
        50,
        10,
        20_000,
    )
    assert presentation.KEYS == (
        "version",
        "default_layers",
        "default_filters",
        "filter_panels",
        "layout",
        "evidence_panels",
        "node_label_namespace",
        "accent",
    )
    assert presentation.MAX_PRESENTATION_CHARS == 8192

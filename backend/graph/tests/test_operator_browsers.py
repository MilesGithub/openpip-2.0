"""The operator guide's page on browser instances (docs/operator-guide/
browser-instances.md) against the code it describes: its headings, the tags,
the reserved words, the presentation schema, the availability tables, every
figure and form sentence it states, and the rows the demonstration's loader
records.

It reads the page with test_operator_guide's helpers. Like those tests, these
need the full checkout: they skip without docs/ locally, and fail without it
in CI."""

import re

import pytest

from browsers.availability import compute
from browsers.demo import DEMO_SLUG, PORTAL_PPI_SLUG
from browsers.presentation import (
    ACCENTS,
    DEFAULT_FILTER_NAMES,
    EVIDENCE_PANELS,
    FILTER_PANELS,
    KEYS,
    LAYOUTS,
    MAX_PRESENTATION_CHARS,
    NOT_JSON,
    NOT_OBJECT,
    TOO_LONG,
)
from browsers.rules import (
    ASSAY_NOT_PSI_MI,
    AVAILABLE_TYPED_NEEDS_RELEASE,
    CONTROL_CHARACTER,
    EXTERNAL_NEEDS_URL,
    MAX_INSTANCE_RELEASES,
    MAX_LONG_TEXT_CHARS,
    MAX_SLUG_CHARS,
    MAX_TAGS,
    NOT_HTTPS,
    ONLY_EXTERNAL_HAS_URL,
    ONLY_TYPED_SELECTS,
    PRIMARY_TAXON_OUTSIDE_TAXA,
    REORDERING_CHARACTER,
    RESERVED_SLUGS,
    SECOND_PORTAL_PPI,
    SLUG_GRAMMAR,
    SLUG_RESERVED,
    TAGS,
    TEXT_TOO_LONG,
    TOO_MANY_RELEASES,
    URL_CHARACTER,
    URL_CREDENTIALS,
    URL_HOST_NOT_ASCII,
)
from graph.api.limits import MAX_LAYERS
from graph.tests import test_operator_guide as guide

PAGE = guide.PAGE.parent / "browser-instances.md"
pytestmark = guide.pytestmark

HEADINGS = [
    "## Kinds",
    "## Status and availability",
    "## Visibility",
    "## Creating an instance",
    "### The fields",
    "### The citation and the selected releases",
    "### What an editor needs",
    "### What is refused",
    "## Presentation",
    "## Tags",
    "## Reserved slugs",
    "## External URLs",
    "## Cards with no releases",
    "## The synthetic flag",
    "## Retiring an instance",
    "## The demonstration's rows",
    "## Rolling back",
]
# Which lists each cell of a presentation table names, in the tuple's order.
PRESENTATION_LISTS = {
    "default_filters": DEFAULT_FILTER_NAMES,
    "filter_panels": FILTER_PANELS,
    "layout": LAYOUTS,
    "evidence_panels": EVIDENCE_PANELS,
    "accent": ACCENTS,
}
# Each row of the availability tables, on this page and the developer page: a
# selected release's status, with the ids the release gate issues a visitor
# and a staff account for it (graph.visibility, pinned to the selection).
PUBLISHED, SUPERSEDED = "published", "superseded"
RELEASE = 1
SEES = {RELEASE}
SELECTED_RELEASES = {
    "Published, with `internal` and `public_view` in effect": (
        [(RELEASE, PUBLISHED)],
        SEES,
        SEES,
    ),
    "Published, with `internal` only": ([(RELEASE, PUBLISHED)], set(), SEES),
    "Published under embargo": ([(RELEASE, PUBLISHED)], set(), SEES),
    "Superseded, with both in effect": ([(RELEASE, SUPERSEDED)], SEES, SEES),
    "Staged": ([(RELEASE, "staged")], set(), SEES),
    "Draft, withdrawn or discarded": ([(RELEASE, "withdrawn")], set(), set()),
    "`internal` revoked": ([(RELEASE, PUBLISHED)], set(), set()),
    "`public_view` revoked": ([(RELEASE, PUBLISHED)], set(), SEES),
    "Due diligence reopened, on real data": ([(RELEASE, PUBLISHED)], set(), set()),
    "Two releases, one hidden from you": (
        [(RELEASE, PUBLISHED), (2, PUBLISHED)],
        SEES,
        SEES,
    ),
    "None selected": ([], set(), set()),
}


def assert_availability_tables(found):
    """The two availability tables among a section's rows, against compute():
    the stored status and kind first, then a typed instance by its selected
    release, for each caller."""
    header = next(
        cells for cells in found if cells[:2] == ["Stored `status`", "`kind`"]
    )
    start = found.index(header)
    stored = {}
    for cells in found[start + 1 :]:
        if not cells[0].startswith("`"):
            break
        stored[(guide.code(cells[0]), guide.code(cells[1]))] = cells[2]
    typed = [(1, PUBLISHED)]
    for (status, kind), cell in stored.items():
        if cell.startswith("By its selected"):
            assert (status, kind) == ("available", "typed")
            continue
        chosen = typed if kind == "typed" else []
        for stated in ("typed", "portal_ppi", "external", "metadata_only"):
            if kind == "any" or stated == kind:
                assert compute(stated, status, chosen, set()) == guide.code(cell)
    assert set(stored) == {
        ("retired", "any"),
        ("planned", "any"),
        ("available", "external"),
        ("available", "portal_ppi"),
        ("available", "metadata_only"),
        ("available", "typed"),
    }
    start = found.index(["Selected release", "Visitor", "Staff"])
    rows = {}
    for cells in found[start + 1 :]:
        if len(cells) != 3:
            break
        rows[cells[0]] = cells[1:]
    assert set(rows) == set(SELECTED_RELEASES)
    for name, (visitor, staff) in rows.items():
        selected, visitor_sees, staff_sees = SELECTED_RELEASES[name]
        assert [
            compute("typed", "available", selected, visitor_sees),
            compute("typed", "available", selected, staff_sees),
        ] == [guide.code(visitor), guide.code(staff)], name


def test_the_page_has_its_headings():
    text = PAGE.read_text(encoding="utf-8")
    assert re.findall(r"^#{2,3} .+$", text, re.MULTILINE) == HEADINGS


def test_the_tags_are_the_codes():
    table = guide.rows(guide.section("## Tags", page=PAGE))
    assert table[0] == ["Tag", "The biology area"]
    assert [guide.code(cells[0]) for cells in table[1:]] == list(TAGS)


def test_the_reserved_words_are_the_codes():
    words = re.findall(r"`([a-z]+)`", guide.section("## Reserved slugs", page=PAGE))
    assert words == list(RESERVED_SLUGS)


def test_the_presentation_table_is_the_schema():
    table = guide.rows(guide.section("## Presentation", page=PAGE))
    assert table[0] == ["Key", "What it holds"]
    rows = {guide.code(cells[0]): cells[1] for cells in table[1:]}
    assert list(rows) == list(KEYS)
    for key, names in PRESENTATION_LISTS.items():
        assert re.findall(r"`([^`]+)`", rows[key]) == list(names), key


def test_the_availability_tables_are_compute():
    assert_availability_tables(
        guide.rows(guide.section("## Status and availability", page=PAGE))
    )


def test_each_figure_is_its_constant():
    text = " ".join(PAGE.read_text(encoding="utf-8").split())
    for stated in (
        f"At most {MAX_TAGS} of the [tags]",
        f"names at most {MAX_TAGS} tags",
        f"single hyphens, at most {MAX_SLUG_CHARS} characters",
        f"An instance selects at most {MAX_INSTANCE_RELEASES} releases.",
        f"at most {MAX_LONG_TEXT_CHARS:,} characters each",
        f"At most {MAX_LAYERS} distinct names",
        f"The form holds at most {MAX_PRESENTATION_CHARS:,} characters of it.",
    ):
        assert stated in text, stated


def test_each_sentence_the_form_shows_is_on_the_page():
    table = guide.rows(guide.section("### What is refused", page=PAGE))
    assert table[0] == ["The form says", "Because"]
    assert {cells[0] for cells in table[1:]} == {
        SLUG_GRAMMAR,
        SLUG_RESERVED.format(slug="new"),
        CONTROL_CHARACTER,
        REORDERING_CHARACTER,
        TEXT_TOO_LONG,
        EXTERNAL_NEEDS_URL,
        ONLY_EXTERNAL_HAS_URL,
        NOT_HTTPS,
        URL_CREDENTIALS,
        URL_HOST_NOT_ASCII,
        URL_CHARACTER,
        PRIMARY_TAXON_OUTSIDE_TAXA,
        ASSAY_NOT_PSI_MI,
        AVAILABLE_TYPED_NEEDS_RELEASE,
        ONLY_TYPED_SELECTS,
        TOO_MANY_RELEASES,
        SECOND_PORTAL_PPI.format(slug=PORTAL_PPI_SLUG),
        TOO_LONG,
        NOT_JSON,
        NOT_OBJECT,
    }


def test_the_demonstrations_rows_are_the_loaders():
    found = " ".join(guide.section("## The demonstration's rows", page=PAGE).split())
    for stated in (
        f"- `{DEMO_SLUG}`, of kind `typed`",
        f"a new one with the slug `{PORTAL_PPI_SLUG}`.",
        f"when `{DEMO_SLUG}` is an instance of another kind",
        f"and `{PORTAL_PPI_SLUG}` is an instance of another kind",
        f"leaves `{DEMO_SLUG}` selecting",
    ):
        assert stated in found, stated


@pytest.mark.parametrize(
    "sentence",
    [
        "An instance's `visibility` and `status` decide who sees the instance, not "
        "its data.",
        "Its releases stay readable by everyone the release gate admits, through "
        "`releases=` and, for a reachable instance, through `browser=`.",
        "To hide data, withdraw the release or revoke its approval.",
        "`unlisted` keeps an instance out of the gallery, but anyone who knows or "
        "guesses its slug can open it.",
        "Header publications and an external URL are shown as stored to everyone "
        "who may see the instance.",
        "`graph.view_taxon` for `taxa` and `primary_taxon`, `graph.view_term` for "
        "`assays`, and `graph.view_publication` for the header citation.",
    ],
)
def test_the_page_says_what_an_operator_must_know(sentence):
    assert sentence in " ".join(PAGE.read_text(encoding="utf-8").split())

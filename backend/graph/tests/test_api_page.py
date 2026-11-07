"""The developer guide's page on the typed graph (docs/developer/api/
typed-graph.md) against the routes it describes: its headings, each route's
parameter and key tables, what it states of caps, enumerations and rates, and
its examples, which are the responses on the synthetic demonstration, up to
ids, as the page's ids are those of one load.

It reads the pages with test_operator_guide's helpers. Like those tests, these
need the full checkout: they skip without docs/ locally, and fail without it
in CI.
"""

import json
import re
from urllib.parse import quote

import pytest

from graph.api import limits
from graph.api.params import MAX_PROBLEMS
from graph.api.serializers import (
    CandidateSerializer,
    EntityNodeSerializer,
    EntityQuerySerializer,
    EntitySerializer,
    FeaturesSerializer,
    LimitsSerializer,
    ParameterErrorSerializer,
    ProblemSerializer,
    RegistryVersionSerializer,
    ReleaseEntrySerializer,
    ResolveQuerySerializer,
    ResolveResultSerializer,
    VocabulariesSerializer,
)
from graph.api.views import ResolveView
from graph.models import ApprovalScope, EntityStatus
from graph.resolution import AMBIGUOUS, CANDIDATE, MATCH_KINDS, RESOLVED, UNMATCHED
from graph.tests import test_operator_guide as guide
from graph.tests.api_helpers import (
    BASE,
    ENTITY,
    RESOLVE,
    STATUS,
    VOCABULARIES,
    entity_url,
    typed_routes,
    without_ids,
)

PAGE = guide.PAGE.parent.parent / "developer" / "api" / "typed-graph.md"
INDEX = PAGE.parent / "index.md"
CONFIGURATION = guide.PAGE.parent / "configuration.md"
REPOSITORY = PAGE.parents[3]
# Where each shipped server could be given a limit on a request line other
# than its default, and the word that would do it.
SERVER_SETTINGS = (
    (REPOSITORY / "docker-compose.prod.yml", "--limit-request-line"),
    (REPOSITORY / "backend" / "Dockerfile", "--limit-request-line"),
    (REPOSITORY / "frontend" / "nginx.conf", "client_header_buffer"),
)
# gunicorn reads this file from its working directory with no flag. No such
# file ships yet.
GUNICORN_CONF = REPOSITORY / "backend" / "gunicorn.conf.py"
pytestmark = guide.pytestmark

ROUTES = list(typed_routes())
ROUTE_IDS = [f"{method} {path}" for method, path, _ in ROUTES]
HEADINGS = [
    "## Conventions",
    "### Ids",
    "### Selecting releases",
    "### What you may see",
    "### Entity nodes",
    "### Release entries",
    "## Errors",
    "## Rate limits",
    "## Status",
    f"### GET {STATUS}",
    "## Vocabularies",
    f"### GET {VOCABULARIES}",
    "## Resolving identifiers",
    f"### GET {RESOLVE}",
    "## Entities",
    f"### GET {ENTITY}",
]
# The examples' requests, as the page states them.
RESOLVE_EXAMPLE = f"{RESOLVE}?q=SYN-TF1,SYN-SHARED"
ERROR_EXAMPLE = f"{RESOLVE}?q=x&foo=1&entity_type=chemical"
# Each throttle scope a route uses: its row of the overview's rate table, and
# its words in the configuration reference's row of rates.
RATE_ROWS = {
    "graph_read": "Typed graph reads, per IP address, or per account when logged in",
    "graph_resolve": (
        "Typed graph `resolve`, per IP address, or per account when logged in"
    ),
}
RATE_WORDS = {
    "graph_read": "typed graph reads",
    "graph_resolve": "typed graph `resolve`",
}
UNITS = {"min": "minute", "hour": "hour"}
# The rows of a route's key table that name a key of a list, by their prefix.
NESTED = {
    RESOLVE: {
        "results[].": ResolveResultSerializer,
        "candidates[].": CandidateSerializer,
    },
}


def section(heading) -> str:
    return guide.section(heading, page=PAGE)


def examples(heading) -> list:
    """The JSON examples of a section, in order. guide.json_block raises when
    two blocks hold a key."""
    return [
        json.loads(block)
        for block in re.findall(r"```json\n(.*?)\n```", section(heading), re.DOTALL)
    ]


def named(cell) -> list:
    """The names a table cell gives, each in backticks; [] for a cell that is
    anything else, as a header row's is."""
    if re.fullmatch(r"`[^`]+`(, `[^`]+`)*", cell) is None:
        return []
    return [guide.code(name) for name in cell.split(", ")]


def table(heading, header) -> dict:
    """The second cell of each row of the table under `heading` with this
    header row, by the names its first cell gives. guide.rows merges every
    table under a heading, so this one ends at the next row that names
    nothing, a header row; a `none` row names nothing and is left out."""
    rows = guide.rows(section(heading))
    found = {}
    for cells in rows[rows.index(header) + 1 :]:
        if cells[0] == "none":
            continue
        if not named(cells[0]):
            break
        for name in named(cells[0]):
            found[name] = cells[1]
    return found


def up_to_ids(body) -> str:
    return without_ids(json.dumps(body))


def request_line(terms, chars, letter="a") -> int:
    """How many bytes the request line takes for so many terms of so many
    characters, percent-encoded and joined by plain commas."""
    query = ",".join([quote(letter * chars)] * terms)
    return len(f"GET {RESOLVE}?q={query} HTTP/1.1")


# ── The page's structure ────────────────────────────────────────────────────


def test_the_page_has_its_headings():
    text = PAGE.read_text(encoding="utf-8")
    assert re.findall(r"^#{2,3} .+$", text, re.MULTILINE) == HEADINGS


def test_each_served_route_has_its_heading():
    headings = {f"### {method} {path}" for method, path, _ in ROUTES}
    assert headings == {line for line in HEADINGS if " GET " in line}


@pytest.mark.parametrize("method,path,view", ROUTES, ids=ROUTE_IDS)
def test_each_routes_parameter_table_lists_its_allowed_names(method, path, view):
    in_path = set(re.findall(r"\{(\w+)\}", path))
    found = table(f"### {method} {path}", ["Parameter", "Notes"])
    assert set(found) == set(view.allowed_params) | in_path


@pytest.mark.parametrize("method,path,view", ROUTES, ids=ROUTE_IDS)
def test_each_routes_key_table_lists_its_keys(method, path, view):
    found = table(f"### {method} {path}", ["Key", "Contents"])
    # A nested key's row names its list: results[].term.
    assert {key for key in found if "[" not in key} == set(
        view.serializer_class().fields
    )
    assert {key for key in found if "[" in key} == {
        f"{prefix}{name}"
        for prefix, serializer in NESTED.get(path, {}).items()
        for name in serializer().fields
    }


@pytest.mark.parametrize(
    "heading,header,serializer",
    (
        ("### Entity nodes", ["Key", "Contents"], EntityNodeSerializer),
        ("### Release entries", ["Key", "Contents"], ReleaseEntrySerializer),
        (f"### GET {STATUS}", ["`features` key", "Covers"], FeaturesSerializer),
        (f"### GET {STATUS}", ["`limits` key", "Contents"], LimitsSerializer),
    ),
)
def test_a_shared_shapes_table_lists_its_keys(heading, header, serializer):
    assert set(table(heading, header)) == set(serializer().fields)


@pytest.mark.parametrize(
    "heading,prefix,serializer,nullable",
    (
        (
            "### Entity nodes",
            "",
            EntityNodeSerializer,
            {"taxon", "status", "primary_identifier"},
        ),
        ("### Release entries", "", ReleaseEntrySerializer, {"changes_note"}),
        ("## Errors", "errors[].", ProblemSerializer, {"value", "allowed", "limit"}),
        (f"### GET {ENTITY}", "", EntitySerializer, {"created_by_release"}),
    ),
)
def test_a_table_says_which_values_may_be_null(heading, prefix, serializer, nullable):
    found = table(heading, ["Key", "Contents"])
    fields = serializer().fields
    assert {name for name, field in fields.items() if field.allow_null} == nullable
    for name in nullable:
        assert "`null`" in found[f"{prefix}{name}"], name


@pytest.mark.parametrize(
    "heading,key,serializer",
    (
        (f"### GET {STATUS}", "registry_version", RegistryVersionSerializer),
        (f"### GET {RESOLVE}", "query", ResolveQuerySerializer),
        (f"### GET {ENTITY}", "query", EntityQuerySerializer),
    ),
)
def test_a_row_says_which_of_its_keys_may_be_null(heading, key, serializer):
    clauses = re.split(r"(?<=\.) |; ", table(heading, ["Key", "Contents"])[key])
    for name, field in serializer().fields.items():
        if field.allow_null:
            assert any(name in part and "`null`" in part for part in clauses), name


def test_the_errors_table_lists_the_bodys_keys():
    found = table("## Errors", ["Key", "Contents"])
    assert {key for key in found if "[" not in key} == set(
        ParameterErrorSerializer().fields
    )
    assert {key.removeprefix("errors[].") for key in found if "[" in key} == set(
        ProblemSerializer().fields
    )


def test_the_vocabulary_table_names_every_field():
    found = table(f"### GET {VOCABULARIES}", ["Key", "Contents"])
    for key, field in VocabulariesSerializer().fields.items():
        for name in getattr(getattr(field, "child", None), "fields", ()):
            assert re.search(rf"\b{name}\b", found[key]), (key, name)


def test_the_enumerations_are_the_codes():
    rows = guide.rows(section(f"### GET {VOCABULARIES}"))
    stated = {cells[0]: re.findall(r"`(\w+)`", cells[1]) for cells in rows}
    assert stated["A term's `status`"] == [RESOLVED, CANDIDATE, AMBIGUOUS, UNMATCHED]
    assert stated["A match's `kind`"] == list(MATCH_KINDS)
    node = table("### Entity nodes", ["Key", "Contents"])["status"]
    assert all(f"`{value}`" in node for value in EntityStatus.values)
    entry = table("### Release entries", ["Key", "Contents"])["scopes"]
    assert re.findall(r"`(\w+)`", entry) == ApprovalScope.values


def test_the_caps_are_the_limits():
    text = " ".join(PAGE.read_text(encoding="utf-8").split())
    for stated in (
        f"or more than {limits.MAX_RELEASES} distinct refs",
        f"at most {limits.RESOLVE_MAX_TERMS} in all, "
        f"each at most {limits.Q_MAX_CHARS} characters",
        f"more than {limits.RESOLVE_MAX_TERMS} terms",
        f"`candidates` lists at most {limits.RESOLVE_MAX_CANDIDATES}",
        f"when more than {limits.RESOLVE_MAX_CANDIDATES} entities match, "
        f"and {limits.RESOLVE_MAX_CANDIDATES} are listed",
        f"At most {limits.RESOLVE_MAX_CANDIDATES}, each as below",
        f"One entry per problem, at most {MAX_PROBLEMS}.",
    ):
        assert stated in text
    pagination = " ".join(guide.section("## Pagination", page=INDEX).split())
    assert (
        f"`resolve` returns at most {limits.RESOLVE_MAX_CANDIDATES} candidates per term"
    ) in pagination


def test_the_page_says_what_a_client_must_know():
    text = " ".join(PAGE.read_text(encoding="utf-8").split())
    for sentence in (
        "`q` cannot name a label that holds a comma, tab or new line; use an "
        "identifier.",
        "A selection *replaces* the default set",
        'A client treats anything but 200 as "features unknown"',
        "Until a later release records which release states an alias, every "
        "stored identifier of a visible entity is listed.",
        "`relationship_counts` count relationships with supporting evidence in "
        "the selected releases.",
        "It gets the web server's own HTML error, with no `detail` or `errors`: "
        "gunicorn's 400 up to 8,190 bytes and nginx's 414 beyond that.",
    ):
        assert sentence in text


def test_the_request_line_figures_are_the_limits():
    # A limit the test client does not have. gunicorn imports only on POSIX,
    # so a local run elsewhere skips; CI always has it, so losing it fails there.
    if not guide.IN_CI:
        pytest.importorskip("gunicorn.config")
    from gunicorn.config import LimitRequestLine

    limit = LimitRequestLine.default
    text = " ".join(PAGE.read_text(encoding="utf-8").split())
    terms, chars = limits.RESOLVE_MAX_TERMS, limits.Q_MAX_CHARS
    most = max(n for n in range(1, terms + 1) if request_line(n, chars) <= limit)
    ascii_chars, greek, chinese = (
        max(n for n in range(1, chars + 1) if request_line(terms, n, letter) <= limit)
        for letter in "aα中"
    )
    for stated in (
        f"it holds {limit:,} bytes",
        f"holds {most} terms of {chars} ASCII characters, "
        f"or {terms} of {ascii_chars} joined by plain commas",
        f"{terms} terms of {greek} Greek or {chinese} Chinese characters",
    ):
        assert stated in text


def test_no_shipped_setting_moves_the_limits_on_the_page():
    # The page's figures are the servers' defaults, so they hold only while
    # nothing moves one. The test client would not notice.
    for path, word in SERVER_SETTINGS:
        assert word not in path.read_text(encoding="utf-8"), path.name
    if GUNICORN_CONF.exists():
        conf = GUNICORN_CONF.read_text(encoding="utf-8")
        assert "limit_request_line" not in conf


def test_the_page_describes_only_what_is_served():
    # A scope or route lands on the page with the code that uses it.
    text = PAGE.read_text(encoding="utf-8")
    for word in ("graph_network", "graph_export", "budget", "browser=", "/network"):
        assert word not in text


# ── The examples ────────────────────────────────────────────────────────────


def test_the_status_examples_are_the_responses(registry, settings, visitor_client):
    on, off = examples(f"### GET {STATUS}")
    assert on == visitor_client.get(STATUS).json()
    settings.TYPED_GRAPH_ENABLED = False
    assert off == visitor_client.get(STATUS).json()


def test_the_resolve_example_is_the_response(demo):
    assert f"`GET {RESOLVE_EXAMPLE}`" in section(f"### GET {RESOLVE}")
    block = json.loads(guide.json_block("results", page=PAGE))
    assert up_to_ids(block) == up_to_ids(demo.staff.get(RESOLVE_EXAMPLE).json())


def test_the_entity_example_is_the_response(demo):
    block = json.loads(guide.json_block("relationship_counts", page=PAGE))
    response = demo.staff.get(entity_url(demo.entity("SYN-TF1")))
    assert up_to_ids(block) == up_to_ids(response.json())
    request = f"`GET {BASE}entities/{block['query']['entity_id']}`"
    assert request in section(f"### GET {ENTITY}")


def test_the_vocabulary_example_is_the_encodes_entry(registry, visitor_client):
    block = json.loads(guide.json_block("relationship_types", page=PAGE))
    body = visitor_client.get(VOCABULARIES).json()
    assert set(block) == {"relationship_types", "outcomes"}
    [encodes] = block["relationship_types"]
    assert encodes["slug"] == "encodes"
    assert encodes in body["relationship_types"]
    assert block["outcomes"] == body["outcomes"]


def test_the_error_example_is_a_400_body(registry, visitor_client):
    assert f"`GET {ERROR_EXAMPLE}`" in section("## Errors")
    response = visitor_client.get(ERROR_EXAMPLE)
    assert response.status_code == 400
    block = json.loads(guide.json_block("errors", page=PAGE))
    assert block == response.json()
    # The sentence and the list keep the route's order.
    assert block["errors"][0]["allowed"] == list(ResolveView.allowed_params)
    assert ", ".join(ResolveView.allowed_params) in block["detail"]


# ── The rates ───────────────────────────────────────────────────────────────


def test_the_rate_tables_hold_the_settings_rates(db, settings, visitor_client):
    rates = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
    throttles = visitor_client.get(STATUS).json()["limits"]["throttles"]
    assert throttles == {scope: rates[scope] for scope in RATE_ROWS}
    overview = dict(guide.rows(guide.section("## Rate limits", page=INDEX))[1:])
    [configured] = [
        cells[1]
        for cells in guide.rows(CONFIGURATION.read_text(encoding="utf-8"))
        if cells[0] == "Rate limits"
    ]
    for scope, row in RATE_ROWS.items():
        number, unit = rates[scope].split("/")
        assert overview[row] == f"{int(number):,} requests per {UNITS[unit]}"
        assert f"{RATE_WORDS[scope]} {rates[scope]}" in configured


def test_the_pages_rate_section_links_to_the_table_and_states_no_rate():
    found = section("## Rate limits")
    assert "(index.md#rate-limits)" in found
    # 429 is the status.
    assert re.findall(r"\d[\d,]*", found) == ["429"]

"""The operator guide's page on typed data (docs/operator-guide/
typed-data-format.md) against the code it describes: its issue codes,
columns, interaction types, registry tables and limits, and its example
file, manifest, resolution file and summary, which a dry run reads and
prints as the page says. Its helpers and its example also serve
test_operator_releases, which reads the page on releases.

Like core's docs contract tests, these need the full checkout: they skip
without docs/ locally, and fail without it in CI."""

import json
import os
import re
from pathlib import Path

import pytest
from django.conf import settings

from graph.entities import create_entity
from graph.ingest import limits
from graph.ingest.canonical_tsv import SPEC
from graph.ingest.dry_run import dry_run, summary_lines
from graph.ingest.protocol import ISSUE_CODES
from graph.models import (
    IdentifierNamespace,
    RecordStatus,
    RelationshipType,
    SourceRecord,
)
from graph.terms import PHYSICAL_INTERACTION_LEVELS
from graph.tests.factories import StaffUserFactory
from graph.tests.ingest_helpers import registered, write

PAGE = (
    Path(settings.BASE_DIR).parent / "docs" / "operator-guide" / "typed-data-format.md"
)
IN_CI = os.environ.get("CI", "").strip().lower() in {"1", "true", "yes"}
pytestmark = pytest.mark.skipif(
    not PAGE.parent.is_dir() and not IN_CI, reason="needs the full repository checkout"
)


def section(heading, *, page=PAGE) -> str:
    """The page's text under a heading, to the next heading of its level or
    above; a fenced code block never ends it. The page is this one unless
    given (test_operator_releases reads typed-releases.md)."""
    lines = page.read_text(encoding="utf-8").splitlines()
    start = lines.index(heading)
    level = len(heading) - len(heading.lstrip("#"))
    fenced = False
    end = len(lines)
    for n in range(start + 1, len(lines)):
        if lines[n].startswith("```"):
            fenced = not fenced
        elif not fenced and re.match(rf"#{{1,{level}}} ", lines[n]):
            end = n
            break
    return "\n".join(lines[start + 1 : end])


def rows(text) -> list:
    """The cells of each table row in the text, the header row included,
    stripped; separator rows left out. An escaped pipe stays in its cell.
    It reads no page: give it the text of a section of either."""
    found = []
    for line in text.splitlines():
        if not line.startswith("|") or re.fullmatch(r"[|:\- ]+", line):
            continue
        found.append([cell.strip() for cell in re.split(r"(?<!\\)\|", line)[1:-1]])
    return found


def code(cell) -> str:
    return cell.strip("`")


def json_block(key, *, page=PAGE) -> str:
    """The page's one JSON example that has this key; the page is this one
    unless given."""
    text = page.read_text(encoding="utf-8")
    [found] = [
        block
        for block in re.findall(r"```json\n(.*?)\n```", text, re.DOTALL)
        if f'"{key}"' in block
    ]
    return found


# ── The page's tables ───────────────────────────────────────────────────────


def test_every_issue_code_is_listed_once():
    listed = [
        code(cells[0])
        for cells in rows(section("## Issue codes"))
        if cells[0] != "Code"
    ]
    assert len(listed) == len(set(listed))
    assert set(listed) == set(ISSUE_CODES)


def test_the_columns_are_the_formats():
    table = rows(section("### Columns"))
    assert table[0] == ["Column", "Required", "What it holds"]
    stated = {code(cells[0]): cells[1] for cells in table[1:]}
    assert len(stated) == len(table) - 1
    plain = {name: required for name, required in stated.items() if ":" not in name}
    assert plain == {
        **{column: "yes" for column in SPEC.columns},
        **{column: "no" for column in SPEC.optional_columns},
    }
    prefixed = {name: required for name, required in stated.items() if ":" in name}
    assert {name.split(":")[0] + ":" for name in prefixed} == set(SPEC.column_prefixes)
    assert set(prefixed.values()) == {"no"}


def test_the_interaction_types_are_the_formats_list():
    table = rows(section("### Interaction types"))[1:]
    assert {code(curie): code(level) for curie, _, level in table} == dict(
        SPEC.interaction_levels
    )
    for curie, name, _ in table:
        assert name == PHYSICAL_INTERACTION_LEVELS[code(curie)][1]


def test_the_registry_tables_are_the_registrys(registry):
    namespaces = [code(cells[0]) for cells in rows(section("### Identifiers"))[1:]]
    assert sorted(namespaces) == sorted(
        IdentifierNamespace.objects.values_list("slug", flat=True)
    )
    types = [code(cells[0]) for cells in rows(section("### Relationship types"))[1:]]
    assert sorted(types) == sorted(
        RelationshipType.objects.values_list("slug", flat=True)
    )


def test_the_limits_are_the_codes():
    values = {
        cells[0]: {int(n.replace(",", "")) for n in re.findall(r"\d[\d,]*", cells[1])}
        for cells in rows(section("### Limits"))[1:]
    }
    stated = {
        "A line, the header included, without its line ending": limits.MAX_LINE_BYTES,
        "Columns": limits.MAX_COLUMNS,
        "Characters in one value": limits.MAX_FIELD_CHARS,
        "Papers or methods in one cell": limits.MAX_LIST_VALUES,
        "Lines after the header, blank ones included": limits.MAX_DATA_LINES,
        "A data file": limits.MAX_FILE_BYTES,
        "A manifest or a resolution file": limits.MAX_JSON_BYTES,
        "Entries in a resolution file": limits.MAX_RESOLUTION_ENTRIES,
        "Distinct identifiers a file states": limits.MAX_IDENTIFIERS,
        "Free space the storage volume keeps after a file is stored": (
            limits.FREE_SPACE_MARGIN_BYTES // 1024**2
        ),
    }
    assert set(values) == set(stated)
    for name, limit in stated.items():
        assert limit in values[name], name
    report = section("### The report")
    assert f"at most {limits.REPORT_LIST_ENTRIES:,}" in report
    assert f"{limits.REPORT_BYTES // 1024**2} MiB budget" in report
    assert (
        f"at most {limits.REPORT_ENTRY_ISSUES} issues, "
        f"{limits.REPORT_ENTRY_CANDIDATES} candidates and its first "
        f"{limits.REPORT_ENTRY_LINES}"
    ) in " ".join(report.split())
    assert f"first {limits.RECORD_ISSUES} issues" in section("## Issue codes")


# ── The page's example ──────────────────────────────────────────────────────


def example_file() -> bytes:
    """The example: its header, then lines 2 to 4, from its table of one
    column per row."""
    table = rows(section("### An example"))
    header, body = table[0], table[1:]
    assert header == ["Column", "Line 2", "Line 3", "Line 4"]
    lines = ["\t".join(code(cells[0]) for cells in body)]
    for n in range(1, len(header)):
        lines.append("\t".join(code(cells[n]) for cells in body))
    return ("\n".join(lines) + "\n").encode()


@pytest.fixture
def example(registry, tmp_path):
    """The example registered with the example manifest, and approved, on a
    portal holding the two genes and two proteins it names; its TP53 gene."""
    tp53 = create_entity("gene", "TP53", 9606, "hgnc", "HGNC:11998")
    create_entity("gene", "MDM2", 9606, "hgnc", "HGNC:6973")
    create_entity("protein", "TP53", 9606, "uniprotkb", "P04637")
    create_entity("protein", "MDM2", 9606, "uniprotkb", "Q00987")
    release = registered(
        tmp_path, data=example_file(), manifest=json_block("publications").encode()
    )
    return release, tp53


def statuses(release) -> dict:
    return dict(
        SourceRecord.objects.filter(release=release).values_list("record_key", "status")
    )


def without_ids(lines) -> list:
    return [
        re.sub(r"Dry run \d+ of release \d+", "Dry run N of release N", line)
        for line in lines
    ]


def test_the_example_dry_runs_as_the_page_says(example):
    release, _ = example
    run = dry_run(release, triggered_by=StaffUserFactory())
    assert run.report["verdict"] == "ok"
    assert statuses(release) == {
        "lab-0001": RecordStatus.MAPPED,
        "lab-0002": RecordStatus.MAPPED,
        "lab-0003": RecordStatus.UNRESOLVED,
    }
    assert [
        issue["code"]
        for record in SourceRecord.objects.filter(release=release)
        for issue in record.issues
        if issue["severity"] == "error"
    ] == []
    [shown] = re.findall(r"```text\n(.*?)\n```", section("### The report"), re.DOTALL)
    assert without_ids(summary_lines(run)) == without_ids(shown.splitlines())


def test_the_example_resolution_file_maps_line_4(example, tmp_path):
    release, tp53 = example
    resolutions = json.loads(json_block("resolutions"))
    resolutions["resolutions"][0]["entity"] = tp53.pk
    path = write(tmp_path, "resolutions.json", json.dumps(resolutions).encode())
    dry_run(release, triggered_by=StaffUserFactory(), resolutions=path)
    assert set(statuses(release).values()) == {RecordStatus.MAPPED}
    record = SourceRecord.objects.get(release=release, record_key="lab-0003")
    assert [issue["code"] for issue in record.issues] == ["curated_mapping"]
